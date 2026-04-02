"""
Lead Repository — Command Center + Source Group pages.
Internal tool for staff and salespeople.
"""
import json
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST


def staff_or_salesperson_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if request.user.is_staff or request.user.is_superuser or hasattr(request.user, 'salesperson_profile'):
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden('Access denied.')
    return wrapper

from core.models.leads import Lead, LeadAssignment
from core.models.business import BusinessProfile, ServiceCategory
from core.models.sales import SalesPerson, SalesProspect
from core.services.enrichment_service import enrich_lead

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
        ('building_violations', 'Building Violations'),
        ('hpd_violations', 'HPD Violations'),
        ('ordinance_violations', 'Ordinance Violations'),
        ('ecb_summonses', 'ECB Summonses'),
        ('code_enforcement', 'Code Enforcement'),
        ('code_complaints', 'Code Complaints'),
        ('permits', 'Permits (BIS)'),
        ('permits_now', 'Permits (NOW)'),
        ('building_permits', 'Building Permits'),
        ('construction_permits', 'Construction Permits'),
        ('electrical_permits', 'Electrical Permits'),
        ('trade_permits', 'Trade Permits'),
        ('boiler_permits', 'Boiler Permits'),
        ('mc_permits', 'MC Permits'),
        ('certificate_of_occupancy', 'Certificate of Occupancy'),
        ('permit_contacts', 'Permit Contacts'),
        ('fire_violations', 'Fire Violations'),
        ('housing_violations', 'Housing Violations'),
        ('property_sales', 'Property Sales'),
        ('health_inspections', 'Health Inspections'),
        ('food_inspections', 'Food Inspections'),
        ('pool_inspections', 'Pool Inspections'),
        ('repeat_offender_violations', 'Repeat Offenders'),
        ('environmental_violations', 'Environmental Violations'),
        ('environmental_remediation', 'Environmental Remediation'),
        ('storage_tanks', 'Storage Tanks'),
        ('liquor_licenses', 'Liquor Licenses'),
        ('alcohol_violations', 'Alcohol Violations'),
        ('liquor_suspensions', 'Liquor Suspensions'),
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
        'contact_email': lead.contact_email,
        'contact_business': lead.contact_business,
        'enrichment_status': lead.enrichment_status,
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

    contact_status = request.GET.get('contact_status')
    if contact_status == 'has_phone':
        qs = qs.exclude(contact_phone='')
    elif contact_status == 'needs_enrichment':
        qs = qs.filter(contact_phone='', enrichment_status='not_enriched')
    elif contact_status == 'enriched':
        qs = qs.filter(enrichment_status='enriched')
    elif contact_status == 'enrichment_failed':
        qs = qs.filter(enrichment_status='enrichment_failed')

    return qs


# -------------------------------------------------------------------
# Command Center (main /admin-leads/ page)
# -------------------------------------------------------------------

@staff_or_salesperson_required
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

    # State filter — applies to all counts
    current_state = request.GET.get('state', '')
    unreviewed = Lead.objects.filter(review_status='unreviewed')
    if current_state:
        unreviewed = unreviewed.filter(state=current_state)

    # Urgency counts (filtered by state)
    urgency_counts = dict(
        unreviewed.values_list('urgency_level')
        .annotate(c=Count('id'))
        .values_list('urgency_level', 'c')
    )

    # Source group overview with sub-type counts (filtered by state)
    source_overview = []
    for group_key, group_label in [('public_records', 'Public Records'),
                                     ('social_media', 'Social Media'),
                                     ('reviews', 'Reviews')]:
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
        'current_state': current_state,
        'hot_count': urgency_counts.get('hot', 0),
        'warm_count': urgency_counts.get('warm', 0),
        'new_count': urgency_counts.get('new', 0),
    })


# -------------------------------------------------------------------
# Source Group Pages (/admin-leads/<group>/)
# -------------------------------------------------------------------

@staff_or_salesperson_required
@ensure_csrf_cookie
def source_group_page(request, group):
    """Source group page with sub-tabs for each source type."""
    group_key = GROUP_SLUG_MAP.get(group)
    if not group_key:
        from django.http import Http404
        raise Http404(f'Unknown source group: {group}')

    group_label = GROUP_DISPLAY.get(group_key, group_key)
    source_types = GROUP_SOURCE_TYPES.get(group_key, [])

    # State filter
    current_state = request.GET.get('state', '')
    unreviewed = Lead.objects.filter(review_status='unreviewed', source_group=group_key)
    if current_state:
        unreviewed = unreviewed.filter(state=current_state)

    # Get counts per type (filtered by state)
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

    # Source group navigation counts (filtered by state)
    all_unreviewed = Lead.objects.filter(review_status='unreviewed')
    if current_state:
        all_unreviewed = all_unreviewed.filter(state=current_state)
    source_nav = []
    for gk, gl in [('public_records', 'Public Records'),
                    ('social_media', 'Social Media'),
                    ('reviews', 'Reviews')]:
        source_nav.append({
            'key': gk,
            'slug': gk.replace('_', '-'),
            'label': gl,
            'icon': GROUP_ICONS.get(gk, 'bi-folder'),
            'total': all_unreviewed.filter(source_group=gk).count(),
        })

    # Available states
    states = Lead.objects.exclude(state='').values_list('state', flat=True).distinct().order_by('state')

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
        'source_nav': source_nav,
        'states': list(states),
        'current_state': current_state,
    })


