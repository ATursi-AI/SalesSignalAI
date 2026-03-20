"""
SignalWire Call Center — webhooks, API endpoints, and dashboard views.
"""
import json
import logging
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db.models import Count, Sum, Avg, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST

from core.models import Lead, SMSMessage, SMSOptOut, CallLog
from core.models.sales import SalesPerson, SalesProspect
from core.models.prospect_videos import ProspectVideo
from core.services import signalwire_service

logger = logging.getLogger(__name__)


# ── Helper: find lead by phone ───────────────────────────────────────

def _find_lead_by_phone(phone):
    """Look up a lead by phone number (try exact and last 10 digits)."""
    lead = Lead.objects.filter(contact_phone=phone).order_by('-discovered_at').first()
    if not lead and len(phone) >= 10:
        digits = phone[-10:]
        lead = Lead.objects.filter(contact_phone__endswith=digits).order_by('-discovered_at').first()
    return lead


def _find_prospect_video_by_phone(phone):
    """Look up a prospect video by phone number."""
    pv = ProspectVideo.objects.filter(prospect_phone=phone).order_by('-created_at').first()
    if not pv and len(phone) >= 10:
        digits = phone[-10:]
        pv = ProspectVideo.objects.filter(prospect_phone__endswith=digits).order_by('-created_at').first()
    return pv


def _notify_admin(subject, body):
    """Send notification to admin via email and SMS."""
    try:
        send_mail(
            subject, body,
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'alerts@salessignal.ai'),
            [getattr(settings, 'ALERT_FROM_EMAIL', 'alerts@salessignal.ai')],
            fail_silently=True,
        )
    except Exception:
        pass

    fallback = getattr(settings, 'SIGNALWIRE_FALLBACK_PHONE', '')
    if fallback:
        try:
            signalwire_service.send_sms(fallback, body[:1500])
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# WEBHOOKS (called by SignalWire)
# ═══════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
def sms_webhook(request):
    """Handle inbound SMS from SignalWire."""
    from_number = request.POST.get('From', '')
    to_number = request.POST.get('To', '')
    body = request.POST.get('Body', '').strip()
    message_sid = request.POST.get('MessageSid', '')

    logger.info(f'[sms_webhook] From={from_number} Body={body[:100]}')

    # Find associated lead or prospect video
    lead = _find_lead_by_phone(from_number)
    pv = _find_prospect_video_by_phone(from_number)

    # Log the inbound message
    sms = SMSMessage.objects.create(
        message_sid=message_sid,
        direction='inbound',
        from_number=from_number,
        to_number=to_number,
        body=body,
        status='received',
        lead=lead,
        is_yes_response='yes' in body.lower().split(),
        is_opt_out=body.strip().upper() in ('STOP', 'UNSUBSCRIBE', 'QUIT', 'CANCEL'),
    )

    # Handle STOP / opt-out
    if sms.is_opt_out:
        SMSOptOut.objects.get_or_create(phone_number=from_number)
        signalwire_service.send_sms(from_number, "You've been unsubscribed. Reply START to re-subscribe.")
        logger.info(f'[sms_webhook] Opt-out: {from_number}')
        return HttpResponse('<Response></Response>', content_type='application/xml')

    # Handle START / re-subscribe
    if body.strip().upper() in ('START', 'SUBSCRIBE', 'UNSTOP'):
        SMSOptOut.objects.filter(phone_number=from_number).delete()
        signalwire_service.send_sms(from_number, "You've been re-subscribed. Reply STOP at any time to opt out.")
        return HttpResponse('<Response></Response>', content_type='application/xml')

    # Handle YES response
    if sms.is_yes_response:
        signalwire_service.send_sms(from_number, "Thanks! Someone from our team will call you within the hour.")

        # Mark prospect video as responded
        if pv and pv.status == 'active':
            pv.prospect_responded = True
            pv.response_date = timezone.now()
            pv.response_notes = f"Texted YES: {body}"
            pv.status = 'responded'
            pv.save(update_fields=['prospect_responded', 'response_date', 'response_notes', 'status'])

        name = 'Unknown'
        details = body
        if lead:
            name = lead.contact_name or lead.source_author or 'Unknown'
            details = f"{lead.source_content[:200]}"
        elif pv:
            name = pv.prospect_owner_name or pv.prospect_business_name
            details = f"Prospect video: /demo/{pv.slug}/"

        _notify_admin(
            f'HOT LEAD: {name} texted YES',
            f'HOT LEAD: {name} texted YES.\nTheir number: {from_number}\n\n'
            f'Message: {body}\n\nDetails: {details}'
        )
        return HttpResponse('<Response></Response>', content_type='application/xml')

    # Any other response — notify admin
    name = 'Unknown'
    if lead:
        name = lead.contact_name or lead.source_author or from_number
    elif pv:
        name = pv.prospect_owner_name or pv.prospect_business_name or from_number

    _notify_admin(
        f'SMS reply from {name}',
        f'SMS from {from_number} ({name}):\n\n{body}'
    )

    return HttpResponse('<Response></Response>', content_type='application/xml')


