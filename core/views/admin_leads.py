"""
Lead Repository — Command Center + Source Group pages.
Internal staff tool for viewing, filtering, and managing ALL incoming leads.
Protected: staff/superuser only.
"""
import json
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from core.models.leads import Lead, LeadAssignment
from core.models.business import BusinessProfile, ServiceCategory
from core.models.sales import SalesPerson, SalesProspect

# Source group slug -> model value mapping
GROUP_SLUG_MAP = {
    'public-records': 'public_records',
    'social-media': 'social_media',
    'reviews': 'reviews',
    'weather': 'weather',
}

# Source group -> list of source_types in that group
GROUP_SOURCE_TYPES = {
    'public_records': [
        ('violations', 'Violations'),
        ('permits', 'Permits (BIS)'),
        ('permits_now', 'Permits (NOW)'),
        ('property_sales', 'Property Sales'),
        ('health_inspections', 'Health Inspections'),
        ('liquor_licenses', 'Liquor Licenses'),
        ('business_filings', 'Business Filings'),
    ],
    'social_media': [
        ('reddit', 'Reddit'),
        ('nextdoor', 'Nextdoor'),
        ('facebook', 'Facebook'),
    ],
    'reviews': [
        ('google_reviews', 'Google Reviews'),
        ('no_website', 'No Website'),
        ('google_qa', 'Google Q&A'),
    ],
    'weather': [
        ('noaa', 'NOAA Alerts'),
    ],
}

GROUP_DISPLAY = {
    'public_records': 'Public Records',
    'social_media': 'Social Media',
    'reviews': 'Reviews',
    'weather': 'Weather',
}

GROUP_ICONS = {
    'public_records': 'bi-file-earmark-text',
    'social_media': 'bi-chat-dots',
    'reviews': 'bi-star',
    'weather': 'bi-cloud-lightning',
}