# -------------------------------------------------------------------
# JSON API (serves both Command Center and Source Group pages)
# -------------------------------------------------------------------

@staff_or_salesperson_required
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

@staff_or_salesperson_required
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

@staff_or_salesperson_required
@require_POST
def lead_action(request, lead_id):
    """Handle single lead actions: approve, reject, assign, delete, etc."""
    lead = get_object_or_404(Lead, id=lead_id)
    data = json.loads(request.body)
    action = data.get('action')

    def _trigger_lead_workflow(lead_obj, from_status, to_status):
        try:
            from core.services.workflow_engine import trigger_workflow
            trigger_workflow('lead_status_changed', {
                'model': 'Lead',
                'lead_id': lead_obj.id,
                'id': lead_obj.id,
                'from_status': from_status,
                'to_status': to_status,
                'name': lead_obj.contact_name,
                'phone': lead_obj.contact_phone,
                'email': lead_obj.contact_email,
                'business_name': lead_obj.contact_business,
            })
        except Exception:
            pass

    if action == 'approve':
        old = lead.review_status
        lead.review_status = 'approved'
        lead.save(update_fields=['review_status'])
        _trigger_lead_workflow(lead, old, 'approved')
        return JsonResponse({'ok': True, 'review_status': 'approved'})

    elif action == 'reject':
        old = lead.review_status
        lead.review_status = 'rejected'
        lead.save(update_fields=['review_status'])
        _trigger_lead_workflow(lead, old, 'rejected')
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

    elif action == 'enrich':
        result = enrich_lead(lead)
        lead.refresh_from_db()
        return JsonResponse({
            'ok': True,
            'result': result,
            'contact_phone': lead.contact_phone,
            'contact_email': lead.contact_email,
            'contact_name': lead.contact_name,
            'contact_business': lead.contact_business,
            'enrichment_status': lead.enrichment_status,
        })

    elif action == 'delete':
        lead_id = lead.id
        lead.delete()
        return JsonResponse({'ok': True, 'deleted_id': lead_id})

    return JsonResponse({'error': 'Unknown action'}, status=400)


@staff_or_salesperson_required
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

    elif action == 'enrich':
        # Enrich leads one by one, return summary
        found = 0
        not_found = 0
        skipped = 0
        for lead in leads:
            result = enrich_lead(lead)
            if result.get('skipped'):
                skipped += 1
            elif result.get('found'):
                found += 1
            else:
                not_found += 1
        return JsonResponse({
            'ok': True, 'count': count,
            'found': found, 'not_found': not_found, 'skipped': skipped,
        })

    elif action == 'delete':
        deleted_count, _ = leads.delete()
        return JsonResponse({'ok': True, 'deleted': deleted_count})

    return JsonResponse({'error': 'Unknown action'}, status=400)


@staff_or_salesperson_required
@require_POST
def lead_delete_all(request):
    """Delete ALL leads from the database. Requires confirmation token."""
    data = json.loads(request.body)
    confirm = data.get('confirm')
    if confirm != 'DELETE_ALL_LEADS':
        return JsonResponse({'error': 'Confirmation required'}, status=400)
    deleted_count, _ = Lead.objects.all().delete()
    return JsonResponse({'ok': True, 'deleted': deleted_count})


# ─── Customer Accounts ────────────────────────────────────────────────

@staff_or_salesperson_required
def customer_accounts(request):
    """List all customer business profiles for admin management."""
    from core.models.business import BusinessProfile
    from core.models.leads import LeadAssignment

    profiles = BusinessProfile.objects.select_related('user').order_by('-created_at')

    accounts = []
    for p in profiles:
        lead_count = LeadAssignment.objects.filter(business=p).count()
        won_count = LeadAssignment.objects.filter(business=p, status='won').count()
        accounts.append({
            'profile': p,
            'lead_count': lead_count,
            'won_count': won_count,
        })

    return render(request, 'admin_leads/customer_accounts.html', {
        'accounts': accounts,
    })


# ─── Mission Control ──────────────────────────────────────────────────