@csrf_exempt
@require_POST
def voice_webhook(request):
    """Handle inbound voice calls from SignalWire."""
    from_number = request.POST.get('From', '')
    to_number = request.POST.get('To', '')
    call_sid = request.POST.get('CallSid', '')

    logger.info(f'[voice_webhook] Inbound call from {from_number} sid={call_sid}')

    lead = _find_lead_by_phone(from_number)

    # Log the call
    CallLog.objects.create(
        call_sid=call_sid,
        direction='inbound',
        from_number=from_number,
        to_number=to_number,
        status='ringing',
        started_at=timezone.now(),
        lead=lead,
    )

    # Forward to fallback phone (Andrew's cell)
    fallback = getattr(settings, 'SIGNALWIRE_FALLBACK_PHONE', '')
    if not fallback:
        # No fallback — go to voicemail
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            '<Say voice="alice">Sorry, no one is available right now. Please leave a message after the beep.</Say>'
            '<Record maxLength="120" transcribe="true" transcribeCallback="/api/signalwire/transcription-webhook/" />'
            '</Response>'
        )
        return HttpResponse(xml, content_type='application/xml')

    # Forward with 30 second timeout, then voicemail
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Dial timeout="30" callerId="{from_number}">'
        f'<Number>{fallback}</Number>'
        '</Dial>'
        '<Say voice="alice">Sorry, no one is available right now. Please leave a message after the beep.</Say>'
        '<Record maxLength="120" transcribe="true" transcribeCallback="/api/signalwire/transcription-webhook/" />'
        '</Response>'
    )
    return HttpResponse(xml, content_type='application/xml')


@csrf_exempt
@require_POST
def call_status_webhook(request):
    """Handle call status updates from SignalWire."""
    call_sid = request.POST.get('CallSid', '')
    status = request.POST.get('CallStatus', '')
    duration = request.POST.get('CallDuration', '0')
    recording_url = request.POST.get('RecordingUrl', '')

    logger.info(f'[call_status] sid={call_sid} status={status} duration={duration}')

    try:
        call_log = CallLog.objects.get(call_sid=call_sid)
        call_log.status = status
        if duration:
            call_log.duration = int(duration)
        if recording_url:
            call_log.recording_url = recording_url
        if status in ('completed', 'no-answer', 'busy', 'failed'):
            call_log.ended_at = timezone.now()

            # Notify admin on missed inbound calls
            if call_log.direction == 'inbound' and status in ('no-answer', 'busy'):
                name = call_log.from_number
                if call_log.lead:
                    name = call_log.lead.contact_name or name
                _notify_admin(
                    f'Missed call from {name}',
                    f'Missed call from {call_log.from_number} ({name}). Status: {status}'
                )

        call_log.save()
    except CallLog.DoesNotExist:
        logger.warning(f'[call_status] No CallLog for sid={call_sid}')

    return HttpResponse('OK')