def _time_ago(dt, now=None):
    """Return human-readable time delta string."""
    if not dt:
        return ''
    if not now:
        now = timezone.now()
    seconds = int((now - dt).total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    elif seconds < 3600:
        return f"{seconds // 60}m ago"
    elif seconds < 86400:
        return f"{seconds // 3600}h ago"
    else:
        return f"{seconds // 86400}d ago"


def _serialize_lead(lead, now, platform_display):
    """Serialize a lead for the JSON API."""
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
    if '\n' in title:
        title = title.split('\n')[0]

    return {
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
        'time_ago': _time_ago(lead.event_date or lead.discovered_at, now),
        'discovered_at': lead.discovered_at.isoformat(),
        'event_date': lead.event_date.isoformat() if lead.event_date else None,
        'author': lead.source_author or '',
        'review_status': lead.review_status,
        'matched_keywords': lead.matched_keywords or [],
        'assignments': assignments,
        'source_group': lead.source_group,
        'source_type': lead.source_type,
        'state': lead.state,
        'region': lead.region,
        'contact_name': lead.contact_name,
        'contact_phone': lead.contact_phone,
        'contact_business': lead.contact_business,
    }


def _apply_filters(qs, request):
    """Apply common filters from query params."""
    platform = request.GET.get('platform')
    category = request.GET.get('category')
    confidence = request.GET.get('confidence')
    urgency = request.GET.get('urgency')
    status = request.GET.get('status')
    location = request.GET.get('location', '').strip()
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    search = request.GET.get('search', '').strip()
    state = request.GET.get('state')
    source_type = request.GET.get('type')
    source_group = request.GET.get('group')

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
            Q(detected_zip__icontains=location) |
            Q(region__icontains=location)
        )
    if date_from:
        qs = qs.filter(discovered_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(discovered_at__date__lte=date_to)
    if search:
        qs = qs.filter(
            Q(source_content__icontains=search) |
            Q(source_author__icontains=search) |
            Q(source_url__icontains=search) |
            Q(contact_name__icontains=search) |
            Q(contact_business__icontains=search)
        )
    if state:
        qs = qs.filter(state=state)
    if source_type:
        qs = qs.filter(source_type=source_type)
    if source_group:
        qs = qs.filter(source_group=source_group)

    return qs


# -------------------------------------------------------------------
# Command Center (main /admin-leads/ page)
# -------------------------------------------------------------------

@staff_member_required
@ensure_csrf_cookie
def lead_repository(request):
    """Command Center — dashboard with urgency cards, source overview, unified feed."""
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
    salespeople = SalesPerson.objects.filter(status='active').order_by('user__first_name')

    # Urgency counts
    unreviewed = Lead.objects.filter(review_status='unreviewed')
    urgency_counts = dict(
        unreviewed.values_list('urgency_level')
        .annotate(c=Count('id'))
        .values_list('urgency_level', 'c')
    )

    # Source group overview with sub-type counts
    source_overview = []
    for group_key, group_label in [('public_records', 'Public Records'),
                                     ('social_media', 'Social Media'),
                                     ('reviews', 'Reviews'),
                                     ('weather', 'Weather')]:
        group_total = unreviewed.filter(source_group=group_key).count()
        sub_types = []
        for type_key, type_label in GROUP_SOURCE_TYPES.get(group_key, []):
            count = unreviewed.filter(source_type=type_key).count()
            sub_types.append({
                'key': type_key,
                'label': type_label,
                'count': count,
            })
        source_overview.append({
            'key': group_key,
            'slug': group_key.replace('_', '-'),
            'label': group_label,
            'icon': GROUP_ICONS.get(group_key, 'bi-folder'),
            'total': group_total,
            'sub_types': sub_types,
        })

    # Available states
    states = (
        Lead.objects
        .exclude(state='')
        .values_list('state', flat=True)
        .distinct()
        .order_by('state')
    )

    return render(request, 'admin_leads/command_center.html', {
        'categories': categories,
        'platforms': sorted(set(platforms)),
        'platform_choices': dict(Lead.PLATFORM_CHOICES),
        'businesses': businesses,
        'salespeople': salespeople,
        'urgency_counts': urgency_counts,
        'source_overview': source_overview,
        'states': list(states),
        'hot_count': urgency_counts.get('hot', 0),
        'warm_count': urgency_counts.get('warm', 0),
        'new_count': urgency_counts.get('new', 0),
    })


# -------------------------------------------------------------------
# Source Group Pages (/admin-leads/<group>/)
# -------------------------------------------------------------------

@staff_member_required
@ensure_csrf_cookie
def source_group_page(request, group):
    """Source group page with sub-tabs for each source type."""
    group_key = GROUP_SLUG_MAP.get(group)
    if not group_key:
        from django.http import Http404
        raise Http404(f'Unknown source group: {group}')

    group_label = GROUP_DISPLAY.get(group_key, group_key)
    source_types = GROUP_SOURCE_TYPES.get(group_key, [])

    # Get counts per type (unreviewed)
    unreviewed = Lead.objects.filter(review_status='unreviewed', source_group=group_key)
    type_counts = dict(
        unreviewed.values_list('source_type')
        .annotate(c=Count('id'))
        .values_list('source_type', 'c')
    )
    type_tabs = []
    total_count = 0
    for type_key, type_label in source_types:
        count = type_counts.get(type_key, 0)
        total_count += count
        type_tabs.append({
            'key': type_key,
            'label': type_label,
            'count': count,
        })

    businesses = BusinessProfile.objects.filter(is_active=True).order_by('business_name')
    salespeople = SalesPerson.objects.filter(status='active').order_by('user__first_name')

    return render(request, 'admin_leads/source_group.html', {
        'group_key': group_key,
        'group_slug': group,
        'group_label': group_label,
        'group_icon': GROUP_ICONS.get(group_key, 'bi-folder'),
        'type_tabs': type_tabs,
        'total_count': total_count,
        'platform_choices': dict(Lead.PLATFORM_CHOICES),
        'businesses': businesses,
        'salespeople': salespeople,
    })


# -------------------------------------------------------------------
# JSON API (serves both Command Center and Source Group pages)
# -------------------------------------------------------------------

@staff_member_required
def lead_repository_api(request):
    """JSON API for fetching filtered leads."""
    qs = Lead.objects.select_related('detected_service_type').prefetch_related(
        'assignments__business'
    )

    qs = _apply_filters(qs, request)

    # Sorting — default to event_date DESC with discovered_at fallback
    sort = request.GET.get('sort', '-event_date')
    allowed_sorts = {
        'event_date', '-event_date',
        'discovered_at', '-discovered_at',
        'urgency_score', '-urgency_score',
        'confidence', '-confidence',
        'platform', '-platform',
    }
    if sort not in allowed_sorts:
        sort = '-event_date'
    # Always add discovered_at as secondary sort for consistent ordering
    if 'event_date' in sort:
        qs = qs.order_by(sort, '-discovered_at')
    else:
        qs = qs.order_by(sort)

    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 50))
    per_page = min(per_page, 100)
    total = qs.count()
    leads = qs[(page - 1) * per_page: page * per_page]

    now = timezone.now()
    platform_display = dict(Lead.PLATFORM_CHOICES)

    results = [_serialize_lead(lead, now, platform_display) for lead in leads]

    return JsonResponse({
        'leads': results,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
    })


