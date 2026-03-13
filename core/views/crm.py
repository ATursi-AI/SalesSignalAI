"""
CRM views — Pipeline, Contacts, Contact Detail, Inbox, Appointments.
All views require login and filter by the user's BusinessProfile.
"""
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models.crm import Contact, Activity, Appointment
from core.models.leads import LeadAssignment
from core.models.outreach import OutreachEmail, OutreachProspect, GeneratedEmail
from core.models.competitors import TrackedCompetitor, CompetitorReview


def _get_business(request):
    """Get current user's business profile or None."""
    if hasattr(request.user, 'business_profile'):
        return request.user.business_profile
    return None


# ─────────────────────────────────────────────────────────────
# PIPELINE (Kanban)
# ─────────────────────────────────────────────────────────────

@login_required
def pipeline(request):
    bp = _get_business(request)
    if not bp:
        return redirect('onboarding')

    contacts = Contact.objects.filter(business=bp).select_related('source_lead')

    stages = ['new', 'contacted', 'follow_up', 'quoted', 'won', 'lost']
    pipeline_data = {}
    for stage in stages:
        pipeline_data[stage] = contacts.filter(pipeline_stage=stage)

    # Pipeline stats
    total_value = contacts.filter(pipeline_stage='quoted').aggregate(
        total=Sum('estimated_value'))['total'] or 0
    won_value = contacts.filter(pipeline_stage='won').aggregate(
        total=Sum('won_value'))['total'] or 0
    active_count = contacts.exclude(pipeline_stage__in=['won', 'lost']).count()

    context = {
        'pipeline_data': pipeline_data,
        'stages': Contact.STAGE_CHOICES,
        'total_value': total_value,
        'won_value': won_value,
        'active_count': active_count,
    }
    return render(request, 'crm/pipeline.html', context)


@login_required
@require_POST
def pipeline_move(request):
    """AJAX: Move a contact to a different pipeline stage."""
    bp = _get_business(request)
    if not bp:
        return JsonResponse({'error': 'No business profile'}, status=400)

    try:
        data = json.loads(request.body)
        contact_id = data.get('contact_id')
        new_stage = data.get('stage')
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid data'}, status=400)

    valid_stages = [s[0] for s in Contact.STAGE_CHOICES]
    if new_stage not in valid_stages:
        return JsonResponse({'error': 'Invalid stage'}, status=400)

    contact = get_object_or_404(Contact, id=contact_id, business=bp)
    old_stage = contact.pipeline_stage
    contact.pipeline_stage = new_stage
    contact.save(update_fields=['pipeline_stage', 'updated_at'])

    # Log the stage change
    old_label = dict(Contact.STAGE_CHOICES).get(old_stage, old_stage)
    new_label = dict(Contact.STAGE_CHOICES).get(new_stage, new_stage)
    Activity.objects.create(
        contact=contact,
        activity_type='stage_change',
        description=f'Moved from {old_label} to {new_label}',
        created_by=request.user,
    )

    # Sync back to LeadAssignment if linked
    if contact.source_assignment:
        stage_to_status = {
            'new': 'new', 'contacted': 'contacted', 'follow_up': 'contacted',
            'quoted': 'quoted', 'won': 'won', 'lost': 'lost',
        }
        assignment_status = stage_to_status.get(new_stage)
        if assignment_status:
            contact.source_assignment.status = assignment_status
            contact.source_assignment.save(update_fields=['status', 'updated_at'])

    return JsonResponse({'ok': True, 'stage': new_stage})


# ─────────────────────────────────────────────────────────────
# CONTACTS LIST
# ─────────────────────────────────────────────────────────────

@login_required
def contact_list(request):
    bp = _get_business(request)
    if not bp:
        return redirect('onboarding')

    contacts = Contact.objects.filter(business=bp).select_related('source_lead')

    # Filters
    stage = request.GET.get('stage')
    source = request.GET.get('source')
    search = request.GET.get('q', '').strip()

    if stage:
        contacts = contacts.filter(pipeline_stage=stage)
    if source:
        contacts = contacts.filter(source=source)
    if search:
        contacts = contacts.filter(
            Q(name__icontains=search) |
            Q(email__icontains=search) |
            Q(phone__icontains=search) |
            Q(service_needed__icontains=search)
        )

    context = {
        'contacts': contacts[:200],
        'stages': Contact.STAGE_CHOICES,
        'sources': Contact.SOURCE_CHOICES,
        'current_stage': stage,
        'current_source': source,
        'search_query': search,
        'total_count': contacts.count(),
    }
    return render(request, 'crm/contacts.html', context)