@staff_or_salesperson_required
def mission_control(request):
    """Dashboard showing all monitor health and run history, grouped by category."""
    from core.models.monitoring import MonitorRun
    from core.utils.monitors.schedule import MONITOR_SCHEDULE, MONITOR_GROUPS
    from datetime import timedelta
    from collections import OrderedDict

    monitors = []
    for entry in MONITOR_SCHEDULE:
        cmd_name, kwargs, freq_hours, description = entry[0], entry[1], entry[2], entry[3]
        group = entry[4] if len(entry) > 4 else 'other'

        key = f"{cmd_name}_{'_'.join(str(v) for v in kwargs.values())}"
        last_run = MonitorRun.objects.filter(monitor_name=key).order_by('-started_at').first()

        is_overdue = False
        if last_run and last_run.finished_at:
            is_overdue = last_run.finished_at < timezone.now() - timedelta(hours=freq_hours)

        if last_run and last_run.status in ('failed',):
            status = 'error'
        elif is_overdue or not last_run:
            status = 'overdue' if last_run else 'never'
        else:
            status = 'healthy'

        monitors.append({
            'key': key,
            'description': description,
            'command': cmd_name,
            'frequency_hours': freq_hours,
            'last_run': last_run,
            'is_overdue': is_overdue,
            'status': status,
            'group': group,
        })

    # Build grouped monitor dict preserving MONITOR_GROUPS order
    grouped_monitors = OrderedDict()
    for group_key, group_meta in MONITOR_GROUPS.items():
        group_items = [m for m in monitors if m['group'] == group_key]
        if group_items:
            grouped_monitors[group_key] = {
                'label': group_meta['label'],
                'icon': group_meta['icon'],
                'color': group_meta['color'],
                'monitors': group_items,
                'healthy': sum(1 for m in group_items if m['status'] == 'healthy'),
                'total': len(group_items),
            }

    recent_runs = MonitorRun.objects.all()[:30]
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    leads_today = MonitorRun.objects.filter(
        started_at__gte=today_start, status='success',
    ).values_list('leads_created', flat=True)

    return render(request, 'admin_leads/mission_control.html', {
        'monitors': monitors,
        'grouped_monitors': grouped_monitors,
        'recent_runs': recent_runs,
        'total_healthy': sum(1 for m in monitors if m['status'] == 'healthy'),
        'total_overdue': sum(1 for m in monitors if m['status'] in ('overdue', 'never')),
        'total_error': sum(1 for m in monitors if m['status'] == 'error'),
        'total_monitors': len(monitors),
        'leads_today': sum(leads_today),
    })


@staff_or_salesperson_required
@require_POST
def run_monitor_now(request):
    """Trigger a single monitor via AJAX."""
    from django.core.management import call_command as django_call_command
    from core.models.monitoring import MonitorRun
    from core.utils.monitors.schedule import MONITOR_SCHEDULE

    data = json.loads(request.body)
    target_key = data.get('monitor_key', '')

    for entry in MONITOR_SCHEDULE:
        cmd_name, kwargs, freq_hours, description = entry[0], entry[1], entry[2], entry[3]
        key = f"{cmd_name}_{'_'.join(str(v) for v in kwargs.values())}"
        if key == target_key:
            run = MonitorRun.objects.create(
                monitor_name=key,
                details={'description': description, 'command': cmd_name, 'kwargs': kwargs},
            )
            try:
                django_call_command(cmd_name, **kwargs)
                run.finish(status='success')
                return JsonResponse({'ok': True, 'status': 'success', 'leads': run.leads_created})
            except Exception as e:
                run.finish(status='failed', error_message=str(e))
                return JsonResponse({'ok': False, 'error': str(e)[:200]})

    return JsonResponse({'ok': False, 'error': 'Monitor not found'})


# ─── Agent Missions ───────────────────────────────────────────────────

@staff_or_salesperson_required
@require_POST
def launch_agent(request):
    """Launch an AI agent via AJAX."""
    import threading
    data = json.loads(request.body)
    goal = data.get('goal', '').strip()
    agent_name = data.get('agent', 'orchestrator')

    if not goal:
        return JsonResponse({'ok': False, 'error': 'Goal required'})

    from core.models.leads import AgentMission

    mission = AgentMission.objects.create(
        agent_name=agent_name, goal=goal, status='queued',
        triggered_by=f'web:{request.user.username}',
    )

    def _run():
        from core.agents import get_agent
        mission.status = 'running'
        mission.started_at = timezone.now()
        mission.save(update_fields=['status', 'started_at'])
        try:
            agent = get_agent(agent_name)
            result = agent.run(goal, mission_id=mission.id)
            mission.status = 'complete'
            mission.result = result or ''
            mission.mission_log = agent.mission_log
            mission.steps_taken = len(agent.mission_log)
            mission.completed_at = timezone.now()
            mission.save()
        except Exception as e:
            mission.status = 'error'
            mission.result = str(e)
            mission.completed_at = timezone.now()
            mission.save()

    threading.Thread(target=_run, daemon=True).start()
    return JsonResponse({'ok': True, 'mission_id': mission.id})


@staff_or_salesperson_required
def agent_mission_status(request, mission_id):
    """Check status of an agent mission."""
    from core.models.leads import AgentMission
    m = AgentMission.objects.filter(id=mission_id).first()
    if not m:
        return JsonResponse({'error': 'Not found'}, status=404)
    return JsonResponse({
        'id': m.id, 'agent': m.agent_name, 'status': m.status,
        'result': m.result[:3000] if m.result else '',
        'steps': m.steps_taken, 'leads': m.leads_found,
    })
