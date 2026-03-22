"""
Google Places API monitor for SalesSignal AI.

Uses the official Google Places API (New) for four types of lead detection:
  1. NEGATIVE REVIEWS — reviews <= 3 stars on local businesses
  2. CLOSED BUSINESSES — orphaned customers from closed businesses
  3. NEW BUSINESSES — newly discovered businesses in an area
  4. GOOGLE Q&A — questions posted on business listings
  5. NO WEBSITE — businesses without a website URL (sales prospects)

API endpoints used:
  - Nearby Search (New): find businesses by type and location
  - Place Details (New): get reviews, business_status, Q&A

Rate limits are respected. API call counts are logged for cost tracking
against Google's $200/month free credit.
"""
import hashlib
import logging
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from core.models.monitoring import TrackedGoogleBusiness
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Google Places API (New) base URL and helpers
# -------------------------------------------------------------------
PLACES_BASE = 'https://places.googleapis.com/v1'

# Category slug -> Google Places type(s) for Nearby Search
# https://developers.google.com/maps/documentation/places/web-service/place-types
CATEGORY_PLACE_TYPES = {
    'plumber': ['plumber'],
    'electrician': ['electrician'],
    'hvac': ['electrician'],
    'roofer': ['roofing_contractor'],
    'painter': ['painter'],
    'locksmith': ['locksmith'],
    'moving': ['moving_company'],
    'dentist': ['dentist'],
    'lawyer': ['lawyer'],
    'insurance': ['insurance_agency'],
    'real-estate': ['real_estate_agency'],
    'veterinarian': ['veterinary_care'],
    'chiropractor': ['chiropractor'],
    'barber': ['barber_shop'],
    'beauty-salon': ['beauty_salon'],
    'tattoo': ['tattoo_parlor'],
    'laundromat': ['laundromat'],
}

# Chain/franchise exclusion — skip these from all lead types
EXCLUDED_BUSINESSES = {
    'home depot',
    'home services at the home depot',
    'national grid',
    'pseg',
    'con edison',
    'abm facility',
    'abm - facility',
    "lowe's",
    'lowes',
    'ace hardware',
    'menards',
    'walmart',
    'costco',
    'target',
    'best buy',
    'sears',
    'amazon',
    'spectrum',
    'verizon',
    'at&t',
    't-mobile',
    'sprint',
    'usps',
    'fedex',
    'ups store',
}

# New businesses in these categories create cross-sell leads
NEW_BUSINESS_CROSS_SELL = {
    'restaurant': ['insurance', 'commercial-cleaning', 'pest-control', 'hvac', 'accountant-cpa'],
    'store': ['insurance', 'commercial-cleaning', 'accountant-cpa'],
    'gym': ['insurance', 'commercial-cleaning', 'hvac', 'plumber'],
    'salon': ['insurance', 'plumber', 'electrical'],
    'dentist': ['insurance', 'commercial-cleaning', 'accountant-cpa'],
    'lawyer': ['insurance', 'commercial-cleaning', 'accountant-cpa'],
    'veterinarian': ['insurance', 'commercial-cleaning', 'pest-control'],
}

# Geocode city names to lat/lng for Nearby Search
CITY_COORDINATES = {
    'long island, ny': (40.7891, -73.1350),
    'nassau county, ny': (40.7400, -73.5894),
    'suffolk county, ny': (40.9432, -72.6831),
    'new york, ny': (40.7128, -74.0060),
    'brooklyn, ny': (40.6782, -73.9442),
    'queens, ny': (40.7282, -73.7949),
    'bronx, ny': (40.8448, -73.8648),
    'staten island, ny': (40.5795, -74.1502),
    'manhattan, ny': (40.7831, -73.9712),
    'jersey city, nj': (40.7178, -74.0431),
    'hoboken, nj': (40.7440, -74.0324),
    'westchester, ny': (41.1220, -73.7949),
    'yonkers, ny': (40.9312, -73.8987),
}