# ─────────────────────────────────────────────────────────────
# CONTACT DETAIL + ACTIVITY TIMELINE
# ─────────────────────────────────────────────────────────────

@login_required
def contact_detail(request, contact_id):
    bp = _get_business(request)
    if not bp:
        return redirect('onboarding')

    contact = get_object_or_404(Contact, id=contact_id, business=bp)
    activities = contact.activities.select_related('created_by').order_by('-created_at')
    appointments = contact.appointments.order_by('-date', '-time')[:5]

    context = {
        'contact': contact,
        'activities': activities,
        'appointments': appointments,
        'stages': Contact.STAGE_CHOICES,
    }
    return render(request, 'crm/contact_detail.html', context)


@login_required
@require_POST
def contact_add_note(request, contact_id):
    """Add a note/activity to a contact."""
    bp = _get_business(request)
    contact = get_object_or_404(Contact, id=contact_id, business=bp)

    activity_type = request.POST.get('activity_type', 'note')
    description = request.POST.get('description', '').strip()
    value = request.POST.get('value', '').strip()

    if not description:
        return JsonResponse({'error': 'Description required'}, status=400)

    valid_types = [t[0] for t in Activity.TYPE_CHOICES]
    if activity_type not in valid_types:
        activity_type = 'note'

    activity = Activity.objects.create(
        contact=contact,
        activity_type=activity_type,
        description=description,
        created_by=request.user,
    )

    if value:
        try:
            activity.value = Decimal(value)
            activity.save(update_fields=['value'])
        except (InvalidOperation, ValueError):
            pass

    # Handle special activity types
    if activity_type == 'won':
        contact.pipeline_stage = 'won'
        if value:
            try:
                contact.won_value = Decimal(value)
            except (InvalidOperation, ValueError):
                pass
        contact.save(update_fields=['pipeline_stage', 'won_value', 'updated_at'])
    elif activity_type == 'lost':
        contact.pipeline_stage = 'lost'
        contact.save(update_fields=['pipeline_stage', 'updated_at'])
    elif activity_type == 'quoted':
        contact.pipeline_stage = 'quoted'
        if value:
            try:
                contact.estimated_value = Decimal(value)
            except (InvalidOperation, ValueError):
                pass
        contact.save(update_fields=['pipeline_stage', 'estimated_value', 'updated_at'])

    return JsonResponse({
        'ok': True,
        'activity': {
            'id': activity.id,
            'type': activity.get_activity_type_display(),
            'description': activity.description,
            'icon': activity.icon,
            'color': activity.color,
            'created_at': activity.created_at.isoformat(),
        }
    })


@login_required
@require_POST
def contact_update(request, contact_id):
    """Update contact info (name, email, phone, address, stage, follow-up, value)."""
    bp = _get_business(request)
    contact = get_object_or_404(Contact, id=contact_id, business=bp)

    fields_changed = []
    for field in ['name', 'email', 'phone', 'address', 'service_needed', 'notes']:
        val = request.POST.get(field)
        if val is not None and getattr(contact, field) != val:
            setattr(contact, field, val)
            fields_changed.append(field)

    stage = request.POST.get('pipeline_stage')
    if stage and stage != contact.pipeline_stage:
        valid = [s[0] for s in Contact.STAGE_CHOICES]
        if stage in valid:
            old_label = contact.get_pipeline_stage_display()
            contact.pipeline_stage = stage
            new_label = dict(Contact.STAGE_CHOICES).get(stage, stage)
            Activity.objects.create(
                contact=contact,
                activity_type='stage_change',
                description=f'Moved from {old_label} to {new_label}',
                created_by=request.user,
            )
            fields_changed.append('pipeline_stage')

    est_val = request.POST.get('estimated_value', '').strip()
    if est_val:
        try:
            contact.estimated_value = Decimal(est_val)
            fields_changed.append('estimated_value')
        except (InvalidOperation, ValueError):
            pass

    follow_up = request.POST.get('next_follow_up', '').strip()
    if follow_up:
        try:
            from django.utils.dateparse import parse_datetime, parse_date
            dt = parse_datetime(follow_up)
            if not dt:
                d = parse_date(follow_up)
                if d:
                    from datetime import time
                    dt = timezone.make_aware(
                        timezone.datetime.combine(d, time(9, 0))
                    )
            if dt:
                contact.next_follow_up = dt
                fields_changed.append('next_follow_up')
                Activity.objects.create(
                    contact=contact,
                    activity_type='follow_up',
                    description=f'Follow-up reminder set for {dt.strftime("%b %d, %Y")}',
                    created_by=request.user,
                )
        except (ValueError, TypeError):
            pass
    elif request.POST.get('clear_follow_up'):
        contact.next_follow_up = None
        fields_changed.append('next_follow_up')

    if fields_changed:
        contact.save()

    return JsonResponse({'ok': True, 'fields_updated': fields_changed})


