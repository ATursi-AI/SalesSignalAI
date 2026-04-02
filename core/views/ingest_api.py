"""
Secure API endpoint for ingesting leads from remote monitors.

POST /api/ingest-lead/
Authorization: Bearer <INGEST_API_KEY>

Accepts JSON with lead data, deduplicates by source_url,
and creates Lead records via the standard pipeline.
"""
import hashlib
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models.leads import Lead
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ['platform', 'source_url', 'source_content']

VALID_PLATFORMS = {p[0] for p in Lead.PLATFORM_CHOICES}
VALID_CONFIDENCE = {c[0] for c in Lead.CONFIDENCE_CHOICES}
VALID_URGENCY = {u[0] for u in Lead.URGENCY_CHOICES}


def _authenticate(request):
    """Validate Bearer token against INGEST_API_KEY setting."""
    api_key = settings.INGEST_API_KEY
    if not api_key:
        return False

    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return False

    token = auth_header[7:].strip()
    return token == api_key


@csrf_exempt
@require_POST
def ingest_lead(request):
    """
    Ingest a lead from a remote monitor.

    POST JSON body:
        platform        (required) — e.g. 'reddit', 'google_reviews'
        source_url      (required) — permalink / URL of original content
        source_content  (required) — full text content
        detected_category (optional) — category slug
        detected_location (optional) — location string
        confidence      (optional) — 'high', 'medium', 'low'
        author          (optional) — source author name
        urgency         (optional) — 'hot', 'warm', 'new'
        raw_data        (optional) — arbitrary JSON metadata

    Returns:
        201 — lead created
        409 — duplicate (source_url already exists)
        401 — unauthorized
        400 — bad request
    """
    # Auth check
    if not _authenticate(request):
        return JsonResponse(
            {'error': 'Unauthorized. Provide valid Bearer token.'},
            status=401,
        )

    # Parse body
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON body.'}, status=400)

    # Validate required fields
    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        return JsonResponse(
            {'error': f'Missing required fields: {", ".join(missing)}'},
            status=400,
        )

    platform = data['platform']
    source_url = data['source_url']
    source_content = data['source_content']

    # Validate platform
    if platform not in VALID_PLATFORMS:
        return JsonResponse(
            {'error': f'Invalid platform. Must be one of: {", ".join(sorted(VALID_PLATFORMS))}'},
            status=400,
        )

    # Validate optional enum fields
    confidence = data.get('confidence', '')
    if confidence and confidence not in VALID_CONFIDENCE:
        return JsonResponse(
            {'error': f'Invalid confidence. Must be one of: {", ".join(sorted(VALID_CONFIDENCE))}'},
            status=400,
        )

    urgency = data.get('urgency', '')
    if urgency and urgency not in VALID_URGENCY:
        return JsonResponse(
            {'error': f'Invalid urgency. Must be one of: {", ".join(sorted(VALID_URGENCY))}'},
            status=400,
        )

    # Dedup by content hash (same scheme as process_lead)
    content_hash = hashlib.sha256(
        f'{platform}|{source_url}|{source_content}'.encode()
    ).hexdigest()

    if Lead.objects.filter(content_hash=content_hash).exists():
        return JsonResponse(
            {'status': 'duplicate', 'message': 'Lead already exists.'},
            status=409,
        )

    # Create lead via process_lead pipeline (handles keyword matching,
    # location extraction, urgency calc, and business assignment)
    author = data.get('author', '')
    raw_data = data.get('raw_data', {})
    if not isinstance(raw_data, dict):
        raw_data = {}
    raw_data['ingested_via'] = 'api'

    try:
        lead, created, num_assigned = process_lead(
            platform=platform,
            source_url=source_url,
            content=source_content,
            author=author,
            posted_at=None,
            raw_data=raw_data,
        )
    except Exception as e:
        logger.error(f'[IngestAPI] process_lead failed: {e}')
        return JsonResponse(
            {'error': 'Internal error creating lead.'},
            status=500,
        )

    if not created:
        return JsonResponse(
            {'status': 'duplicate', 'message': 'Lead already exists.'},
            status=409,
        )

    # Apply overrides that the caller specified (process_lead may have
    # set its own values via keyword matching, but the caller's explicit
    # values take precedence for confidence/urgency/location)
    updated_fields = []

    if confidence:
        lead.confidence = confidence
        updated_fields.append('confidence')

    if urgency:
        lead.urgency_level = urgency
        urgency_scores = {'hot': 90, 'warm': 70, 'new': 50, 'stale': 30}
        lead.urgency_score = urgency_scores.get(urgency, 50)
        updated_fields.extend(['urgency_level', 'urgency_score'])

    detected_location = data.get('detected_location', '')
    if detected_location:
        lead.detected_location = detected_location
        updated_fields.append('detected_location')

    if updated_fields:
        lead.save(update_fields=updated_fields)

    logger.info(
        f'[IngestAPI] Lead created: id={lead.id} platform={platform} '
        f'assigned={num_assigned}'
    )

    return JsonResponse(
        {
            'status': 'created',
            'lead_id': lead.id,
            'assigned_to': num_assigned,
        },
        status=201,
    )
