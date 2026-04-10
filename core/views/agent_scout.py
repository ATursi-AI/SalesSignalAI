"""Agent SCOUT — Data Source Discovery Tool."""
import json
import logging
import re
import time

import requests
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.models.data_sources import DatasetCandidate, DatasetRegistry

logger = logging.getLogger('agents')

PORTALS = {
    'NYC': 'data.cityofnewyork.us',
    'NY': 'data.ny.gov',
    'CA': 'data.ca.gov',
    'TX': 'data.texas.gov',
    'FL': 'data.florida.gov',
    'IL': 'data.illinois.gov',
    'PA': 'data.pa.gov',
    'OH': 'data.ohio.gov',
    'GA': 'data.georgia.gov',
    'NC': 'data.nc.gov',
    'MI': 'data.michigan.gov',
    'WA': 'data.wa.gov',
    'CO': 'data.colorado.gov',
    'AZ': 'data.az.gov',
    'NV': 'data.nv.gov',
}

PHONE_PATTERNS = ['phone', 'tel', 'mobile', 'contact_phone', 'owner_phone', 'business_phone', 'fax']
NAME_PATTERNS = ['name', 'owner', 'dba', 'business_name', 'respondent', 'applicant', 'corp_name', 'legalname', 'licensee', 'operator']
DATE_PATTERNS = ['date', 'issued', 'filed', 'inspection', 'created', 'received', 'recorded']
EMAIL_PATTERNS = ['email', 'e_mail', 'contact_email']

SEARCH_QUERIES = ['violations', 'permits', 'inspections', 'licenses', 'code enforcement', 'business filings']


def _matches_pattern(field_name, patterns):
    fn = field_name.lower()
    return any(p in fn for p in patterns)


def _analyze_columns(columns):
    """Analyze columns for contact fields."""
    phone_fields = []
    name_fields = []
    date_fields = []
    email_fields = []
    all_fields = []

    for col in columns:
        fn = col.get('fieldName', '')
        if fn.startswith(':'):
            continue
        all_fields.append(fn)
        if _matches_pattern(fn, PHONE_PATTERNS):
            phone_fields.append(fn)
        if _matches_pattern(fn, NAME_PATTERNS):
            name_fields.append(fn)
        if _matches_pattern(fn, DATE_PATTERNS):
            date_fields.append(fn)
        if _matches_pattern(fn, EMAIL_PATTERNS):
            email_fields.append(fn)

    return {
        'phone_fields': phone_fields,
        'name_fields': name_fields,
        'date_fields': date_fields,
        'email_fields': email_fields,
        'all_fields': all_fields,
        'has_phone': len(phone_fields) > 0,
        'has_name': len(name_fields) > 0,
        'has_date': len(date_fields) > 0,
        'has_email': len(email_fields) > 0,
    }


def _calc_relevance(analysis):
    if analysis['has_phone'] and analysis['has_date']:
        return 'HIGH'
    if analysis['has_name'] and analysis['has_date']:
        return 'MEDIUM'
    if analysis['has_date']:
        return 'LOW'
    return 'LOW'


@staff_member_required
def agent_scout_tool(request):
    return render(request, 'tools/agent_scout.html', {'portals': PORTALS})