@csrf_exempt
@require_POST
def transcription_webhook(request):
    """Handle voicemail transcription from SignalWire."""
    call_sid = request.POST.get('CallSid', '')
    transcription = request.POST.get('TranscriptionText', '')
    recording_url = request.POST.get('RecordingUrl', '')

    logger.info(f'[transcription] sid={call_sid} text={transcription[:100]}')

    try:
        call_log = CallLog.objects.get(call_sid=call_sid)
        call_log.voicemail_transcription = transcription
        if recording_url:
            call_log.recording_url = recording_url
        call_log.disposition = 'left_voicemail'
        call_log.save()

        name = call_log.from_number
        if call_log.lead:
            name = call_log.lead.contact_name or name

        _notify_admin(
            f'Voicemail from {name}',
            f'Voicemail from {call_log.from_number} ({name}):\n\n{transcription}\n\nRecording: {recording_url}'
        )
    except CallLog.DoesNotExist:
        pass

    return HttpResponse('OK')


# ═══════════════════════════════════════════════════════════════════════
# API ENDPOINTS (staff actions)
# ═══════════════════════════════════════════════════════════════════════

@staff_member_required
@require_POST
def api_send_sms(request):
    """POST /api/sms/send/ — Send SMS from dashboard."""
    data = json.loads(request.body)
    to = data.get('to', '').strip()
    message = data.get('message', '').strip()
    media_url = data.get('media_url', '')
    lead_id = data.get('lead_id')

    if not to or not message:
        return JsonResponse({'error': 'to and message required'}, status=400)

    result = signalwire_service.send_sms(to, message, media_url=media_url or None)

    if result.get('ok'):
        lead = Lead.objects.filter(pk=lead_id).first() if lead_id else None
        sp = getattr(request.user, 'salesperson_profile', None)

        SMSMessage.objects.create(
            message_sid=result.get('sid', ''),
            direction='outbound',
            from_number=signalwire_service._from_number(),
            to_number=to,
            body=message,
            media_url=media_url or '',
            status=result.get('status', 'sent'),
            lead=lead,
            salesperson=sp,
        )
        return JsonResponse({'ok': True, 'sid': result['sid']})

    return JsonResponse({'ok': False, 'error': result.get('error', 'Unknown error')}, status=500)


@staff_member_required
@require_POST
def api_send_bulk_sms(request):
    """POST /api/sms/send-bulk/ — Send SMS to multiple leads."""
    data = json.loads(request.body)
    lead_ids = data.get('lead_ids', [])
    template = data.get('template', '')

    if not lead_ids or not template:
        return JsonResponse({'error': 'lead_ids and template required'}, status=400)

    leads = Lead.objects.filter(pk__in=lead_ids, contact_phone__gt='')
    sp = getattr(request.user, 'salesperson_profile', None)
    sent = 0
    failed = 0

    for lead in leads:
        msg = template.replace('{name}', lead.contact_name or '')
        msg = msg.replace('{business}', lead.contact_business or '')
        msg = msg.replace('{city}', lead.detected_location or '')
        msg = msg.replace('{service}', lead.detected_service_type.name if lead.detected_service_type else '')

        result = signalwire_service.send_sms(lead.contact_phone, msg)
        if result.get('ok'):
            SMSMessage.objects.create(
                message_sid=result.get('sid', ''),
                direction='outbound',
                from_number=signalwire_service._from_number(),
                to_number=lead.contact_phone,
                body=msg,
                status='sent',
                lead=lead,
                salesperson=sp,
            )
            sent += 1
        else:
            failed += 1

    return JsonResponse({'ok': True, 'sent': sent, 'failed': failed})


@staff_member_required
@require_POST
def api_call_disposition(request, call_id):
    """POST /api/calls/<id>/disposition/ — Set call disposition and notes."""
    data = json.loads(request.body)
    call_log = get_object_or_404(CallLog, pk=call_id)
    call_log.disposition = data.get('disposition', '')
    call_log.notes = data.get('notes', '')
    call_log.save(update_fields=['disposition', 'notes'])
    return JsonResponse({'ok': True})


