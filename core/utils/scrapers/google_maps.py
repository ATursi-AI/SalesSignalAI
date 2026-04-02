"""
Google Maps business scraper for building prospect lists.
Uses Google Places API (New + legacy fallback) to find businesses
by type and geography.
"""
import logging
import time

import requests
from django.conf import settings

from core.models import ProspectBusiness

logger = logging.getLogger(__name__)

REQUEST_DELAY = 0.5


def search_businesses(query, location=None, radius_meters=40000, max_results=20):
    """
    Search Google Places for businesses by type/keyword and location.
    Returns list of business dicts.

    Args:
        query: e.g. "plumber Brooklyn NY"
        location: optional (lat, lng) tuple to bias search
        radius_meters: search radius (default ~25 miles)
        max_results: max businesses to return
    """
    api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        logger.warning('GOOGLE_MAPS_API_KEY not configured')
        return []

    results = _new_api_search(query, api_key, max_results)
    if not results:
        results = _legacy_api_search(query, location, radius_meters, api_key, max_results)

    return results


def _new_api_search(query, api_key, max_results):
    """Search via Google Places API (New)."""
    url = 'https://places.googleapis.com/v1/places:searchText'
    headers = {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': (
            'places.id,places.displayName,places.formattedAddress,'
            'places.rating,places.userRatingCount,places.websiteUri,'
            'places.nationalPhoneNumber,places.types'
        ),
    }
    payload = {
        'textQuery': query,
        'maxResultCount': min(max_results, 20),
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []

        data = resp.json()
        results = []
        for place in data.get('places', []):
            results.append({
                'place_id': place.get('id', ''),
                'name': place.get('displayName', {}).get('text', ''),
                'address': place.get('formattedAddress', ''),
                'rating': place.get('rating'),
                'review_count': place.get('userRatingCount'),
                'website': place.get('websiteUri', ''),
                'phone': place.get('nationalPhoneNumber', ''),
                'types': place.get('types', []),
            })
        return results
    except requests.RequestException as e:
        logger.error(f'Google Places new API error: {e}')
        return []


def _legacy_api_search(query, location, radius_meters, api_key, max_results):
    """Fallback to legacy Places API."""
    url = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
    params = {'query': query, 'key': api_key}
    if location:
        params['location'] = f'{location[0]},{location[1]}'
        params['radius'] = radius_meters

    all_results = []
    next_page = None

    for _ in range(3):  # max 3 pages = 60 results
        if next_page:
            params = {'pagetoken': next_page, 'key': api_key}
            time.sleep(2)  # page token needs delay

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f'Google Places legacy API error: {e}')
            break

        for place in data.get('results', []):
            all_results.append({
                'place_id': place.get('place_id', ''),
                'name': place.get('name', ''),
                'address': place.get('formatted_address', ''),
                'rating': place.get('rating'),
                'review_count': place.get('user_ratings_total'),
                'website': '',
                'phone': '',
                'types': place.get('types', []),
            })

        next_page = data.get('next_page_token')
        if not next_page or len(all_results) >= max_results:
            break

    return all_results[:max_results]


def get_place_details(place_id):
    """Fetch detailed info for a single place."""
    api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        return None

    # Try new API
    url = f'https://places.googleapis.com/v1/places/{place_id}'
    headers = {
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': (
            'displayName,formattedAddress,rating,userRatingCount,'
            'websiteUri,nationalPhoneNumber,types'
        ),
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                'name': data.get('displayName', {}).get('text', ''),
                'address': data.get('formattedAddress', ''),
                'rating': data.get('rating'),
                'review_count': data.get('userRatingCount'),
                'website': data.get('websiteUri', ''),
                'phone': data.get('nationalPhoneNumber', ''),
            }
    except requests.RequestException:
        pass

    # Fallback
    try:
        legacy_url = 'https://maps.googleapis.com/maps/api/place/details/json'
        resp = requests.get(legacy_url, params={
            'place_id': place_id,
            'fields': 'name,formatted_address,rating,user_ratings_total,website,formatted_phone_number',
            'key': api_key,
        }, timeout=10)
        if resp.status_code == 200:
            r = resp.json().get('result', {})
            return {
                'name': r.get('name', ''),
                'address': r.get('formatted_address', ''),
                'rating': r.get('rating'),
                'review_count': r.get('user_ratings_total'),
                'website': r.get('website', ''),
                'phone': r.get('formatted_phone_number', ''),
            }
    except requests.RequestException:
        pass

    return None


def scrape_prospects(query, zip_codes=None, radius_miles=25, max_per_query=20):
    """
    High-level function: search Google Maps and create ProspectBusiness records.

    Args:
        query: business type (e.g. "plumber", "HVAC contractor")
        zip_codes: list of zip codes to search around
        radius_miles: search radius
        max_per_query: max results per search query

    Returns:
        dict with counts: searched, created, duplicates
    """
    stats = {'searched': 0, 'created': 0, 'duplicates': 0}
    radius_meters = int(radius_miles * 1609.34)

    search_queries = []
    if zip_codes:
        for zc in zip_codes:
            search_queries.append(f'{query} near {zc}')
    else:
        search_queries.append(query)

    for sq in search_queries:
        logger.info(f'Searching Google Maps: {sq}')
        businesses = search_businesses(sq, radius_meters=radius_meters, max_results=max_per_query)
        stats['searched'] += len(businesses)

        for biz in businesses:
            # Deduplicate by google_place_id or name+address
            exists = False
            if biz['place_id']:
                exists = ProspectBusiness.objects.filter(
                    google_place_id=biz['place_id']
                ).exists()
            if not exists and biz['name']:
                exists = ProspectBusiness.objects.filter(
                    name=biz['name'],
                    address=biz['address'],
                ).exists()

            if exists:
                stats['duplicates'] += 1
                continue

            # Parse city/state from address
            city, state, zip_code = _parse_address(biz.get('address', ''))

            ProspectBusiness.objects.create(
                name=biz['name'],
                category=query,
                address=biz.get('address', ''),
                city=city,
                state=state,
                zip_code=zip_code,
                phone=biz.get('phone', ''),
                website=biz.get('website', ''),
                google_rating=biz.get('rating'),
                google_review_count=biz.get('review_count'),
                google_place_id=biz.get('place_id', ''),
                source='google_maps',
                raw_data=biz,
            )
            stats['created'] += 1

        time.sleep(REQUEST_DELAY)

    logger.info(f'Prospect scrape complete: {stats}')
    return stats


def _parse_address(address):
    """Extract city, state, zip from a formatted address string."""
    import re
    city, state, zip_code = '', '', ''

    if not address:
        return city, state, zip_code

    # Pattern: "City, ST ZIP, Country" or "City, ST ZIP"
    match = re.search(r'([A-Za-z\s]+),\s*([A-Z]{2})\s*(\d{5})', address)
    if match:
        city = match.group(1).strip()
        state = match.group(2)
        zip_code = match.group(3)
    else:
        # Try just state + zip
        match = re.search(r'([A-Z]{2})\s+(\d{5})', address)
        if match:
            state = match.group(1)
            zip_code = match.group(2)

    return city, state, zip_code