def _get_coordinates(city):
    """
    Resolve city string to (lat, lng). First checks hardcoded map,
    then falls back to Google Geocoding API.
    """
    key = city.lower().strip()
    if key in CITY_COORDINATES:
        return CITY_COORDINATES[key]

    # Fallback: Geocoding API
    api_key = settings.GOOGLE_PLACES_API_KEY
    if not api_key:
        return None

    try:
        resp = requests.get(
            'https://maps.googleapis.com/maps/api/geocode/json',
            params={'address': city, 'key': api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get('results', [])
            if results:
                loc = results[0]['geometry']['location']
                return (loc['lat'], loc['lng'])
    except requests.RequestException as e:
        logger.error(f'[GooglePlaces] Geocode failed for "{city}": {e}')

    return None


# -------------------------------------------------------------------
# API call wrappers
# -------------------------------------------------------------------

class APIUsageTracker:
    """Track API calls for cost monitoring."""
    def __init__(self):
        self.calls = {
            'nearby_search': 0,
            'place_details': 0,
            'geocoding': 0,
        }

    @property
    def total(self):
        return sum(self.calls.values())

    def log(self, endpoint):
        self.calls[endpoint] = self.calls.get(endpoint, 0) + 1

    def summary(self):
        # Approximate costs per Google pricing (as of 2025):
        # Nearby Search: $0.032/call, Place Details: $0.017/call
        nearby_cost = self.calls.get('nearby_search', 0) * 0.032
        details_cost = self.calls.get('place_details', 0) * 0.017
        return {
            'total_calls': self.total,
            'nearby_search_calls': self.calls.get('nearby_search', 0),
            'place_details_calls': self.calls.get('place_details', 0),
            'estimated_cost_usd': round(nearby_cost + details_cost, 4),
        }


def _nearby_search(api_key, lat, lng, radius, place_types, tracker,
                   max_results=20):
    """
    Google Places Nearby Search (New).
    Returns list of place dicts.
    """
    url = f'{PLACES_BASE}/places:searchNearby'
    headers = {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': (
            'places.id,places.displayName,places.formattedAddress,'
            'places.location,places.types,places.rating,'
            'places.userRatingCount,places.businessStatus,'
            'places.googleMapsUri'
        ),
    }
    body = {
        'locationRestriction': {
            'circle': {
                'center': {'latitude': lat, 'longitude': lng},
                'radius': radius,
            }
        },
        'includedTypes': place_types,
        'maxResultCount': min(max_results, 20),
        'languageCode': 'en',
    }

    tracker.log('nearby_search')
    logger.debug(f'[GooglePlaces] Nearby Search: types={place_types} at ({lat},{lng}) r={radius}')

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.error(f'[GooglePlaces] Nearby Search request failed: {e}')
        return []

    if resp.status_code != 200:
        logger.warning(
            f'[GooglePlaces] Nearby Search returned {resp.status_code}: '
            f'{resp.text[:300]}'
        )
        return []

    data = resp.json()
    return data.get('places', [])


def _place_details(api_key, place_id, tracker):
    """
    Google Place Details (New) — fetches reviews, Q&A, and business status.
    Returns the place dict or None.
    """
    url = f'{PLACES_BASE}/places/{place_id}'
    headers = {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': (
            'id,displayName,formattedAddress,location,types,'
            'rating,userRatingCount,businessStatus,googleMapsUri,'
            'reviews,websiteUri,nationalPhoneNumber'
        ),
    }

    tracker.log('place_details')

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.error(f'[GooglePlaces] Place Details failed for {place_id}: {e}')
        return None

    if resp.status_code != 200:
        logger.warning(
            f'[GooglePlaces] Place Details {place_id} returned {resp.status_code}: '
            f'{resp.text[:300]}'
        )
        return None

    return resp.json()


# -------------------------------------------------------------------
# Detection logic
# -------------------------------------------------------------------

def _review_confidence(rating):
    """1 star=HIGH, 2=MEDIUM, 3=LOW."""
    if rating <= 1:
        return 'high'
    elif rating <= 2:
        return 'medium'
    return 'low'


def _review_urgency(published_time):
    """HOT if <7 days, WARM if 7-30 days, else NEW."""
    if not published_time:
        return 'new', 50

    try:
        from datetime import datetime
        # Google returns ISO 8601: "2025-12-15T10:30:00Z"
        if isinstance(published_time, str):
            published_time = published_time.replace('Z', '+00:00')
            dt = datetime.fromisoformat(published_time)
            if dt.tzinfo is None:
                dt = timezone.make_aware(dt)
        else:
            dt = published_time
        age = timezone.now() - dt
        if age < timedelta(days=7):
            return 'hot', 90
        elif age < timedelta(days=30):
            return 'warm', 70
    except (ValueError, TypeError):
        pass

    return 'new', 50


