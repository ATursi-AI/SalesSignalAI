from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q
from django.views.decorators.http import require_POST
from core.models import LeadAssignment, Lead, ServiceCategory


@login_required
def lead_feed(request):
    profile = request.user.business_profile
    assignments = LeadAssignment.objects.filter(
        business=profile
    ).exclude(
        status='dismissed'
    ).select_related('lead', 'lead__detected_service_type').order_by('-created_at')

    # Filters
    platform = request.GET.get('platform', '')
    urgency = request.GET.get('urgency', '')
    status = request.GET.get('status', '')
    search = request.GET.get('q', '')

    if platform:
        assignments = assignments.filter(lead__platform=platform)
    if urgency:
        assignments = assignments.filter(lead__urgency_level=urgency)
    if status:
        assignments = assignments.filter(status=status)
    if search:
        assignments = assignments.filter(
            Q(lead__source_content__icontains=search) |
            Q(lead__detected_location__icontains=search)
        )

    # Urgency counts for header (exclude dismissed)
    active = LeadAssignment.objects.filter(business=profile).exclude(status='dismissed')
    hot_count = active.filter(lead__urgency_level='hot', status__in=['new', 'alerted', 'viewed']).count()
    warm_count = active.filter(lead__urgency_level='warm', status__in=['new', 'alerted', 'viewed']).count()
    new_count = active.filter(lead__urgency_level='new', status__in=['new', 'alerted', 'viewed']).count()

    platforms = Lead.PLATFORM_CHOICES
    statuses = LeadAssignment.STATUS_CHOICES

    is_trial = (profile.account_status == 'trial' or profile.subscription_tier == 'none')
    free_limit = 5

    context = {
        'assignments': assignments[:50],
        'hot_count': hot_count,
        'warm_count': warm_count,
        'new_count': new_count,
        'platforms': platforms,
        'statuses': statuses,
        'current_platform': platform,
        'current_urgency': urgency,
        'current_status': status,
        'current_search': search,
        'is_trial': is_trial,
        'free_limit': free_limit,
        'trial_leads_remaining': profile.trial_leads_remaining if is_trial else 0,
    }
    return render(request, 'leads/feed.html', context)


@login_required
def lead_detail(request, assignment_id):
    profile = request.user.business_profile
    assignment = get_object_or_404(
        LeadAssignment.objects.select_related('lead', 'lead__detected_service_type'),
        id=assignment_id,
        business=profile,
    )

    is_trial = (profile.account_status == 'trial' or profile.subscription_tier == 'none')
    lead_blocked = False

    if is_trial and profile.trial_leads_remaining <= 0:
        lead_blocked = True

    # Mark as viewed and decrement trial counter
    if assignment.status in ('new', 'alerted'):
        if not assignment.viewed_at:
            assignment.viewed_at = timezone.now()
            if is_trial and profile.trial_leads_remaining > 0:
                profile.trial_leads_remaining -= 1
                profile.save(update_fields=['trial_leads_remaining'])
        if assignment.status in ('new', 'alerted'):
            assignment.status = 'viewed'
        assignment.save()

    # Auto-create CRM contact on first view
    lead = assignment.lead
    if (lead.contact_name or lead.contact_business) and not lead_blocked:
        _auto_create_contact(profile, lead)

    # Similar leads in same area
    similar = LeadAssignment.objects.filter(
        business=profile,
        lead__detected_location=lead.detected_location,
    ).exclude(id=assignment.id).select_related('lead')[:5] if lead.detected_location else []

    # Active campaigns for "Add to Campaign" dropdown
    from core.models import OutreachCampaign
    campaigns = OutreachCampaign.objects.filter(
        business=profile, status='active'
    ).values_list('id', 'name')

    context = {
        'assignment': assignment,
        'lead': lead,
        'similar_leads': similar,
        'is_trial': is_trial,
        'lead_blocked': lead_blocked,
        'trial_leads_remaining': profile.trial_leads_remaining,
        'campaigns': list(campaigns),
    }
    return render(request, 'leads/detail.html', context)


def _auto_create_contact(profile, lead):
    """Auto-create a CRM contact from a lead if one doesn't exist."""
    from core.models.crm import Contact
    name = lead.contact_name or lead.contact_business or ''
    if not name:
        return
    existing = Contact.objects.filter(business=profile, name=name).first()
    if existing:
        return
    Contact.objects.create(
        business=profile,
        name=name,
        phone=lead.contact_phone or '',
        email=lead.contact_email or '',
        address=lead.contact_address or '',
        source='lead',
        source_lead=lead,
        notes=f'Auto-created from lead #{lead.id}',
    )


@login_required
def lead_update_status(request, assignment_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = request.user.business_profile
    assignment = get_object_or_404(LeadAssignment, id=assignment_id, business=profile)

    new_status = request.POST.get('status', '')
    valid_statuses = [s[0] for s in LeadAssignment.STATUS_CHOICES]
    if new_status not in valid_statuses:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    assignment.status = new_status

    if new_status == 'contacted' and not assignment.contacted_at:
        assignment.contacted_at = timezone.now()
    if new_status == 'viewed' and not assignment.viewed_at:
        assignment.viewed_at = timezone.now()

    revenue = request.POST.get('revenue', '')
    if revenue:
        try:
            assignment.revenue = float(revenue)
        except (ValueError, TypeError):
            pass

    notes = request.POST.get('notes', '')
    if notes:
        assignment.notes = notes

    assignment.save()

    # Auto-create pipeline entry for contacted/quoted/won
    if new_status in ('contacted', 'quoted', 'won'):
        _auto_create_pipeline_entry(profile, assignment, new_status)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'status': assignment.status,
            'status_display': assignment.get_status_display(),
        })

    return redirect('lead_detail', assignment_id=assignment.id)


def _auto_create_pipeline_entry(profile, assignment, status):
    """Auto-create CRM Contact pipeline entry when lead status changes."""
    from core.models.crm import Contact
    lead = assignment.lead
    name = lead.contact_name or lead.contact_business or 'Unknown'

    contact = Contact.objects.filter(business=profile, source_lead=lead).first()
    if not contact:
        contact = Contact.objects.filter(business=profile, name=name).first()
    if not contact:
        contact = Contact.objects.create(
            business=profile,
            name=name,
            phone=lead.contact_phone or '',
            email=lead.contact_email or '',
            address=lead.contact_address or '',
            source='lead',
            source_lead=lead,
        )

    stage_map = {'contacted': 'contacted', 'quoted': 'quoted', 'won': 'won'}
    new_stage = stage_map.get(status, 'new')
    if contact.pipeline_stage != new_stage:
        contact.pipeline_stage = new_stage
        contact.save(update_fields=['pipeline_stage'])


@login_required
@require_POST
def lead_dismiss(request, assignment_id):
    """Dismiss a lead — hides it from the feed."""
    profile = request.user.business_profile
    assignment = get_object_or_404(LeadAssignment, id=assignment_id, business=profile)
    assignment.status = 'dismissed'
    assignment.save(update_fields=['status'])
    return JsonResponse({'ok': True})


@login_required
def lead_bulk_action(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = request.user.business_profile
    action = request.POST.get('action', '')
    ids = request.POST.getlist('lead_ids')

    if not ids or not action:
        return JsonResponse({'error': 'Missing params'}, status=400)

    assignments = LeadAssignment.objects.filter(id__in=ids, business=profile)

    if action == 'contacted':
        assignments.update(status='contacted', contacted_at=timezone.now())
    elif action == 'dismiss':
        assignments.update(status='dismissed')

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'count': len(ids)})

    return redirect('lead_feed')