@login_required
@require_POST
def contact_create(request):
    """Manually create a new contact."""
    bp = _get_business(request)
    if not bp:
        return JsonResponse({'error': 'No business profile'}, status=400)

    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Name is required'}, status=400)

    contact = Contact.objects.create(
        business=bp,
        name=name,
        email=request.POST.get('email', '').strip(),
        phone=request.POST.get('phone', '').strip(),
        address=request.POST.get('address', '').strip(),
        service_needed=request.POST.get('service_needed', '').strip(),
        source='manual',
        pipeline_stage='new',
    )

    Activity.objects.create(
        contact=contact,
        activity_type='note',
        description='Contact created manually',
        created_by=request.user,
    )

    return JsonResponse({'ok': True, 'contact_id': contact.id})


# ─────────────────────────────────────────────────────────────
# INBOX (Email Replies)
# ─────────────────────────────────────────────────────────────

@login_required
def inbox(request):
    bp = _get_business(request)
    if not bp:
        return redirect('onboarding')

    # Legacy replied emails
    replied_emails = OutreachEmail.objects.filter(
        campaign__business=bp,
        status='replied',
    ).select_related('prospect', 'campaign').order_by('-replied_at')

    # New system: prospects that replied or are interested
    replied_prospects = OutreachProspect.objects.filter(
        campaign__business=bp,
        status__in=['replied', 'interested'],
    ).select_related('campaign').order_by('-replied_at')

    context = {
        'replied_emails': replied_emails[:50],
        'replied_prospects': replied_prospects[:50],
        'unread_count': replied_emails.count() + replied_prospects.count(),
    }
    return render(request, 'crm/inbox.html', context)


# ─────────────────────────────────────────────────────────────
# APPOINTMENTS
# ─────────────────────────────────────────────────────────────

@login_required
def appointment_list(request):
    bp = _get_business(request)
    if not bp:
        return redirect('onboarding')

    appointments = Appointment.objects.filter(
        business=bp
    ).select_related('contact').order_by('date', 'time')

    # Separate upcoming vs past
    today = timezone.now().date()
    upcoming = appointments.filter(date__gte=today).exclude(status__in=['cancelled'])
    past = appointments.filter(
        Q(date__lt=today) | Q(status__in=['completed', 'no_show', 'cancelled'])
    ).order_by('-date', '-time')

    context = {
        'upcoming': upcoming[:50],
        'past': past[:50],
        'contacts': Contact.objects.filter(business=bp).order_by('name')[:200],
    }
    return render(request, 'crm/appointments.html', context)


@login_required
@require_POST
def appointment_create(request):
    bp = _get_business(request)
    if not bp:
        return JsonResponse({'error': 'No business profile'}, status=400)

    contact_id = request.POST.get('contact_id')
    date = request.POST.get('date')
    time_val = request.POST.get('time')

    if not all([contact_id, date, time_val]):
        return JsonResponse({'error': 'Contact, date, and time required'}, status=400)

    contact = get_object_or_404(Contact, id=contact_id, business=bp)

    from django.utils.dateparse import parse_date, parse_time
    parsed_date = parse_date(date)
    parsed_time = parse_time(time_val)
    if not parsed_date or not parsed_time:
        return JsonResponse({'error': 'Invalid date or time'}, status=400)

    appt = Appointment.objects.create(
        contact=contact,
        business=bp,
        date=parsed_date,
        time=parsed_time,
        duration_minutes=int(request.POST.get('duration', 60)),
        service_needed=request.POST.get('service_needed', '').strip(),
        notes=request.POST.get('notes', '').strip(),
    )

    Activity.objects.create(
        contact=contact,
        activity_type='appointment',
        description=f'Appointment booked for {parsed_date.strftime("%b %d, %Y")} at {parsed_time.strftime("%I:%M %p")}',
        created_by=request.user,
    )

    return JsonResponse({'ok': True, 'appointment_id': appt.id})