@staff_member_required
@require_POST
def agent_scout_api(request):
    """Search a state portal for datasets and analyze them."""
    data = json.loads(request.body)
    state = data.get('state', 'NY')
    keyword = data.get('keyword', '').strip()

    # Get portal(s) — some states have multiple
    portals_to_search = []
    if state in PORTALS:
        portals_to_search.append((state, PORTALS[state]))
    if state == 'NY':
        portals_to_search.append(('NYC', PORTALS['NYC']))

    results = []
    total_found = 0
    total_phone = 0
    total_name = 0

    for state_code, portal in portals_to_search:
        queries = [keyword] if keyword else SEARCH_QUERIES
        seen_ids = set()

        for query in queries:
            try:
                url = f'https://{portal}/api/catalog/v1'
                resp = requests.get(url, params={'q': query, 'limit': 30}, timeout=20)
                if resp.status_code != 200:
                    continue
                catalog = resp.json()
                items = catalog.get('results', [])
            except Exception as e:
                logger.warning(f'Scout catalog search failed for {portal}/{query}: {e}')
                continue

            for item in items:
                resource = item.get('resource', {})
                did = resource.get('id', '')
                if not did or did in seen_ids:
                    continue
                seen_ids.add(did)

                name = resource.get('name', '')
                desc = (resource.get('description', '') or '')[:500]
                rtype = resource.get('type', '')
                if rtype not in ('dataset', ''):
                    continue

                # Check if already in registry or candidates
                if DatasetRegistry.objects.filter(portal_domain=portal, dataset_id=did).exists():
                    continue
                if DatasetCandidate.objects.filter(portal_domain=portal, dataset_id=did).exists():
                    continue

                # Fetch metadata for column analysis
                time.sleep(0.5)
                try:
                    meta_resp = requests.get(f'https://{portal}/api/views/{did}.json', timeout=15)
                    if meta_resp.status_code != 200:
                        continue
                    meta = meta_resp.json()
                    columns = meta.get('columns', [])
                except Exception:
                    columns = []

                analysis = _analyze_columns(columns)
                relevance = _calc_relevance(analysis)

                # Determine data type from name/description
                text = (name + ' ' + desc).lower()
                if any(w in text for w in ['violation', 'enforcement', 'complaint']):
                    data_type = 'violations'
                elif any(w in text for w in ['permit', 'building permit', 'construction']):
                    data_type = 'permits'
                elif any(w in text for w in ['inspection', 'health', 'food']):
                    data_type = 'health_inspections'
                elif any(w in text for w in ['license', 'licensing', 'contractor']):
                    data_type = 'contractor_licenses'
                elif any(w in text for w in ['business', 'filing', 'corporation']):
                    data_type = 'business_filings'
                else:
                    data_type = 'other'

                row_count = resource.get('page_views', {}).get('page_views_total', 0)

                # Save candidate
                candidate = DatasetCandidate.objects.create(
                    name=name[:200],
                    portal_domain=portal,
                    dataset_id=did,
                    api_url=f'https://{portal}/resource/{did}.json',
                    state=state if state_code != 'NYC' else 'NY',
                    city='New York City' if state_code == 'NYC' else '',
                    data_type=data_type,
                    description=desc,
                    total_records=row_count,
                    has_phone_field=analysis['has_phone'],
                    has_email_field=analysis['has_email'],
                    has_name_field=analysis['has_name'],
                    contact_fields_found=analysis['phone_fields'] + analysis['name_fields'] + analysis['email_fields'],
                    all_fields=analysis['all_fields'][:50],
                    relevance=relevance,
                    status='new',
                    discovered_by='agent_scout',
                )

                total_found += 1
                if analysis['has_phone']:
                    total_phone += 1
                if analysis['has_name']:
                    total_name += 1

                results.append({
                    'id': candidate.id,
                    'name': name[:100],
                    'description': desc[:200],
                    'portal': portal,
                    'dataset_id': did,
                    'data_type': data_type,
                    'records': row_count,
                    'has_phone': analysis['has_phone'],
                    'has_name': analysis['has_name'],
                    'has_date': analysis['has_date'],
                    'has_email': analysis['has_email'],
                    'phone_fields': analysis['phone_fields'],
                    'name_fields': analysis['name_fields'],
                    'relevance': relevance,
                })

            time.sleep(1)

    return JsonResponse({
        'ok': True,
        'total_found': total_found,
        'total_phone': total_phone,
        'total_name': total_name,
        'datasets': results,
    })


@staff_member_required
@require_POST
def agent_scout_approve(request):
    """Approve a dataset candidate — create DatasetRegistry entry."""
    data = json.loads(request.body)
    candidate_id = data.get('id')
    candidate = DatasetCandidate.objects.filter(id=candidate_id).first()
    if not candidate:
        return JsonResponse({'ok': False, 'error': 'Not found'}, status=404)

    # Create registry entry
    reg, created = DatasetRegistry.objects.get_or_create(
        portal_domain=candidate.portal_domain,
        dataset_id=candidate.dataset_id,
        defaults={
            'name': candidate.name,
            'state': candidate.state,
            'city': candidate.city,
            'data_type': candidate.data_type,
            'api_url': candidate.api_url,
            'total_records': candidate.total_records,
            'contact_fields': candidate.contact_fields_found,
            'all_fields': candidate.all_fields,
            'notes': f'Discovered by Agent SCOUT. {candidate.description[:200]}',
            'is_active': True,
        },
    )
    candidate.status = 'approved'
    candidate.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'ok': True, 'created': created})


@staff_member_required
@require_POST
def agent_scout_reject(request):
    """Reject a dataset candidate."""
    data = json.loads(request.body)
    candidate_id = data.get('id')
    updated = DatasetCandidate.objects.filter(id=candidate_id).update(status='rejected')
    return JsonResponse({'ok': True, 'updated': updated})
