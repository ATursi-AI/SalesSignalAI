"""Lead enrichment via Google Places API (free $200/month tier).

Uses the Places API (New) with text search to find businesses by name/address,
then fetches phone, website, rating, and other details.

Costs: ~$0.032 per lookup (Text Search + Place Details).
Free tier: $200/month = ~6,250 lookups/month at zero cost.
"""
import hashlib
import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Cache enrichment results for 30 days to avoid repeat lookups
CACHE_TTL = 60 * 60 * 24 * 30


def _cache_key(query_string):
    """Generate a stable cache key from the search query."""
    h = hashlib.md5(query_string.lower().strip().encode()).hexdigest()
    return f'places_enrich:{h}'


def enrich_lead_via_places(lead):
    """
    Enrich a single Lead with contact info via Google Places API.

    Returns dict with: name, phone, website, rating, total_ratings, found, etc.
    Updates the lead's contact fields in-place.
    """
    api_key = getattr(settings, 'GOOGLE_PLACES_API_KEY', '') or getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        logger.warning('[Places] No GOOGLE_PLACES_API_KEY or GOOGLE_MAPS_API_KEY configured')
        return {'found': False, 'reason': 'Google Places API key not configured'}

    # Build search query from lead data
    raw = lead.raw_data or {}
    biz_name = (
        lead.contact_business
        or raw.get('business_name', '')
        or raw.get('respondent_name', '')
        or raw.get('entity_name', '')
        or lead.source_author
        or ''
    )
    address = (
        lead.contact_address
        or ((raw.get('house_number', '') + ' ' + raw.get('street_name', '')).strip())
        or raw.get('address', '')
        or lead.detected_location
        or ''
    )
    city = lead.region or raw.get('borough', '') or raw.get('city', '') or ''
    state = lead.state or 'NY'
    zip_code = lead.detected_zip or raw.get('zip', '') or ''

    # Build the text query
    parts = [p for p in [biz_name, address, city, state, zip_code] if p]
    query = ', '.join(parts)

    if not query or len(query) < 5:
        return {'found': False, 'reason': 'Not enough info to search'}

    # Check cache first
    ck = _cache_key(query)
    cached = cache.get(ck)
    if cached is not None:
        logger.info(f'[Places] Cache hit for: {query[:60]}')
        _apply_result_to_lead(lead, cached)
        return cached

    # Call Google Places Text Search (New)
    result = _search_places(api_key, query)

    if result is None:
        return {'found': False, 'reason': 'Google Places API error'}

    if not result.get('found'):
        # Cache negative results too (shorter TTL) to avoid re-querying
        cache.set(ck, result, 60 * 60 * 24 * 7)  # 7 days for not-found
        return result

    # Cache the successful result
    cache.set(ck, result, CACHE_TTL)

    # Apply to lead
    _apply_result_to_lead(lead, result)

    return result


def _apply_result_to_lead(lead, result):
    """Apply enrichment result fields to the lead and save."""
    if not result.get('found'):
        return

    updated_fields = []
    if result.get('phone') and not lead.contact_phone:
        lead.contact_phone = result['phone']
        updated_fields.append('contact_phone')
    if result.get('email') and not lead.contact_email:
        lead.contact_email = result['email']
        updated_fields.append('contact_email')
    if result.get('website') and not lead.contact_business:
        lead.contact_business = result['website']
        updated_fields.append('contact_business')
    elif result.get('name') and not lead.contact_business:
        lead.contact_business = result['name']
        updated_fields.append('contact_business')
    if result.get('address') and not lead.contact_address:
        lead.contact_address = result['address']
        updated_fields.append('contact_address')

    # Store in raw_data
    raw = lead.raw_data or {}
    raw['places_enrichment'] = result
    lead.raw_data = raw
    updated_fields.append('raw_data')

    if result.get('phone') or result.get('website'):
        lead.enrichment_status = 'enriched'
        lead.enrichment_date = timezone.now()
        updated_fields.extend(['enrichment_status', 'enrichment_date'])

    if updated_fields:
        lead.save(update_fields=list(set(updated_fields)))


def _search_places(api_key, query):
    """
    Search Google Places using the Text Search (New) endpoint.
    Returns dict with phone, website, name, rating, etc.
    """
    # Step 1: Text Search to find the place
    url = 'https://places.googleapis.com/v1/places:searchText'
    headers = {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': (
            'places.id,places.displayName,places.formattedAddress,'
            'places.nationalPhoneNumber,places.internationalPhoneNumber,'
            'places.websiteUri,places.rating,places.userRatingCount,'
            'places.businessStatus,places.types'
        ),
    }

    try:
        resp = requests.post(
            url,
            headers=headers,
            json={'textQuery': query, 'maxResultCount': 3},
            timeout=15,
        )

        if resp.status_code != 200:
            logger.error(f'[Places] API error {resp.status_code}: {resp.text[:200]}')
            return None

        data = resp.json()
        places = data.get('places', [])

        if not places:
            logger.info(f'[Places] No results for: {query[:60]}')
            return {'found': False, 'reason': 'No matching business found'}

        # Use the first result (most relevant)
        place = places[0]

        result = {
            'found': True,
            'source': 'google_places',
            'place_id': place.get('id', ''),
            'name': place.get('displayName', {}).get('text', ''),
            'address': place.get('formattedAddress', ''),
            'phone': place.get('nationalPhoneNumber', '') or place.get('internationalPhoneNumber', ''),
            'website': place.get('websiteUri', ''),
            'rating': place.get('rating'),
            'total_ratings': place.get('userRatingCount', 0),
            'business_status': place.get('businessStatus', ''),
            'types': place.get('types', []),
        }

        logger.info(
            f'[Places] Found: {result["name"]} | '
            f'phone={result["phone"]} | website={result["website"]}'
        )
        return result

    except requests.RequestException as e:
        logger.error(f'[Places] Request error: {e}')
        return None
