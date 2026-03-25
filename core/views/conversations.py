"""Unified Conversations Inbox for customer dashboard."""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone

from core.models import LeadAssignment, Lead


@login_required
def conversations(request):
    """Unified inbox — all leads/contacts with their full interaction history."""
    profile = getattr(request.user, 'business_profile', None)
    if not profile:
        return redirect('sales_today')

    assignments = LeadAssignment.objects.filter(
        business=profile,
    ).select_related('lead').order_by('-updated_at')

    convos = []
    for a in assignments:
        lead = a.lead
        convos.append({
            'id': a.id,
            'lead_id': lead.id,
            'name': lead.contact_name or lead.contact_business or 'Unknown',
            'phone': lead.contact_phone or '',
            'email': lead.contact_email or '',
            'address': lead.contact_address or '',
            'status': a.status,
            'urgency': lead.urgency_level,
            'platform': lead.platform,
            'source_type': lead.get_source_type_display() if lead.source_type else '',
            'last_message': (lead.source_content or '')[:100],
            'updated_at': a.updated_at,
            'unread': a.status == 'new',
        })

    return render(request, 'dashboard/conversations.html', {
        'conversations': convos,
    })


@login_required
def conversation_detail_api(request, assignment_id):
    """Get full conversation thread for a lead via AJAX."""
    profile = getattr(request.user, 'business_profile', None)
    if not profile:
        return JsonResponse({'error': 'No profile'}, status=403)

    try:
        assignment = LeadAssignment.objects.select_related('lead').get(
            id=assignment_id, business=profile,
        )
    except LeadAssignment.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    lead = assignment.lead

    # Build timeline
    timeline = []

    # Lead discovery
    timeline.append({
        'type': 'lead_detected',
        'icon': 'bi-broadcast',
        'color': 'teal',
        'title': f'Lead Detected — {lead.get_source_type_display() if lead.source_type else lead.platform}',
        'content': lead.source_content[:500] if lead.source_content else '',
        'time': lead.discovered_at.isoformat() if lead.discovered_at else '',
    })

    if assignment.alert_sent_at:
        timeline.append({
            'type': 'alert', 'icon': 'bi-bell-fill', 'color': 'amber',
            'title': f'Alert Sent ({assignment.alert_method or "email"})',
            'content': '', 'time': assignment.alert_sent_at.isoformat(),
        })

    if assignment.viewed_at:
        timeline.append({
            'type': 'status', 'icon': 'bi-eye', 'color': 'blue',
            'title': 'Viewed', 'content': '',
            'time': assignment.viewed_at.isoformat(),
        })

    if assignment.contacted_at:
        timeline.append({
            'type': 'status', 'icon': 'bi-telephone-fill', 'color': 'emerald',
            'title': 'Contacted', 'content': '',
            'time': assignment.contacted_at.isoformat(),
        })

    if assignment.notes:
        timeline.append({
            'type': 'note', 'icon': 'bi-journal-text', 'color': 'gray',
            'title': 'Notes', 'content': assignment.notes,
            'time': assignment.updated_at.isoformat(),
        })

    # Enrichment
    if lead.enrichment_status == 'enriched' and lead.enrichment_date:
        timeline.append({
            'type': 'enrichment', 'icon': 'bi-search', 'color': 'purple',
            'title': 'Contact Enriched',
            'content': f'Phone: {lead.contact_phone}, Email: {lead.contact_email}',
            'time': lead.enrichment_date.isoformat(),
        })

    timeline.sort(key=lambda x: x.get('time', ''))

    # Mark as viewed
    if assignment.status == 'new':
        assignment.status = 'viewed'
        assignment.viewed_at = timezone.now()
        assignment.save(update_fields=['status', 'viewed_at'])

    return JsonResponse({
        'id': assignment.id,
        'lead_id': lead.id,
        'name': lead.contact_name or lead.contact_business or 'Unknown',
        'phone': lead.contact_phone or '',
        'email': lead.contact_email or '',
        'address': lead.contact_address or '',
        'business': lead.contact_business or '',
        'status': assignment.status,
        'urgency': lead.urgency_level,
        'platform': lead.platform,
        'source_type': lead.get_source_type_display() if lead.source_type else '',
        'revenue': str(assignment.revenue) if assignment.revenue else '',
        'notes': assignment.notes or '',
        'ai_summary': lead.ai_summary or '',
        'ai_response': lead.ai_suggested_response or '',
        'source_content': lead.source_content or '',
        'timeline': timeline,
    })


@login_required
def conversation_update(request, assignment_id):
    """Update notes/status/revenue on a conversation."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    import json
    profile = getattr(request.user, 'business_profile', None)
    if not profile:
        return JsonResponse({'error': 'No profile'}, status=403)

    try:
        assignment = LeadAssignment.objects.get(id=assignment_id, business=profile)
    except LeadAssignment.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    data = json.loads(request.body)

    if 'notes' in data:
        assignment.notes = data['notes']
    if 'status' in data:
        assignment.status = data['status']
        if data['status'] == 'contacted' and not assignment.contacted_at:
            assignment.contacted_at = timezone.now()
    if 'revenue' in data:
        try:
            assignment.revenue = float(data['revenue']) if data['revenue'] else None
        except (ValueError, TypeError):
            pass

    assignment.save()
    return JsonResponse({'ok': True, 'status': assignment.status})