@login_required
@require_POST
def appointment_update_status(request, appointment_id):
    bp = _get_business(request)
    appt = get_object_or_404(Appointment, id=appointment_id, business=bp)

    status = request.POST.get('status')
    valid = [s[0] for s in Appointment.STATUS_CHOICES]
    if status not in valid:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    appt.status = status
    appt.save(update_fields=['status', 'updated_at'])

    return JsonResponse({'ok': True, 'status': status})


# ─────────────────────────────────────────────────────────────
# COMPETITOR TRACKER (enhanced)
# ─────────────────────────────────────────────────────────────

@login_required
def competitor_dashboard(request):
    bp = _get_business(request)
    if not bp:
        return redirect('onboarding')

    competitors = TrackedCompetitor.objects.filter(
        business=bp, is_active=True
    ).order_by('name')

    # Get recent negative reviews for each
    competitor_data = []
    for comp in competitors:
        recent_negative = comp.reviews.filter(
            is_negative=True
        ).order_by('-review_date')[:5]

        competitor_data.append({
            'competitor': comp,
            'recent_negative': recent_negative,
            'negative_count_30d': comp.reviews.filter(
                is_negative=True,
                created_at__gte=timezone.now() - timedelta(days=30),
            ).count(),
        })

    context = {
        'competitor_data': competitor_data,
        'total_competitors': competitors.count(),
    }
    return render(request, 'crm/competitors.html', context)


# ─────────────────────────────────────────────────────────────
# REVENUE TRACKER API (for dashboard widget)
# ─────────────────────────────────────────────────────────────

@login_required
def revenue_data(request):
    """JSON endpoint for the revenue tracker widget."""
    bp = _get_business(request)
    if not bp:
        return JsonResponse({'error': 'No business'}, status=400)

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # This month's won deals
    won_this_month = Contact.objects.filter(
        business=bp,
        pipeline_stage='won',
        updated_at__gte=month_start,
    )
    deals_won = won_this_month.count()
    total_revenue = won_this_month.aggregate(total=Sum('won_value'))['total'] or 0

    # Active leads (not won/lost)
    active_leads = Contact.objects.filter(
        business=bp,
    ).exclude(pipeline_stage__in=['won', 'lost']).count()

    # Conversion rate (last 90 days)
    ninety_days_ago = now - timedelta(days=90)
    total_contacts_90d = Contact.objects.filter(
        business=bp,
        created_at__gte=ninety_days_ago,
    ).count()
    won_contacts_90d = Contact.objects.filter(
        business=bp,
        pipeline_stage='won',
        updated_at__gte=ninety_days_ago,
    ).count()
    conversion_rate = round((won_contacts_90d / total_contacts_90d * 100), 1) if total_contacts_90d else 0

    # Monthly trend (last 6 months)
    monthly_trend = []
    for i in range(5, -1, -1):
        month = (now.month - i - 1) % 12 + 1
        year = now.year - ((now.month - i - 1) < 0)
        month_won = Contact.objects.filter(
            business=bp,
            pipeline_stage='won',
            updated_at__year=year,
            updated_at__month=month,
        ).aggregate(total=Sum('won_value'))['total'] or 0
        import calendar
        monthly_trend.append({
            'month': calendar.month_abbr[month],
            'revenue': float(month_won),
        })

    # ROI calculation
    tier_costs = {'starter': 99, 'growth': 249, 'pro': 499}
    monthly_cost = tier_costs.get(bp.subscription_tier, 249)

    return JsonResponse({
        'deals_won': deals_won,
        'total_revenue': float(total_revenue),
        'active_leads': active_leads,
        'conversion_rate': conversion_rate,
        'monthly_cost': monthly_cost,
        'roi_multiplier': round(float(total_revenue) / monthly_cost, 1) if monthly_cost else 0,
        'monthly_trend': monthly_trend,
    })
