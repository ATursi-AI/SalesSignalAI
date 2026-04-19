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

    For leads that originate from property-related sources (DOB permits,
    building violations, etc.), we FIRST consult public deed records (ACRIS
    for NYC) to pin down the legal owner + their mailing address. That
    authoritative owner is then the entity Gemini searches for — this
    prevents enrichment from accidentally returning the contractor/expeditor
    whose phone happens to be printed on the filing for the same address.
    """
    # Skip if already has phone
    if lead.contact_phone:
        return {'found': False, 'skipped': True, 'reason': 'Already has contact info'}

    raw = lead.raw_data or {}

    # --- Step 1: consult property deed records for authoritative owner ---
    deed = None
    try:
        from core.services.property_records_service import (
            attach_property_records_to_lead,
        )
        deed = attach_property_records_to_lead(lead)
        if deed:
            logger.info(
                '[Enrichment] Deed found for lead %s: owner=%s mailing=%s',
                lead.id, deed.get('owner_name'),
                deed.get('owner_mailing_city'),
            )
            raw = lead.raw_data or {}  # refreshed by attach_property_records_to_lead
    except Exception as e:
        logger.warning('[Enrichment] Property records lookup failed: %s', e)

    # --- Step 2: build entity info, preferring deed data when available ---
    respondent = (
        (deed.get('owner_name') if deed else '')
        or lead.contact_name
        or lead.contact_business
        or raw.get('respondent_name', '')
        or raw.get('business_name', '')
        or raw.get('entity_name', '')
        or lead.source_author
        or ''
    )
    # Prefer the owner's MAILING address from the deed — often different
    # from the property address, and that's where white-pages/Gemini should
    # actually search.
    if deed and deed.get('owner_mailing_address'):
        address = deed['owner_mailing_address']
        city = deed.get('owner_mailing_city') or lead.region or ''
        state = deed.get('owner_mailing_state') or lead.state or 'NY'
        zip_code = deed.get('owner_mailing_zip') or ''
    else:
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

    # Collect phones already on the lead / raw data so we can reject
    # Gemini results that just echo the contractor/applicant's phone.
    excluded_phones = _gather_existing_phones(lead, raw)

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

    # Call Gemini — pass the deed context and excluded phones so the
    # model doesn't return the contractor/expeditor by mistake.
    result = _call_gemini_enrichment(
        respondent, address, city, state, zip_code,
        excluded_phones=excluded_phones,
        deed=deed,
    )

    if result is None:
        lead.enrichment_status = 'enrichment_failed'
        lead.enrichment_date = timezone.now()
        lead.save(update_fields=['enrichment_status', 'enrichment_date'])
        # Cache the failure so we don't retry the same query
        cache.set(ck, {'found': False, 'reason': 'Gemini API error'}, CACHE_TTL_NOT_FOUND)
        return {'found': False, 'reason': 'Gemini API error'}

    # Reject phones that are clearly the contractor's / applicant's, not the owner's.
    if result.get('phone') and _normalize_phone(result['phone']) in excluded_phones:
        logger.info(
            '[Enrichment] Rejected phone %s — matches existing filing phone',
            result['phone'],
        )
        result['phone'] = ''
        result['rejected_phone_as_contractor'] = True

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


def _normalize_phone(phone):
    """Strip everything except digits for comparison."""
    if not phone:
        return ''
    return re.sub(r'\D', '', str(phone))[-10:]  # last 10 digits


def _gather_existing_phones(lead, raw):
    """
    Collect every phone we can find already associated with this lead — these
    are almost always the contractor/applicant/expeditor on a filing, NOT the
    owner. If Gemini returns one of these as "owner phone" we reject it.
    """
    candidates = set()
    for field in ('contact_phone',):
        v = getattr(lead, field, '') or ''
        if v:
            candidates.add(_normalize_phone(v))
    for key in (
        'phone', 'owner_sphone__', 'applicant_phone', 'applicant_s_phone__',
        'applicant_phone_1', 'contractor_phone', 'filer_phone',
        'respondent_phone', 'business_phone',
    ):
        v = raw.get(key, '')
        if v:
            candidates.add(_normalize_phone(v))
    return {p for p in candidates if p and len(p) == 10}


def _call_gemini_enrichment(respondent, address, city, state, zip_code,
                            excluded_phones=None, deed=None):
    """Call Gemini 2.0 Flash to find contact info."""
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        logger.warning('[Enrichment] GEMINI_API_KEY not configured')
        return None

    excluded_phones = excluded_phones or set()
    deed_block = ''
    if deed:
        deed_block = (
            '\n[DEED RECORD — use this as the authoritative owner, not any '
            'name found on building permits or DOB filings]\n'
            f"Legal owner per deed: {deed.get('owner_name', '')}\n"
            f"Deed recorded: {deed.get('recorded_date', '')}\n"
            f"Owner mailing: {deed.get('owner_mailing_address', '')}, "
            f"{deed.get('owner_mailing_city', '')} "
            f"{deed.get('owner_mailing_state', '')} "
            f"{deed.get('owner_mailing_zip', '')}\n"
        )

    excl_block = ''
    if excluded_phones:
        excl_block = (
            '\n[DO NOT RETURN these phone numbers — they are contractors, '
            'expeditors, or applicants on the filing, NOT the owner]:\n'
            + '\n'.join(f'  - {p}' for p in sorted(excluded_phones))
            + '\n'
        )

    prompt = (
        'You are a lead enrichment agent. Your job is to find the '
        'PROPERTY OWNER\'s direct personal contact info — phone, email, '
        'website, full name. You are NOT looking for the contractor, '
        'applicant, expeditor, architect, engineer, or any professional '
        'who filed paperwork on the property.\n\n'
        'Search priority order:\n'
        '  1. Deed / ACRIS / county clerk records (most authoritative)\n'
        '  2. Property tax assessor records & mailing addresses\n'
        '  3. NY Dept of State LLC / corporate filings (for LLC owners — '
        'find the registered agent, manager, or principals)\n'
        '  4. White-pages, spokeo-style directories for individual owners\n'
        '  5. The owner\'s personal or business web presence\n\n'
        'DO NOT USE:\n'
        '  - NYC DOB BIS filings, permit applicant contact blocks, '
        'expeditor data, or any phone/email printed on a construction filing\n'
        '  - HPD/ECB violation respondent contact info (those are often '
        'managing agents, not owners)\n'
        '  - Generic customer-service numbers for property management firms '
        'when the underlying owner is an individual\n\n'
        f'Entity (from deed if available, else filing): {respondent}\n'
        f'Mailing address: {address}\n'
        f'City: {city}\n'
        f'State: {state}\n'
        f'ZIP: {zip_code}\n'
        f'{deed_block}'
        f'{excl_block}\n'
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