def _process_negative_reviews(place, place_detail, category, city,
                              max_reviews, dry_run, stats):
    """Detect reviews <= 3 stars and create leads."""
    reviews = place_detail.get('reviews', [])
    if not reviews:
        return

    biz_name = place_detail.get('displayName', {}).get('text', 'Unknown')
    address = place_detail.get('formattedAddress', city)
    maps_url = place_detail.get('googleMapsUri', '')

    review_count = 0
    for review in reviews:
        if review_count >= max_reviews:
            break

        rating_obj = review.get('rating', 0)
        try:
            rating = int(float(rating_obj))
        except (ValueError, TypeError):
            continue

        if rating > 3 or rating < 1:
            continue

        text_obj = review.get('text', {})
        review_text = text_obj.get('text', '') if isinstance(text_obj, dict) else str(text_obj)
        if not review_text or len(review_text) < 15:
            continue

        review_count += 1
        stats['negative_reviews'] += 1

        author_name = review.get('authorAttribution', {}).get('displayName', 'Anonymous')
        published_time = review.get('publishTime', '')
        confidence = _review_confidence(rating)
        urgency_level, urgency_score = _review_urgency(published_time)

        content = (
            f'{rating}-star Google review on {biz_name}\n\n'
            f'Reviewer: {author_name}\n'
            f'Rating: {"*" * rating} ({rating}/5)\n\n'
            f'"{review_text}"\n\n'
            f'Business: {biz_name}\n'
            f'Category: {category}\n'
            f'Location: {address}'
        )

        source_url = maps_url or f'https://www.google.com/maps/place/?q=place_id:{place.get("id", "")}'

        if dry_run:
            stats['dry_run_matches'].append({
                'type': 'NEGATIVE_REVIEW',
                'business_name': biz_name,
                'category': category,
                'rating': rating,
                'confidence': confidence,
                'urgency': urgency_level,
                'author': author_name,
                'text': review_text[:120],
                'location': address,
                'url': source_url,
            })
            continue

        phone = place_detail.get('nationalPhoneNumber', '')
        lead, created, num_assigned = process_lead(
            platform='google_reviews',
            source_url=source_url,
            content=content,
            author=author_name,
            posted_at=None,
            raw_data={
                'business_name': biz_name,
                'category': category,
                'star_rating': rating,
                'reviewer_name': author_name,
                'review_date': published_time,
                'place_id': place.get('id', ''),
                'type': 'negative_review',
            },
            source_group='reviews',
            source_type='google_reviews',
            contact_business=biz_name,
            contact_phone=phone,
            contact_address=address,
        )
        if created:
            stats['created'] += 1
            stats['assigned'] += num_assigned
        else:
            stats['duplicates'] += 1


def _process_closed_business(place, category, city, dry_run, stats):
    """Detect closed businesses and create orphaned customer leads."""
    biz_status = place.get('businessStatus', 'OPERATIONAL')
    if biz_status not in ('CLOSED_TEMPORARILY', 'CLOSED_PERMANENTLY'):
        return

    stats['closed_businesses'] += 1
    biz_name = place.get('displayName', {}).get('text', 'Unknown')
    address = place.get('formattedAddress', city)
    maps_url = place.get('googleMapsUri', '')
    status_display = biz_status.replace('_', ' ').title()
    source_url = maps_url or f'https://www.google.com/maps/place/?q=place_id:{place.get("id", "")}'

    content = (
        f'ORPHANED CUSTOMERS: {biz_name} is {status_display}.\n\n'
        f'Business: {biz_name}\n'
        f'Category: {category}\n'
        f'Location: {address}\n'
        f'Status: {status_display}\n\n'
        f'Customers of this business may be looking for a new {category} provider.'
    )

    if dry_run:
        stats['dry_run_matches'].append({
            'type': 'CLOSED_BUSINESS',
            'business_name': biz_name,
            'category': category,
            'status': biz_status,
            'location': address,
            'url': source_url,
        })
        return

    content_hash = hashlib.sha256(
        f'google_maps|{place.get("id", "")}|orphaned'.encode()
    ).hexdigest()

    from core.models.leads import Lead
    if Lead.objects.filter(content_hash=content_hash).exists():
        stats['duplicates'] += 1
        return

    Lead.objects.create(
        platform='google_maps',
        source_url=source_url,
        source_content=content,
        source_author=biz_name,
        detected_location=address,
        urgency_score=90,
        urgency_level='hot',
        confidence='high',
        content_hash=content_hash,
        raw_data={
            'business_name': biz_name,
            'category': category,
            'business_status': biz_status,
            'place_id': place.get('id', ''),
            'type': 'orphaned_customer',
        },
    )
    stats['created'] += 1


