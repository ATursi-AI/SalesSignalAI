"""Lead enrichment via Gemini API with caching to avoid duplicate API calls."""
import hashlib
import json
import logging
import re

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Cache TTL: 30 days for found results, 7 days for not-found
CACHE_TTL_FOUND = 60 * 60 * 24 * 30
CACHE_TTL_NOT_FOUND = 60 * 60 * 24 * 7


def _enrichment_cache_key(respondent, address, city, state, zip_code):
    """Generate a stable cache key from enrichment query params."""
    raw_str = f'{respondent}|{address}|{city}|{state}|{zip_code}'.lower().strip()
    h = hashlib.md5(raw_str.encode()).hexdigest()
    return f'gemini_enrich:{h}'


def enrich_lead(lead):
    """
    Enrich a single Lead with contact info via Gemini.

    Returns dict: {phone, email, website, owner_name, source, confidence, found: bool}
    Updates the lead's contact fields and enrichment status in-place.
    Uses caching to avoid duplicate API calls for the same entity.
    """
    # Skip if already has phone
    if lead.contact_phone:
        return {'found': False, 'skipped': True, 'reason': 'Already has contact info'}

    # Build entity info from lead data
    raw = lead.raw_data or {}
    respondent = (
        lead.contact_name
        or lead.contact_business
        or raw.get('respondent_name', '')
        or raw.get('business_name', '')
        or raw.get('entity_name', '')
        or lead.source_author
        or ''
    )
    address = (
        lead.contact_address
        or raw.get('house_number', '') + ' ' + raw.get('street_name', '')
        or raw.get('address', '')
        or ''
    ).strip()
    city = (
        lead.region
        or raw.get('borough', '')
        or raw.get('city', '')
        or ''
    )
    state = lead.state or 'NY'
    zip_code = lead.detected_zip or raw.get('zip', '') or ''

    if not respondent and not address:
        lead.enrichment_status = 'enrichment_failed'
        lead.enrichment_date = timezone.now()
        lead.save(update_fields=['enrichment_status', 'enrichment_date'])
        return {'found': False, 'reason': 'No entity name or address to search'}

    # Check cache first to avoid duplicate API calls
    ck = _enrichment_cache_key(respondent, address, city, state, zip_code)
    cached = cache.get(ck)
    if cached is not None:
        logger.info(f'[Enrichment] Cache hit for: {respondent or address}')
        # Apply cached result to this lead
        if cached.get('phone'):
            lead.contact_phone = cached['phone']
        if cached.get('email'):
            lead.contact_email = cached['email']
        if cached.get('website') and not lead.contact_business:
            lead.contact_business = cached['website']
        if cached.get('owner_name') and not lead.contact_name:
            lead.contact_name = cached['owner_name']
        found = bool(cached.get('phone') or cached.get('email'))
        lead.enrichment_status = 'enriched' if found else 'enrichment_failed'
        lead.enrichment_date = timezone.now()
        raw['enrichment'] = cached
        raw['enrichment_source'] = 'cache'
        lead.raw_data = raw
        lead.save(update_fields=[
            'contact_phone', 'contact_email', 'contact_business',
            'contact_name', 'raw_data', 'enrichment_status', 'enrichment_date',
        ])
        cached['found'] = found
        cached['from_cache'] = True
        return cached

    # Call Gemini
    result = _call_gemini_enrichment(respondent, address, city, state, zip_code)

    if result is None:
        lead.enrichment_status = 'enrichment_failed'
        lead.enrichment_date = timezone.now()
        lead.save(update_fields=['enrichment_status', 'enrichment_date'])
        # Cache the failure so we don't retry the same query
        cache.set(ck, {'found': False, 'reason': 'Gemini API error'}, CACHE_TTL_NOT_FOUND)
        return {'found': False, 'reason': 'Gemini API error'}

    # Update lead fields
    found = False
    if result.get('phone'):
        lead.contact_phone = result['phone']
        found = True
    if result.get('email'):
        lead.contact_email = result['email']
        found = True
    if result.get('website'):
        # Store website in contact_business if no business name set
        if not lead.contact_business:
            lead.contact_business = result['website']
    if result.get('owner_name') and not lead.contact_name:
        lead.contact_name = result['owner_name']
        found = True

    # Store enrichment response
    raw['enrichment'] = result
    lead.raw_data = raw
    lead.enrichment_status = 'enriched' if found else 'enrichment_failed'
    lead.enrichment_date = timezone.now()
    lead.save(update_fields=[
        'contact_phone', 'contact_email', 'contact_business',
        'contact_name', 'raw_data', 'enrichment_status', 'enrichment_date',
    ])

    # Cache the result to avoid duplicate API calls
    ttl = CACHE_TTL_FOUND if found else CACHE_TTL_NOT_FOUND
    cache.set(ck, result, ttl)
    logger.info(f'[Enrichment] Cached result for: {respondent or address} (found={found})')

    result['found'] = found
    return result


def _call_gemini_enrichment(respondent, address, city, state, zip_code):
    """Call Gemini 2.0 Flash to find contact info."""
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        logger.warning('[Enrichment] GEMINI_API_KEY not configured')
        return None

    prompt = (
        'You are a lead enrichment agent. Find the phone number, email address, '
        'website, and owner/contact name for the following entity. Cross-reference '
        'property records, business registries, professional licenses, NPI data, '
        'and public filings.\n\n'
        f'Entity: {respondent}\n'
        f'Address: {address}\n'
        f'City: {city}\n'
        f'State: {state}\n'
        f'ZIP: {zip_code}\n\n'
        'Return ONLY valid JSON with no markdown:\n'
        '{"phone": "", "email": "", "website": "", "owner_name": "", '
        '"source": "", "confidence": "high/medium/low"}'
    )

    model_name = 'gemini-3-flash-preview'
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent'

    try:
        resp = requests.post(
            url,
            params={'key': api_key},
            headers={'Content-Type': 'application/json'},
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {
                    'maxOutputTokens': 4096,
                    'temperature': 0.2,
                },
            },
            timeout=30,
        )

        if resp.status_code != 200:
            logger.error(f'[Enrichment] Gemini API error {resp.status_code}: {resp.text[:200]}')
            return None

        data = resp.json()
        # Extract text, skipping thinking/thought parts
        parts = data['candidates'][0]['content']['parts']
        text = ''
        for part in parts:
            if 'text' in part:
                text += part['text']

        # Strip markdown code fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())

        # Extract JSON object even if surrounded by extra text
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if not match:
            logger.error(f'[Enrichment] No JSON found in response: {text[:200]}')
            return None
        result = json.loads(match.group())
        logger.info(f'[Enrichment] Found: phone={result.get("phone")}, email={result.get("email")}')
        return result

    except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f'[Enrichment] Error: {e}')
        return None
