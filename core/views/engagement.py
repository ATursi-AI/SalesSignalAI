"""
Engagement views — Voicemail Drops, Booking Pages, Review Campaigns.
"""
import json
import logging
from datetime import datetime, timedelta, time as dt_time

from django.db import models
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q, Count

from core.models import (
    VoicemailDrop, VoicemailDropLog,
    BookingPage, BookingSubmission,
    ReviewCampaign, ReviewRequest,
    Contact, Appointment, Activity,
    SalesProspect, Lead,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# VOICEMAIL DROPS
# ═══════════════════════════════════════════════════════════════════

@login_required
def voicemail_drops(request):
    """List voicemail drop templates and recent activity."""
    business = getattr(request.user, 'business_profile', None)
    templates = VoicemailDrop.objects.filter(
        Q(business=business) | Q(business__isnull=True)
    ).filter(is_active=True)
    recent_logs = VoicemailDropLog.objects.filter(
        voicemail__in=templates
    ).select_related('voicemail')[:50]

    # Stats
    total_drops = VoicemailDropLog.objects.filter(voicemail__in=templates).count()
    delivered = VoicemailDropLog.objects.filter(voicemail__in=templates, status='delivered').count()

    return render(request, 'engagement/voicemail_drops.html', {
        'templates': templates,
        'recent_logs': recent_logs,
        'total_drops': total_drops,
        'delivered': delivered,
        'delivery_rate': round(delivered / total_drops * 100, 1) if total_drops else 0,
    })


@login_required
@require_POST
def voicemail_drop_create(request):
    """Create a new voicemail drop template."""
    business = getattr(request.user, 'business_profile', None)
    name = request.POST.get('name', '').strip()
    audio_url = request.POST.get('audio_url', '').strip()
    description = request.POST.get('description', '').strip()

    if not name or not audio_url:
        return JsonResponse({'ok': False, 'error': 'Name and audio URL are required'})

    vm = VoicemailDrop.objects.create(
        business=business,
        name=name,
        audio_url=audio_url,
        description=description,
    )
    return JsonResponse({'ok': True, 'id': vm.id, 'name': vm.name})


@login_required
@require_POST
def voicemail_drop_send(request):
    """Send voicemail drop to one or more numbers."""
    from core.services.signalwire_service import drop_voicemail

    data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
    vm_id = data.get('voicemail_id')
    numbers = data.get('numbers', [])  # list of phone numbers
    prospect_ids = data.get('prospect_ids', [])  # OR list of sales prospect IDs

    if not vm_id:
        return JsonResponse({'ok': False, 'error': 'voicemail_id required'})

    vm = get_object_or_404(VoicemailDrop, id=vm_id)
    salesperson = getattr(request.user, 'salesperson_profile', None)

    # Build recipient list from prospect IDs if provided
    if prospect_ids and not numbers:
        prospects = SalesProspect.objects.filter(id__in=prospect_ids).exclude(phone='')
        numbers = []
        prospect_map = {}
        for p in prospects:
            numbers.append(p.phone)
            prospect_map[p.phone] = p

    # Create logs and send
    results = {'sent': 0, 'failed': 0, 'errors': []}
    callback_url = request.build_absolute_uri('/api/voicemail-drop/status/')

    for number in numbers:
        # Create log entry
        log = VoicemailDropLog.objects.create(
            voicemail=vm,
            to_number=number,
            salesperson=salesperson,
            prospect=prospect_map.get(number) if 'prospect_map' in dir() else None,
            status='queued',
        )

        # Fire the call
        result = drop_voicemail(number, vm.audio_url, callback_url)

        if result.get('ok'):
            log.status = 'calling'
            log.call_sid = result.get('sid', '')
            log.save()
            results['sent'] += 1
        else:
            log.status = 'failed'
            log.error_message = result.get('error', '')
            log.completed_at = timezone.now()
            log.save()
            results['failed'] += 1
            results['errors'].append({'number': number, 'error': result.get('error', '')})

    # Update template usage counter
    vm.times_used += results['sent']
    vm.save(update_fields=['times_used'])

    return JsonResponse({'ok': True, **results})


@csrf_exempt
@require_POST
def voicemail_drop_status_webhook(request):
    """SignalWire callback for voicemail drop call status."""
    call_sid = request.POST.get('CallSid', '')
    call_status = request.POST.get('CallStatus', '')
    answered_by = request.POST.get('AnsweredBy', '')  # human, machine, unknown

    if not call_sid:
        return JsonResponse({'ok': False})

    try:
        log = VoicemailDropLog.objects.get(call_sid=call_sid)
    except VoicemailDropLog.DoesNotExist:
        return JsonResponse({'ok': False})

    status_map = {
        'completed': 'delivered',
        'busy': 'busy',
        'no-answer': 'no_answer',
        'failed': 'failed',
        'canceled': 'failed',
    }
    log.status = status_map.get(call_status, log.status)
    log.completed_at = timezone.now()
    log.save()

    return JsonResponse({'ok': True})


@login_required
@require_POST
def voicemail_drop_delete(request, vm_id):
    """Deactivate a voicemail template."""
    vm = get_object_or_404(VoicemailDrop, id=vm_id)
    vm.is_active = False
    vm.save(update_fields=['is_active'])
    return JsonResponse({'ok': True})


# ═══════════════════════════════════════════════════════════════════
# BOOKING PAGES
# ═══════════════════════════════════════════════════════════════════

@login_required
def booking_page_list(request):
    """List booking pages for the business."""
    business = getattr(request.user, 'business_profile', None)
    pages = BookingPage.objects.filter(business=business)
    submissions = BookingSubmission.objects.filter(
        booking_page__business=business
    ).select_related('booking_page')[:30]

    return render(request, 'engagement/booking_pages.html', {
        'pages': pages,
        'recent_submissions': submissions,
    })


@login_required
@require_POST
def booking_page_create(request):
    """Create a new booking page."""
    business = getattr(request.user, 'business_profile', None)
    if not business:
        return JsonResponse({'ok': False, 'error': 'No business profile'})

    title = request.POST.get('title', '').strip() or f'{business.business_name} — Book a Consultation'
    slug = request.POST.get('slug', '').strip()
    if not slug:
        slug = business.business_name.lower().replace(' ', '-').replace("'", '')[:50]
        # Ensure unique
        base_slug = slug
        counter = 1
        while BookingPage.objects.filter(slug=slug).exists():
            slug = f'{base_slug}-{counter}'
            counter += 1

    available_days = request.POST.getlist('available_days', ['0', '1', '2', '3', '4'])
    try:
        available_days = [int(d) for d in available_days]
    except (ValueError, TypeError):
        available_days = [0, 1, 2, 3, 4]

    page = BookingPage.objects.create(
        business=business,
        slug=slug,
        title=title,
        description=request.POST.get('description', ''),
        available_days=available_days,
        start_time=request.POST.get('start_time', '09:00'),
        end_time=request.POST.get('end_time', '17:00'),
        slot_duration_minutes=int(request.POST.get('slot_duration', 30)),
    )
    return JsonResponse({
        'ok': True,
        'id': page.id,
        'slug': page.slug,
        'url': f'/book/{page.slug}/',
    })


@login_required
@require_POST
def booking_page_update(request, page_id):
    """Update an existing booking page."""
    business = getattr(request.user, 'business_profile', None)
    page = get_object_or_404(BookingPage, id=page_id, business=business)

    if request.POST.get('title'):
        page.title = request.POST['title']
    if request.POST.get('description') is not None:
        page.description = request.POST.get('description', '')
    if request.POST.get('start_time'):
        page.start_time = request.POST['start_time']
    if request.POST.get('end_time'):
        page.end_time = request.POST['end_time']
    if request.POST.get('slot_duration'):
        page.slot_duration_minutes = int(request.POST['slot_duration'])
    if request.POST.get('is_active') is not None:
        page.is_active = request.POST.get('is_active') == 'true'
    if request.POST.getlist('available_days'):
        page.available_days = [int(d) for d in request.POST.getlist('available_days')]

    page.save()
    return JsonResponse({'ok': True})


def booking_public_page(request, slug):
    """Public-facing booking page — no auth required."""
    page = get_object_or_404(BookingPage, slug=slug, is_active=True)

    # Increment view counter
    BookingPage.objects.filter(id=page.id).update(page_views=models.F('page_views') + 1)

    # Generate available time slots for next 14 days
    slots_by_date = {}
    today = timezone.now().date()

    for day_offset in range(14):
        date = today + timedelta(days=day_offset)
        weekday = date.weekday()  # 0=Mon

        if weekday not in (page.available_days or []):
            continue

        # Get already-booked times for this date
        booked_times = set(
            BookingSubmission.objects.filter(
                booking_page=page,
                date=date,
                status__in=['pending', 'confirmed'],
            ).values_list('time', flat=True)
        )

        # Check max bookings per day
        day_count = len(booked_times)
        if day_count >= page.max_bookings_per_day:
            continue

        # Generate slots
        slots = []
        current = datetime.combine(date, page.start_time)
        end = datetime.combine(date, page.end_time)

        while current + timedelta(minutes=page.slot_duration_minutes) <= end:
            slot_time = current.time()
            if slot_time not in booked_times:
                # Don't show past slots for today
                if date == today and current <= datetime.now():
                    current += timedelta(minutes=page.slot_duration_minutes)
                    continue
                slots.append(slot_time.strftime('%I:%M %p'))
            current += timedelta(minutes=page.slot_duration_minutes)

        if slots:
            slots_by_date[date.strftime('%Y-%m-%d')] = {
                'display': date.strftime('%A, %b %d'),
                'slots': slots,
            }

    return render(request, 'engagement/booking_public.html', {
        'page': page,
        'business': page.business,
        'slots_by_date': json.dumps(slots_by_date),
    })


@csrf_exempt
@require_POST
def booking_submit(request, slug):
    """Handle public booking form submission."""
    page = get_object_or_404(BookingPage, slug=slug, is_active=True)

    name = request.POST.get('name', '').strip()
    phone = request.POST.get('phone', '').strip()
    email = request.POST.get('email', '').strip()
    date_str = request.POST.get('date', '')
    time_str = request.POST.get('time', '')
    service = request.POST.get('service_needed', '').strip()
    notes = request.POST.get('notes', '').strip()

    if not name or not phone or not date_str or not time_str:
        return JsonResponse({'ok': False, 'error': 'Name, phone, date, and time are required'})

    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
        time_obj = datetime.strptime(time_str, '%I:%M %p').time()
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid date or time format'})

    # Check double-booking
    if BookingSubmission.objects.filter(
        booking_page=page, date=date, time=time_obj,
        status__in=['pending', 'confirmed']
    ).exists():
        return JsonResponse({'ok': False, 'error': 'This time slot is no longer available'})

    # Create the booking
    submission = BookingSubmission.objects.create(
        booking_page=page,
        name=name,
        phone=phone,
        email=email,
        service_needed=service,
        notes=notes,
        date=date,
        time=time_obj,
    )

    # Create or link CRM contact
    business = page.business
    contact, created = Contact.objects.get_or_create(
        business=business,
        phone=phone,
        defaults={
            'name': name,
            'email': email,
            'source': 'referral',
            'source_platform': 'booking_page',
            'service_needed': service,
            'pipeline_stage': 'new',
        }
    )
    if not created and email and not contact.email:
        contact.email = email
        contact.save(update_fields=['email'])

    # Create appointment in CRM
    appt = Appointment.objects.create(
        contact=contact,
        business=business,
        date=date,
        time=time_obj,
        duration_minutes=page.slot_duration_minutes,
        service_needed=service,
        notes=f'Booked online (code: {submission.confirmation_code})\n{notes}',
    )
    submission.contact = contact
    submission.appointment = appt
    submission.save(update_fields=['contact', 'appointment'])

    # Log activity
    Activity.objects.create(
        contact=contact,
        activity_type='appointment',
        description=f'Appointment booked online for {date.strftime("%b %d")} at {time_obj.strftime("%I:%M %p")}',
    )

    # Increment booking counter
    BookingPage.objects.filter(id=page.id).update(bookings_made=models.F('bookings_made') + 1)

    # Send confirmation SMS to the person who booked
    try:
        from core.services.signalwire_service import send_sms
        msg = (
            f'Confirmed! Your appointment with {business.business_name} is booked for '
            f'{date.strftime("%b %d")} at {time_obj.strftime("%I:%M %p")}. '
            f'Code: {submission.confirmation_code}'
        )
        send_sms(phone, msg)
    except Exception as e:
        logger.warning(f'[booking] SMS confirmation failed: {e}')

    # Notify business owner
    try:
        from core.services.signalwire_service import send_sms as sms
        if business.alert_via_sms and business.alert_phone:
            sms(business.alert_phone,
                f'New booking! {name} ({phone}) booked for {date.strftime("%b %d")} at {time_obj.strftime("%I:%M %p")}. Service: {service or "Not specified"}')
    except Exception as e:
        logger.warning(f'[booking] Owner notification failed: {e}')

    return JsonResponse({
        'ok': True,
        'confirmation_code': submission.confirmation_code,
        'date': date.strftime('%A, %B %d'),
        'time': time_obj.strftime('%I:%M %p'),
    })


@login_required
@require_POST
def booking_submission_action(request, submission_id):
    """Update a booking submission status."""
    business = getattr(request.user, 'business_profile', None)
    sub = get_object_or_404(BookingSubmission, id=submission_id, booking_page__business=business)
    new_status = request.POST.get('status')
    if new_status in dict(BookingSubmission.STATUS_CHOICES):
        sub.status = new_status
        sub.save(update_fields=['status'])
        # Mirror to appointment
        if sub.appointment:
            appt_status_map = {
                'confirmed': 'upcoming',
                'completed': 'completed',
                'cancelled': 'cancelled',
                'no_show': 'no_show',
            }
            if new_status in appt_status_map:
                sub.appointment.status = appt_status_map[new_status]
                sub.appointment.save(update_fields=['status'])
    return JsonResponse({'ok': True})


# ═══════════════════════════════════════════════════════════════════
# REVIEW CAMPAIGNS
# ═══════════════════════════════════════════════════════════════════

@login_required
def review_campaigns(request):
    """List review campaigns for the business."""
    business = getattr(request.user, 'business_profile', None)
    campaigns = ReviewCampaign.objects.filter(business=business).annotate(
        request_count=Count('reviewrequest'),
    )
    return render(request, 'engagement/review_campaigns.html', {
        'campaigns': campaigns,
    })


@login_required
@require_POST
def review_campaign_create(request):
    """Create a new review campaign."""
    business = getattr(request.user, 'business_profile', None)
    if not business:
        return JsonResponse({'ok': False, 'error': 'No business profile'})

    data = request.POST
    campaign = ReviewCampaign.objects.create(
        business=business,
        name=data.get('name', f'{business.business_name} Review Campaign'),
        google_review_url=data.get('google_review_url', ''),
        yelp_review_url=data.get('yelp_review_url', ''),
        channel=data.get('channel', 'sms'),
        auto_send_on_won=data.get('auto_send_on_won') == 'true',
        delay_hours=int(data.get('delay_hours', 24)),
        sms_template=data.get('sms_template', ReviewCampaign._meta.get_field('sms_template').default),
        email_subject=data.get('email_subject', ReviewCampaign._meta.get_field('email_subject').default),
        email_template=data.get('email_template', ReviewCampaign._meta.get_field('email_template').default),
    )
    return JsonResponse({'ok': True, 'id': campaign.id})


@login_required
@require_POST
def review_campaign_send(request, campaign_id):
    """Send review requests to selected contacts."""
    from core.services.signalwire_service import send_sms

    business = getattr(request.user, 'business_profile', None)
    campaign = get_object_or_404(ReviewCampaign, id=campaign_id, business=business)

    data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
    contact_ids = data.get('contact_ids', [])

    if not contact_ids:
        # Default: all "won" contacts not yet sent this campaign
        already_sent = ReviewRequest.objects.filter(campaign=campaign).values_list('contact_id', flat=True)
        contacts = Contact.objects.filter(
            business=business,
            pipeline_stage='won',
        ).exclude(id__in=already_sent).exclude(phone='')
    else:
        contacts = Contact.objects.filter(id__in=contact_ids, business=business).exclude(phone='')

    review_url = campaign.google_review_url or campaign.yelp_review_url
    results = {'sent': 0, 'failed': 0, 'skipped': 0}

    for contact in contacts:
        # Skip if already sent
        if ReviewRequest.objects.filter(campaign=campaign, contact=contact).exists():
            results['skipped'] += 1
            continue

        # Build message
        message = campaign.sms_template.format(
            name=contact.name.split()[0] if contact.name else 'there',
            business=business.business_name,
            link=review_url,
        )

        rr = ReviewRequest.objects.create(
            campaign=campaign,
            contact=contact,
            sent_via='sms' if campaign.channel in ('sms', 'both') else 'email',
        )

        if campaign.channel in ('sms', 'both') and contact.phone:
            result = send_sms(contact.phone, message)
            if result.get('ok'):
                rr.status = 'sent'
                rr.sent_at = timezone.now()
                rr.sms_sid = result.get('sid', '')
                results['sent'] += 1
            else:
                rr.status = 'failed'
                results['failed'] += 1
        else:
            results['skipped'] += 1

        rr.save()

    # Update campaign counters
    campaign.total_sent += results['sent']
    campaign.status = 'active'
    campaign.save(update_fields=['total_sent', 'status'])

    return JsonResponse({'ok': True, **results})


@login_required
@require_POST
def review_campaign_toggle(request, campaign_id):
    """Pause or activate a review campaign."""
    business = getattr(request.user, 'business_profile', None)
    campaign = get_object_or_404(ReviewCampaign, id=campaign_id, business=business)
    if campaign.status == 'active':
        campaign.status = 'paused'
    elif campaign.status in ('paused', 'draft'):
        campaign.status = 'active'
    campaign.save(update_fields=['status'])
    return JsonResponse({'ok': True, 'status': campaign.status})


@login_required
def review_campaign_detail(request, campaign_id):
    """Detail view for a review campaign with all requests."""
    business = getattr(request.user, 'business_profile', None)
    campaign = get_object_or_404(ReviewCampaign, id=campaign_id, business=business)
    requests_qs = ReviewRequest.objects.filter(campaign=campaign).select_related('contact')

    # Available contacts to send to (won, have phone, not yet sent)
    already_sent = requests_qs.values_list('contact_id', flat=True)
    available_contacts = Contact.objects.filter(
        business=business,
        pipeline_stage='won',
    ).exclude(id__in=already_sent).exclude(phone='')[:100]

    return render(request, 'engagement/review_campaign_detail.html', {
        'campaign': campaign,
        'review_requests': requests_qs[:100],
        'available_contacts': available_contacts,
        'sent_count': requests_qs.filter(status='sent').count(),
        'clicked_count': requests_qs.filter(status='clicked').count(),
        'reviewed_count': requests_qs.filter(status='reviewed').count(),
    })


@csrf_exempt
def review_click_track(request, request_id):
    """Track when someone clicks the review link and redirect."""
    try:
        rr = ReviewRequest.objects.get(id=request_id)
        if rr.status == 'sent':
            rr.status = 'clicked'
            rr.clicked_at = timezone.now()
            rr.save(update_fields=['status', 'clicked_at'])
            # Update campaign counter
            ReviewCampaign.objects.filter(id=rr.campaign_id).update(
                total_clicked=models.F('total_clicked') + 1
            )
        # Redirect to the actual review URL
        review_url = rr.campaign.google_review_url or rr.campaign.yelp_review_url
        if review_url:
            return redirect(review_url)
    except ReviewRequest.DoesNotExist:
        pass
    return redirect('/')