def _process_new_business(place, category, city, dry_run, stats):
    """
    Detect newly discovered businesses. Returns True if business is new.
    Updates TrackedGoogleBusiness either way.
    """
    place_id = place.get('id', '')
    if not place_id:
        return False

    biz_name = place.get('displayName', {}).get('text', 'Unknown')
    address = place.get('formattedAddress', city)
    location = place.get('location', {})
    lat = location.get('latitude')
    lng = location.get('longitude')
    biz_status = place.get('businessStatus', 'OPERATIONAL')
    rating = place.get('rating', 0)
    review_count = place.get('userRatingCount', 0)
    maps_url = place.get('googleMapsUri', '')

    tracked, created = TrackedGoogleBusiness.objects.update_or_create(
        place_id=place_id,
        defaults={
            'name': biz_name,
            'address': address,
            'category': category,
            'latitude': lat,
            'longitude': lng,
            'business_status': biz_status,
            'avg_rating': rating or None,
            'total_reviews': review_count or 0,
            'google_maps_url': maps_url,
        },
    )

    if not created:
        return False

    # New business detected
    stats['new_businesses'] += 1
    source_url = maps_url or f'https://www.google.com/maps/place/?q=place_id:{place_id}'

    content = (
        f'NEW BUSINESS: {biz_name} in {address}\n\n'
        f'Category: {category}\n'
        f'Rating: {rating}/5 ({review_count} reviews)\n'
        f'Location: {address}\n\n'
        f'This business was not previously tracked and may represent '
        f'a new entrant in the {category} market.'
    )

    if dry_run:
        stats['dry_run_matches'].append({
            'type': 'NEW_BUSINESS',
            'business_name': biz_name,
            'category': category,
            'rating': rating,
            'reviews': review_count,
            'location': address,
            'url': source_url,
        })
        return True

    content_hash = hashlib.sha256(
        f'google_maps|{place_id}|new_business'.encode()
    ).hexdigest()

    from core.models.leads import Lead
    if not Lead.objects.filter(content_hash=content_hash).exists():
        Lead.objects.create(
            platform='google_maps',
            source_url=source_url,
            source_content=content,
            source_author=biz_name,
            detected_location=address,
            urgency_score=70,
            urgency_level='warm',
            confidence='high',
            content_hash=content_hash,
            raw_data={
                'business_name': biz_name,
                'category': category,
                'place_id': place_id,
                'rating': rating,
                'review_count': review_count,
                'type': 'new_business',
            },
        )
        stats['created'] += 1
    else:
        stats['duplicates'] += 1

    return True