@staff_member_required
def api_sms_thread(request, phone):
    """GET /api/sms/thread/<phone>/ — Get SMS conversation thread."""
    messages = SMSMessage.objects.filter(
        Q(from_number=phone) | Q(to_number=phone)
    ).order_by('sent_at')

    # Mark inbound as read
    messages.filter(direction='inbound', read=False).update(read=True)

    thread = [{
        'id': m.id,
        'direction': m.direction,
        'from_number': m.from_number,
        'to_number': m.to_number,
        'body': m.body,
        'status': m.status,
        'sent_at': m.sent_at.isoformat(),
        'is_yes': m.is_yes_response,
        'is_opt_out': m.is_opt_out,
        'lead_id': m.lead_id,
    } for m in messages]

    return JsonResponse({'ok': True, 'messages': thread})


@staff_member_required
@require_POST
def api_sms_reply(request):
    """POST /api/sms/reply/ — Reply to an SMS thread."""
    data = json.loads(request.body)
    to = data.get('to', '').strip()
    body = data.get('body', '').strip()

    if not to or not body:
        return JsonResponse({'error': 'to and body required'}, status=400)

    result = signalwire_service.send_sms(to, body)
    if result.get('ok'):
        lead = _find_lead_by_phone(to)
        sp = getattr(request.user, 'salesperson_profile', None)
        SMSMessage.objects.create(
            message_sid=result.get('sid', ''),
            direction='outbound',
            from_number=signalwire_service._from_number(),
            to_number=to,
            body=body,
            status='sent',
            lead=lead,
            salesperson=sp,
        )
        return JsonResponse({'ok': True, 'sid': result['sid']})
    return JsonResponse({'ok': False, 'error': result.get('error')}, status=500)


# ═══════════════════════════════════════════════════════════════════════
# DASHBOARD PAGES
# ═══════════════════════════════════════════════════════════════════════

@login_required
@ensure_csrf_cookie
def sms_inbox(request):
    """SMS inbox — threaded conversations."""
    return render(request, 'call_center/sms_inbox.html')


@login_required
def sms_inbox_api(request):
    """JSON API for SMS inbox conversations list."""
    filter_type = request.GET.get('filter', 'all')

    # Get distinct conversations (by phone number)
    from django.db.models import Max, Subquery, OuterRef

    our_number = getattr(settings, 'SIGNALWIRE_PHONE_NUMBER', '')

    # Get the "other" phone number for each message
    conversations_qs = SMSMessage.objects.exclude(
        from_number=our_number, to_number=our_number
    )

    if filter_type == 'unread':
        conversations_qs = conversations_qs.filter(direction='inbound', read=False)
    elif filter_type == 'yes':
        conversations_qs = conversations_qs.filter(is_yes_response=True)
    elif filter_type == 'optout':
        conversations_qs = conversations_qs.filter(is_opt_out=True)

    # Group by the "other" number
    from django.db.models.functions import Greatest
    from django.db.models import Case, When, Value, CharField

    # Get unique conversation partners
    all_msgs = SMSMessage.objects.all()
    phone_set = set()
    for m in all_msgs.values('from_number', 'to_number', 'direction'):
        if m['direction'] == 'inbound':
            phone_set.add(m['from_number'])
        else:
            phone_set.add(m['to_number'])
    phone_set.discard(our_number)

    conversations = []
    for phone in sorted(phone_set):
        thread = SMSMessage.objects.filter(
            Q(from_number=phone) | Q(to_number=phone)
        )

        if filter_type == 'unread' and not thread.filter(direction='inbound', read=False).exists():
            continue
        if filter_type == 'yes' and not thread.filter(is_yes_response=True).exists():
            continue
        if filter_type == 'optout' and not thread.filter(is_opt_out=True).exists():
            continue

        last_msg = thread.order_by('-sent_at').first()
        unread = thread.filter(direction='inbound', read=False).count()
        lead = thread.exclude(lead=None).values_list('lead_id', flat=True).first()

        # Get lead name
        name = phone
        lead_obj = None
        if lead:
            lead_obj = Lead.objects.filter(pk=lead).first()
            if lead_obj:
                name = lead_obj.contact_name or lead_obj.contact_business or phone
        if name == phone:
            pv = _find_prospect_video_by_phone(phone)
            if pv:
                name = pv.prospect_owner_name or pv.prospect_business_name or phone

        conversations.append({
            'phone': phone,
            'name': name,
            'last_message': last_msg.body[:100] if last_msg else '',
            'last_at': last_msg.sent_at.isoformat() if last_msg else '',
            'unread': unread,
            'lead_id': lead,
            'has_yes': thread.filter(is_yes_response=True).exists(),
            'is_opted_out': SMSOptOut.objects.filter(phone_number=phone).exists(),
        })

    # Sort by most recent
    conversations.sort(key=lambda c: c['last_at'], reverse=True)

    return JsonResponse({'ok': True, 'conversations': conversations})