# -------------------------------------------------------------------
# Lead Detail API
# -------------------------------------------------------------------

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
        'event_date': lead.event_date.isoformat() if lead.event_date else None,
        'raw_data': lead.raw_data or {},
        'assignments': assignments,
        'state': lead.state,
        'region': lead.region,
        'source_group': lead.source_group,
        'source_type': lead.source_type,
        'contact_name': lead.contact_name,
        'contact_phone': lead.contact_phone,
        'contact_email': lead.contact_email,
        'contact_business': lead.contact_business,
        'contact_address': lead.contact_address,
    })


# -------------------------------------------------------------------
# Lead Actions (single and bulk)
# -------------------------------------------------------------------

@staff_member_required
@require_POST
def lead_action(request, lead_id):
    """Handle single lead actions: approve, reject, assign, delete, etc."""
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
            lead=lead, business=business,
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
        if not lead.assignments.exists():
            lead.review_status = 'approved'
            lead.save(update_fields=['review_status'])
        return JsonResponse({'ok': True, 'review_status': lead.review_status})

    elif action == 'send_to_sales':
        sp_id = data.get('salesperson_id')
        if not sp_id:
            return JsonResponse({'error': 'salesperson_id required'}, status=400)
        sp = get_object_or_404(SalesPerson, id=sp_id)
        if SalesProspect.objects.filter(source_lead_id=lead.id).exists():
            return JsonResponse({'error': 'Already in sales pipeline'}, status=400)
        raw = lead.raw_data or {}
        SalesProspect.objects.create(
            salesperson=sp,
            business_name=lead.contact_business or raw.get('business_name', lead.source_author or 'Unknown'),
            phone=lead.contact_phone or raw.get('phone', ''),
            address=lead.contact_address or raw.get('address', lead.detected_location),
            city=raw.get('city', ''),
            state=lead.state or raw.get('state', ''),
            zip_code=lead.detected_zip or raw.get('zip_code', ''),
            service_category=raw.get('category', ''),
            source='google_maps_scan',
            source_lead_id=lead.id,
            google_rating=raw.get('rating'),
            google_review_count=raw.get('review_count'),
            has_website=raw.get('type') != 'no_website',
            notes=lead.source_content[:500] if lead.source_content else '',
        )
        return JsonResponse({'ok': True, 'message': 'Sent to sales pipeline'})

    elif action == 'delete':
        lead_id = lead.id
        lead.delete()
        return JsonResponse({'ok': True, 'deleted_id': lead_id})

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
                lead=lead, business=business,
            )
            if created:
                created_count += 1
        leads.update(review_status='assigned')
        return JsonResponse({
            'ok': True, 'count': count,
            'assigned': created_count, 'review_status': 'assigned',
        })

    elif action == 'delete':
        deleted_count, _ = leads.delete()
        return JsonResponse({'ok': True, 'deleted': deleted_count})

    return JsonResponse({'error': 'Unknown action'}, status=400)


@staff_member_required
@require_POST
def lead_delete_all(request):
    """Delete ALL leads from the database. Requires confirmation token."""
    data = json.loads(request.body)
    confirm = data.get('confirm')
    if confirm != 'DELETE_ALL_LEADS':
        return JsonResponse({'error': 'Confirmation required'}, status=400)
    deleted_count, _ = Lead.objects.all().delete()
    return JsonResponse({'ok': True, 'deleted': deleted_count})