def _process_qna(place_detail, category, city, dry_run, stats):
    """
    Detect Q&A questions on business listings.
    Note: The Places API (New) currently does not expose Q&A in the
    standard Place Details response. This function is structured to
    handle it if/when Google adds it, or if we use the legacy API.
    For now, we check the response for any Q&A-related fields.
    """
    # The New Places API uses 'questions' or 'googleQAndA' — check both
    questions = place_detail.get('questions', [])
    if not questions:
        questions = place_detail.get('googleQAndA', [])
    if not questions:
        return

    biz_name = place_detail.get('displayName', {}).get('text', 'Unknown')
    address = place_detail.get('formattedAddress', city)
    maps_url = place_detail.get('googleMapsUri', '')

    for question in questions:
        q_text = question.get('text', '') or question.get('question', '')
        if not q_text or len(q_text) < 10:
            continue

        stats['qna_questions'] += 1
        author = question.get('author', {}).get('displayName', 'Anonymous')
        source_url = maps_url or f'https://www.google.com/maps/place/?q=place_id:{place_detail.get("id", "")}'

        content = (
            f'Google Q&A on {biz_name}: "{q_text}"\n\n'
            f'Business: {biz_name}\n'
            f'Category: {category}\n'
            f'Location: {address}\n\n'
            f'This person is actively asking about {category} services.'
        )

        if dry_run:
            stats['dry_run_matches'].append({
                'type': 'QNA_QUESTION',
                'business_name': biz_name,
                'category': category,
                'question': q_text[:120],
                'author': author,
                'location': address,
                'url': source_url,
            })
            continue

        phone = place_detail.get('nationalPhoneNumber', '')
        lead, created, num_assigned = process_lead(
            platform='google_qna',
            source_url=source_url,
            content=content,
            author=author,
            raw_data={
                'business_name': biz_name,
                'category': category,
                'question_text': q_text,
                'place_id': place_detail.get('id', ''),
                'type': 'qna_question',
            },
            source_group='reviews',
            source_type='google_qa',
            contact_business=biz_name,
            contact_phone=phone,
            contact_address=address,
        )
        if created:
            stats['created'] += 1
            stats['assigned'] += num_assigned
        else:
            stats['duplicates'] += 1


def _process_no_website(place, place_detail, category, city, dry_run, stats):
    """Detect businesses with no website URL — these are sales prospects
    for SalesSignal AND potential website-building customers."""
    website = place_detail.get('websiteUri', '')
    if website:
        return

    place_id = place.get('id', '')
    biz_name = place_detail.get('displayName', {}).get('text', 'Unknown')
    address = place_detail.get('formattedAddress', city)
    maps_url = place_detail.get('googleMapsUri', '')
    phone = place_detail.get('nationalPhoneNumber', '')
    rating = place_detail.get('rating', 0)
    review_count = place_detail.get('userRatingCount', 0)
    biz_types = place_detail.get('types', [])

    stats['no_website'] += 1
    source_url = maps_url or f'https://www.google.com/maps/place/?q=place_id:{place_id}'

    content = (
        f'NO WEBSITE: {biz_name} has no website listed on Google.\n\n'
        f'Business: {biz_name}\n'
        f'Category: {category}\n'
        f'Location: {address}\n'
        f'Phone: {phone or "Not listed"}\n'
        f'Rating: {rating}/5 ({review_count} reviews)\n\n'
        f'This business has no online presence beyond Google Maps — '
        f'strong prospect for website building and digital marketing services.'
    )

    if dry_run:
        stats['dry_run_matches'].append({
            'type': 'NO_WEBSITE',
            'business_name': biz_name,
            'category': category,
            'phone': phone,
            'rating': rating,
            'reviews': review_count,
            'location': address,
            'url': source_url,
        })
        return

    content_hash = hashlib.sha256(
        f'google_maps|{place_id}|no_website'.encode()
    ).hexdigest()

    from core.models.leads import Lead
    if Lead.objects.filter(content_hash=content_hash).exists():
        stats['duplicates'] += 1
        return

    Lead.objects.create(
        platform='google_maps',
        source_url=source_url,
        source_content=content,
        source_author=biz_name,
        detected_location=address,
        urgency_score=80,
        urgency_level='warm',
        confidence='high',
        content_hash=content_hash,
        raw_data={
            'business_name': biz_name,
            'category': category,
            'phone': phone,
            'address': address,
            'rating': rating,
            'review_count': review_count,
            'place_id': place_id,
            'google_types': biz_types[:5],
            'type': 'no_website',
            'detected_category': 'NO_WEBSITE_PROSPECT',
        },
    )
    stats['created'] += 1


# -------------------------------------------------------------------
# Main monitoring function
# -------------------------------------------------------------------

