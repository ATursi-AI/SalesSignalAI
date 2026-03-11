"""
Lead Repository — internal staff tool for viewing, filtering, and managing
ALL incoming leads before they get assigned to customers.
Protected: staff/superuser only.
"""
import json
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models.leads import Lead, LeadAssignment
from core.models.business import BusinessProfile, ServiceCategory


@staff_member_required
def lead_repository(request):
    """Main lead repository page — renders the template with initial context."""
    categories = (
        ServiceCategory.objects
        .filter(is_active=True, lead__isnull=False)
        .annotate(lead_count=Count('lead'))
        .filter(lead_count__gt=0)
        .order_by('-lead_count')
    )
    platforms = (
        Lead.objects
        .values_list('platform', flat=True)
        .distinct()
        .order_by('platform')
    )
    businesses = BusinessProfile.objects.filter(is_active=True).order_by('business_name')

    return render(request, 'admin_leads/repository.html', {
        'categories': categories,
        'platforms': sorted(set(platforms)),
        'platform_choices': dict(Lead.PLATFORM_CHOICES),
        'businesses': businesses,
    })


@staff_member_required
def lead_repository_api(request):
    """JSON API for fetching filtered leads."""
    qs = Lead.objects.select_related('detected_service_type').prefetch_related(
        'assignments__business'
    )

    # Filters
    platform = request.GET.get('platform')
    category = request.GET.get('category')
    confidence = request.GET.get('confidence')
    urgency = request.GET.get('urgency')
    status = request.GET.get('status')
    location = request.GET.get('location', '').strip()
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    search = request.GET.get('search', '').strip()
    sort = request.GET.get('sort', '-discovered_at')

    if platform:
        qs = qs.filter(platform=platform)
    if category:
        qs = qs.filter(detected_service_type__slug=category)
    if confidence:
        qs = qs.filter(confidence=confidence)
    if urgency:
        qs = qs.filter(urgency_level=urgency)
    if status:
        if status == 'assigned':
            qs = qs.filter(review_status='assigned')
        elif status == 'unassigned':
            qs = qs.filter(assignments__isnull=True).exclude(review_status='rejected')
        else:
            qs = qs.filter(review_status=status)
    if location:
        qs = qs.filter(
            Q(detected_location__icontains=location) |
            Q(detected_zip__icontains=location)
        )
    if date_from:
        qs = qs.filter(discovered_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(discovered_at__date__lte=date_to)
    if search:
        qs = qs.filter(
            Q(source_content__icontains=search) |
            Q(source_author__icontains=search) |
            Q(source_url__icontains=search)
        )

    # Sorting
    allowed_sorts = {
        'discovered_at', '-discovered_at',
        'urgency_score', '-urgency_score',
        'confidence', '-confidence',
        'platform', '-platform',
    }
    if sort not in allowed_sorts:
        sort = '-discovered_at'
    qs = qs.order_by(sort)

    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = 50
    total = qs.count()
    leads = qs[(page - 1) * per_page: page * per_page]

    now = timezone.now()
    platform_display = dict(Lead.PLATFORM_CHOICES)

    results = []
    for lead in leads:
        # Time ago
        delta = now - lead.discovered_at
        seconds = int(delta.total_seconds())
        if seconds < 60:
            time_ago = f"{seconds}s ago"
        elif seconds < 3600:
            time_ago = f"{seconds // 60}m ago"
        elif seconds < 86400:
            time_ago = f"{seconds // 3600}h ago"
        else:
            time_ago = f"{seconds // 86400}d ago"

        # Assignments
        assignments = [
            {
                'id': a.id,
                'business_name': a.business.business_name,
                'business_id': a.business.id,
                'status': a.status,
            }
            for a in lead.assignments.all()
        ]

        title = lead.source_content[:120]
        if '\n' in lead.source_content[:120]:
            title = lead.source_content[:120].split('\n')[0]

        results.append({
            'id': lead.id,
            'platform': lead.platform,
            'platform_display': platform_display.get(lead.platform, lead.platform),
            'title': title,
            'source_url': lead.source_url,
            'category': lead.detected_service_type.name if lead.detected_service_type else 'Uncategorized',
            'category_slug': lead.detected_service_type.slug if lead.detected_service_type else '',
            'location': lead.detected_location or '',
            'confidence': lead.confidence,
            'urgency': lead.urgency_level,
            'time_ago': time_ago,
            'discovered_at': lead.discovered_at.isoformat(),
            'author': lead.source_author or '',
            'review_status': lead.review_status,
            'matched_keywords': lead.matched_keywords or [],
            'assignments': assignments,
        })

    return JsonResponse({
        'leads': results,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
    })


@staff_member_required
def lead_detail_api(request, lead_id):
    """JSON API for fetching full lead detail."""
    lead = get_object_or_404(Lead.objects.select_related('detected_service_type'), id=lead_id)
    assignments = [
        {
            'id': a.id,
            'business_name': a.business.business_name,
            'business_id': a.business.id,
            'status': a.status,
            'created_at': a.created_at.isoformat(),
        }
        for a in lead.assignments.select_related('business').all()
    ]

    return JsonResponse({
        'id': lead.id,
        'platform': lead.platform,
        'platform_display': dict(Lead.PLATFORM_CHOICES).get(lead.platform, lead.platform),
        'source_url': lead.source_url,
        'source_content': lead.source_content,
        'source_author': lead.source_author or '',
        'source_posted_at': lead.source_posted_at.isoformat() if lead.source_posted_at else None,
        'detected_location': lead.detected_location or '',
        'detected_zip': lead.detected_zip or '',
        'category': lead.detected_service_type.name if lead.detected_service_type else 'Uncategorized',
        'category_slug': lead.detected_service_type.slug if lead.detected_service_type else '',
        'matched_keywords': lead.matched_keywords or [],
        'urgency_level': lead.urgency_level,
        'urgency_score': lead.urgency_score,
        'confidence': lead.confidence,
        'review_status': lead.review_status,
        'discovered_at': lead.discovered_at.isoformat(),
        'raw_data': lead.raw_data or {},
        'assignments': assignments,
    })


@staff_member_required
@require_POST
def lead_action(request, lead_id):
    """Handle single lead actions: approve, reject, assign."""
    lead = get_object_or_404(Lead, id=lead_id)
    data = json.loads(request.body)
    action = data.get('action')

    if action == 'approve':
        lead.review_status = 'approved'
        lead.save(update_fields=['review_status'])
        return JsonResponse({'ok': True, 'review_status': 'approved'})

    elif action == 'reject':
        lead.review_status = 'rejected'
        lead.save(update_fields=['review_status'])
        return JsonResponse({'ok': True, 'review_status': 'rejected'})

    elif action == 'unreview':
        lead.review_status = 'unreviewed'
        lead.save(update_fields=['review_status'])
        return JsonResponse({'ok': True, 'review_status': 'unreviewed'})

    elif action == 'assign':
        business_id = data.get('business_id')
        if not business_id:
            return JsonResponse({'error': 'business_id required'}, status=400)
        business = get_object_or_404(BusinessProfile, id=business_id)
        assignment, created = LeadAssignment.objects.get_or_create(
            lead=lead,
            business=business,
        )
        if not created:
            return JsonResponse({'error': 'Already assigned to this business'}, status=400)
        lead.review_status = 'assigned'
        lead.save(update_fields=['review_status'])
        return JsonResponse({
            'ok': True,
            'review_status': 'assigned',
            'assignment': {
                'id': assignment.id,
                'business_name': business.business_name,
                'business_id': business.id,
                'status': assignment.status,
            },
        })

    elif action == 'unassign':
        business_id = data.get('business_id')
        if not business_id:
            return JsonResponse({'error': 'business_id required'}, status=400)
        deleted, _ = LeadAssignment.objects.filter(lead=lead, business_id=business_id).delete()
        if not deleted:
            return JsonResponse({'error': 'Assignment not found'}, status=400)
        # Reset review status if no more assignments
        if not lead.assignments.exists():
            lead.review_status = 'approved'
            lead.save(update_fields=['review_status'])
        return JsonResponse({'ok': True, 'review_status': lead.review_status})

    return JsonResponse({'error': 'Unknown action'}, status=400)


@staff_member_required
@require_POST
def lead_bulk_action(request):
    """Handle bulk actions on multiple leads."""
    data = json.loads(request.body)
    lead_ids = data.get('lead_ids', [])
    action = data.get('action')
    business_id = data.get('business_id')

    if not lead_ids:
        return JsonResponse({'error': 'No leads selected'}, status=400)

    leads = Lead.objects.filter(id__in=lead_ids)
    count = leads.count()

    if action == 'approve':
        leads.update(review_status='approved')
        return JsonResponse({'ok': True, 'count': count, 'review_status': 'approved'})

    elif action == 'reject':
        leads.update(review_status='rejected')
        return JsonResponse({'ok': True, 'count': count, 'review_status': 'rejected'})

    elif action == 'assign':
        if not business_id:
            return JsonResponse({'error': 'business_id required'}, status=400)
        business = get_object_or_404(BusinessProfile, id=business_id)
        created_count = 0
        for lead in leads:
            _, created = LeadAssignment.objects.get_or_create(
                lead=lead,
                business=business,
            )
            if created:
                created_count += 1
        leads.update(review_status='assigned')
        return JsonResponse({
            'ok': True,
            'count': count,
            'assigned': created_count,
            'review_status': 'assigned',
        })

    return JsonResponse({'error': 'Unknown action'}, status=400)
