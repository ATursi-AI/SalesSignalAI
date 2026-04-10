"""Agent SCOUT — Data Source Discovery Tool (background task version)."""
import json
import subprocess
import sys

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.models.data_sources import DatasetCandidate, DatasetRegistry

PORTALS = {
    'NYC': 'data.cityofnewyork.us', 'NY': 'data.ny.gov', 'CA': 'data.ca.gov',
    'TX': 'data.texas.gov', 'FL': 'data.florida.gov', 'IL': 'data.illinois.gov',
    'PA': 'data.pa.gov', 'OH': 'data.ohio.gov', 'GA': 'data.georgia.gov',
    'NC': 'data.nc.gov', 'MI': 'data.michigan.gov', 'WA': 'data.wa.gov',
    'CO': 'data.colorado.gov', 'AZ': 'data.az.gov', 'NV': 'data.nv.gov',
}


@staff_member_required
def agent_scout_tool(request):
    return render(request, 'tools/agent_scout.html', {'portals': PORTALS})


@staff_member_required
@require_POST
def agent_scout_api(request):
    """Launch scout as background subprocess."""
    data = json.loads(request.body)
    state = data.get('state', 'NY')
    keyword = data.get('keyword', '').strip()

    cmd = [sys.executable, 'manage.py', 'scout_datasets', '--state', state]
    if keyword:
        cmd.extend(['--keyword', keyword])

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return JsonResponse({
        'ok': True,
        'message': f'Scout started for {state}. Check results in 1-3 minutes.',
    })


@staff_member_required
def agent_scout_status(request):
    """Return current candidate counts by state + recent candidates."""
    state_filter = request.GET.get('state', '')

    # Counts by state
    counts = {}
    qs = DatasetCandidate.objects.values('state', 'status').annotate(c=Count('id'))
    for row in qs:
        s = row['state'] or 'Unknown'
        if s not in counts:
            counts[s] = {'new': 0, 'approved': 0, 'rejected': 0}
        counts[s][row['status']] = row['c']

    # Recent candidates for display
    candidates_qs = DatasetCandidate.objects.filter(status='new').order_by('-created_at')
    if state_filter:
        candidates_qs = candidates_qs.filter(state=state_filter)

    candidates = []
    for c in candidates_qs[:50]:
        candidates.append({
            'id': c.id,
            'name': c.name[:100],
            'description': (c.description or '')[:200],
            'portal': c.portal_domain,
            'dataset_id': c.dataset_id,
            'data_type': c.data_type,
            'records': c.total_records or 0,
            'has_phone': c.has_phone_field,
            'has_name': c.has_name_field,
            'has_email': c.has_email_field,
            'phone_fields': [f for f in (c.contact_fields_found or []) if any(p in f.lower() for p in ['phone', 'tel', 'mobile'])],
            'relevance': c.relevance,
            'state': c.state,
        })

    return JsonResponse({
        'ok': True,
        'counts': counts,
        'candidates': candidates,
        'total_new': DatasetCandidate.objects.filter(status='new').count(),
    })


@staff_member_required
@require_POST
def agent_scout_approve(request):
    """Approve a dataset candidate."""
    data = json.loads(request.body)
    candidate = DatasetCandidate.objects.filter(id=data.get('id')).first()
    if not candidate:
        return JsonResponse({'ok': False, 'error': 'Not found'}, status=404)

    DatasetRegistry.objects.get_or_create(
        portal_domain=candidate.portal_domain,
        dataset_id=candidate.dataset_id,
        defaults={
            'name': candidate.name, 'state': candidate.state, 'city': candidate.city,
            'data_type': candidate.data_type, 'api_url': candidate.api_url,
            'total_records': candidate.total_records,
            'contact_fields': candidate.contact_fields_found,
            'all_fields': candidate.all_fields,
            'notes': f'Discovered by Agent SCOUT. {(candidate.description or "")[:200]}',
            'is_active': True,
        },
    )
    candidate.status = 'approved'
    candidate.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'ok': True})


@staff_member_required
@require_POST
def agent_scout_reject(request):
    """Reject a dataset candidate."""
    data = json.loads(request.body)
    DatasetCandidate.objects.filter(id=data.get('id')).update(status='rejected')
    return JsonResponse({'ok': True})