def monitor_google_places(
    categories=None,
    city='Long Island, NY',
    radius=10000,
    max_reviews=5,
    dry_run=False,
    no_website_only=False,
):
    """
    Main Google Places API monitor. Searches for businesses by category
    and location, then detects:
      1. Negative reviews (<= 3 stars)
      2. Closed businesses (orphaned customers)
      3. New businesses (not previously tracked)
      4. Google Q&A questions
      5. No-website businesses (sales prospects)

    Args:
        categories: list of category slugs (default: all with place types)
        city: target city/area
        radius: search radius in meters (default 10000)
        max_reviews: max negative reviews per business to process
        dry_run: if True, log matches without creating leads

    Returns:
        dict with stats
    """
    api_key = settings.GOOGLE_PLACES_API_KEY
    if not api_key:
        logger.error('[GooglePlaces] GOOGLE_PLACES_API_KEY not configured')
        return {
            'error': 'GOOGLE_PLACES_API_KEY not configured',
            'businesses_found': 0,
            'negative_reviews': 0,
            'closed_businesses': 0,
            'new_businesses': 0,
            'qna_questions': 0,
            'no_website': 0,
            'created': 0,
            'duplicates': 0,
            'assigned': 0,
            'categories_searched': 0,
            'dry_run_matches': [],
            'api_usage': {},
        }

    tracker = APIUsageTracker()

    # Resolve coordinates
    coords = _get_coordinates(city)
    if not coords:
        logger.error(f'[GooglePlaces] Could not resolve coordinates for "{city}"')
        return {
            'error': f'Could not geocode "{city}"',
            'businesses_found': 0,
            'negative_reviews': 0,
            'closed_businesses': 0,
            'new_businesses': 0,
            'qna_questions': 0,
            'created': 0,
            'duplicates': 0,
            'assigned': 0,
            'categories_searched': 0,
            'dry_run_matches': [],
            'api_usage': tracker.summary(),
        }

    lat, lng = coords

    # Resolve categories to place types
    if categories is None:
        categories = list(CATEGORY_PLACE_TYPES.keys())

    stats = {
        'businesses_found': 0,
        'negative_reviews': 0,
        'closed_businesses': 0,
        'new_businesses': 0,
        'qna_questions': 0,
        'no_website': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
        'categories_searched': 0,
        'dry_run_matches': [],
    }

    seen_place_ids = set()

    for category in categories:
        place_types = CATEGORY_PLACE_TYPES.get(category)
        if not place_types:
            logger.warning(f'[GooglePlaces] No place types for category "{category}", skipping')
            continue

        stats['categories_searched'] += 1
        logger.info(f'[GooglePlaces] Searching "{category}" ({place_types}) near {city}')

        # Nearby Search
        places = _nearby_search(
            api_key, lat, lng, radius, place_types, tracker,
        )

        if not places:
            logger.info(f'[GooglePlaces] No results for "{category}"')
            continue

        logger.info(f'[GooglePlaces] Found {len(places)} businesses for "{category}"')
        stats['businesses_found'] += len(places)

        for place in places:
            place_id = place.get('id', '')
            if not place_id:
                continue

            # Chain/franchise exclusion
            biz_name_check = place.get('displayName', {}).get('text', '')
            if any(excluded in biz_name_check.lower() for excluded in EXCLUDED_BUSINESSES):
                continue

            # Cross-category deduplication
            if place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            if not no_website_only:
                # 1. Check for closed businesses (from Nearby Search data)
                _process_closed_business(place, category, city, dry_run, stats)

                # 2. Track new businesses
                _process_new_business(place, category, city, dry_run, stats)

            # 3. Get Place Details for reviews, Q&A, and website check
            # Rate limit: 100ms between detail requests
            time.sleep(0.1)

            detail = _place_details(api_key, place_id, tracker)
            if not detail:
                stats['errors'] += 1
                continue

            if not no_website_only:
                # 4. Process negative reviews
                _process_negative_reviews(
                    place, detail, category, city,
                    max_reviews, dry_run, stats,
                )

                # 5. Process Q&A
                _process_qna(detail, category, city, dry_run, stats)

            # 6. Detect businesses with no website
            _process_no_website(place, detail, category, city, dry_run, stats)

        # Rate limit between category searches
        time.sleep(0.2)

    stats['api_usage'] = tracker.summary()
    logger.info(
        f'[GooglePlaces] Monitor complete: '
        f'{stats["businesses_found"]} businesses, '
        f'{stats["negative_reviews"]} negative reviews, '
        f'{stats["closed_businesses"]} closed, '
        f'{stats["new_businesses"]} new, '
        f'{stats["no_website"]} no-website, '
        f'{stats["qna_questions"]} Q&A. '
        f'API calls: {tracker.total} (est. ${tracker.summary()["estimated_cost_usd"]})'
    )
    return stats