@login_required
@ensure_csrf_cookie
def softphone(request):
    """Browser softphone page."""
    return render(request, 'call_center/softphone.html')


@login_required
@ensure_csrf_cookie
def call_center_dashboard(request):
    """Call center admin dashboard."""
    today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    stats = {
        'calls_today': CallLog.objects.filter(started_at__gte=today).count(),
        'calls_answered': CallLog.objects.filter(started_at__gte=today, status='completed').count(),
        'avg_duration': CallLog.objects.filter(started_at__gte=today, status='completed').aggregate(avg=Avg('duration'))['avg'] or 0,
        'sms_sent': SMSMessage.objects.filter(sent_at__gte=today, direction='outbound').count(),
        'yes_responses': SMSMessage.objects.filter(sent_at__gte=today, is_yes_response=True).count(),
        'appointments': CallLog.objects.filter(started_at__gte=today, disposition='appointment_booked').count(),
    }

    recent_calls = CallLog.objects.select_related('lead', 'salesperson').order_by('-started_at')[:25]
    recent_sms = SMSMessage.objects.select_related('lead', 'salesperson').filter(direction='inbound').order_by('-sent_at')[:25]

    return render(request, 'call_center/dashboard.html', {
        'stats': stats,
        'recent_calls': recent_calls,
        'recent_sms': recent_sms,
    })


@login_required
def my_calls(request):
    """Individual rep's call history and stats."""
    sp = getattr(request.user, 'salesperson_profile', None)
    today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if sp:
        calls = CallLog.objects.filter(salesperson=sp).order_by('-started_at')[:50]
        sms_list = SMSMessage.objects.filter(salesperson=sp).order_by('-sent_at')[:50]
        stats = {
            'calls_today': CallLog.objects.filter(salesperson=sp, started_at__gte=today).count(),
            'calls_answered': CallLog.objects.filter(salesperson=sp, started_at__gte=today, status='completed').count(),
            'sms_sent': SMSMessage.objects.filter(salesperson=sp, sent_at__gte=today, direction='outbound').count(),
            'appointments': CallLog.objects.filter(salesperson=sp, started_at__gte=today, disposition='appointment_booked').count(),
        }
    else:
        calls = CallLog.objects.order_by('-started_at')[:50]
        sms_list = SMSMessage.objects.order_by('-sent_at')[:50]
        stats = {
            'calls_today': CallLog.objects.filter(started_at__gte=today).count(),
            'calls_answered': CallLog.objects.filter(started_at__gte=today, status='completed').count(),
            'sms_sent': SMSMessage.objects.filter(sent_at__gte=today, direction='outbound').count(),
            'appointments': CallLog.objects.filter(started_at__gte=today, disposition='appointment_booked').count(),
        }

    return render(request, 'call_center/my_calls.html', {
        'calls': calls,
        'sms_list': sms_list,
        'stats': stats,
    })
