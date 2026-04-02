import json

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count
from datetime import timedelta

from core.models import LeadAssignment, Lead


URGENCY_COLORS = {
    'hot': '#FF4757',
    'warm': '#F59E0B',
    'new': '#3B82F6',
    'stale': '#6B6B80',
}

STATUS_COLORS = {
    'won': '#10B981',
    'lost': '#6B6B80',
}


@login_required
def territory_map(request):
    profile = request.user.business_profile

    context = {
        'profile': profile,
        'center_lat': 40.7128,
        'center_lng': -74.0060,
    }
    return render(request, 'territory/map.html', context)


@login_required
def territory_data(request):
    """AJAX endpoint: return lead pins as GeoJSON for the territory map."""
    profile = request.user.business_profile

    # Filters
    days = int(request.GET.get('days', 30))
    platform = request.GET.get('platform', '')
    urgency = request.GET.get('urgency', '')

    cutoff = timezone.now() - timedelta(days=days)

    assignments = LeadAssignment.objects.filter(
        business=profile,
        created_at__gte=cutoff,
    ).select_related('lead', 'lead__detected_service_type')

    if platform:
        assignments = assignments.filter(lead__platform=platform)
    if urgency:
        assignments = assignments.filter(lead__urgency_level=urgency)

    features = []
    for a in assignments:
        lead = a.lead
        if not lead.latitude or not lead.longitude:
            continue

        # Determine pin color
        if a.status == 'won':
            color = STATUS_COLORS['won']
        elif a.status == 'lost':
            color = STATUS_COLORS['lost']
        else:
            color = URGENCY_COLORS.get(lead.urgency_level, '#3B82F6')

        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [lead.longitude, lead.latitude],
            },
            'properties': {
                'id': a.id,
                'urgency': lead.urgency_level,
                'status': a.status,
                'platform': lead.platform,
                'platform_display': lead.get_platform_display(),
                'content': lead.source_content[:150],
                'location': lead.detected_location,
                'service_type': lead.detected_service_type.name if lead.detected_service_type else '',
                'discovered': lead.discovered_at.isoformat(),
                'color': color,
                'detail_url': f'/leads/{a.id}/',
            },
        })

    geojson = {
        'type': 'FeatureCollection',
        'features': features,
    }

    # Also send heatmap points (all leads with coords, no filtering)
    heat_assignments = LeadAssignment.objects.filter(
        business=profile,
        lead__latitude__isnull=False,
        lead__longitude__isnull=False,
    ).select_related('lead')

    heatmap_points = [
        [a.lead.latitude, a.lead.longitude, 1.0]
        for a in heat_assignments
    ]

    return JsonResponse({
        'geojson': geojson,
        'heatmap': heatmap_points,
        'total': len(features),
    })
