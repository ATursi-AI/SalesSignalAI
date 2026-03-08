"""
Apify-based Google Maps enhanced scraper for SalesSignal AI.

Replaces/supplements the Google Places API for outreach campaign
prospect scraping. Bypasses Google's 10,000 unit daily quota.
Richer data extraction including popular times, review highlights.

Also powers enhanced competitor review monitoring at $0.35/1K reviews.

Uses Apify's Google Maps Scraper actor. Dynamically builds search queries
from active BusinessProfile service areas. Works nationwide.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core.models.business import BusinessProfile
from core.models.monitoring import MonitorRun
from core.utils.apify_client import ApifyIntegration, ApifyError

logger = logging.getLogger(__name__)

# Apify actor for Google Maps scraping
ACTOR_ID = 'compass/crawler-google-places'

# Cooldown between runs
COOLDOWN_MINUTES = 360  # 6 hours


def _get_search_queries():
    """
    Build Google Maps search queries from active business service areas.
    Returns list of search query strings like 'plumber in Miami, FL'.
    """
    profiles = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    ).exclude(city='').exclude(state='').select_related('service_category')

    locations = set()
    service_types = set()

    for bp in profiles:
        if bp.city and bp.state:
            locations.add(f'{bp.city}, {bp.state}')
        if bp.service_category:
            service_types.add(bp.service_category.name.lower())

    if not service_types:
        service_types = {'plumber', 'electrician', 'contractor', 'HVAC', 'roofer'}

    queries = []
    for loc in list(locations)[:10]:
        for stype in list(service_types)[:5]:
            queries.append(f'{stype} in {loc}')

    return queries[:30]


def scrape_google_maps(search_queries=None, max_results_per_query=50,
                       include_reviews=False, max_reviews=0):
    """
    Scrape Google Maps business listings via Apify.

    Bypasses Google Places API daily quota limits.
    Returns richer data including popular times, review highlights.

    Args:
        search_queries: list of search strings (default: from active profiles)
        max_results_per_query: max places per search query
        include_reviews: also scrape reviews for each place
        max_reviews: max reviews per place (0 = skip reviews)

    Returns:
        dict with: items (list of place dicts), stats (counts)
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='google_maps_apify', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'google_maps_apify cooldown: {remaining}m remaining'
            logger.info(reason)
            return {'items': [], 'items_scraped': 0, 'skipped_reason': reason}

    # Initialize Apify client
    try:
        apify = ApifyIntegration()
    except ApifyError as e:
        logger.error(f'Apify not available: {e}')
        return {'items': [], 'items_scraped': 0, 'error': 'api_not_configured'}

    if search_queries is None:
        search_queries = _get_search_queries()

    if not search_queries:
        logger.warning('No search queries generated for Google Maps')
        return {'items': [], 'items_scraped': 0}

    run_input = {
        'searchStringsArray': search_queries,
        'maxCrawledPlacesPerSearch': max_results_per_query,
        'language': 'en',
        'maxReviews': max_reviews if include_reviews else 0,
    }

    logger.info(f'[Apify Google Maps] Searching {len(search_queries)} queries')

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=600)
    except ApifyError as e:
        logger.error(f'[Apify Google Maps] Actor run failed: {e}')
        return {'items': [], 'items_scraped': 0, 'error': str(e)}

    # Parse results into structured dicts
    places = []
    for item in items:
        place = {
            'name': item.get('title', '') or item.get('name', ''),
            'address': item.get('address', ''),
            'phone': item.get('phone', ''),
            'website': item.get('website', ''),
            'category': item.get('categoryName', '') or item.get('category', ''),
            'rating': item.get('totalScore', 0) or item.get('rating', 0),
            'review_count': item.get('reviewsCount', 0) or item.get('reviews', 0),
            'place_id': item.get('placeId', ''),
            'url': item.get('url', ''),
            'latitude': item.get('location', {}).get('lat', 0) if isinstance(item.get('location'), dict) else 0,
            'longitude': item.get('location', {}).get('lng', 0) if isinstance(item.get('location'), dict) else 0,
            'popular_times': item.get('popularTimesHistogram', {}),
            'opening_hours': item.get('openingHours', []),
            'reviews': item.get('reviews', []) if include_reviews else [],
        }
        if place['name']:
            places.append(place)

    logger.info(f'[Apify Google Maps] Scraped {len(places)} places')

    return {
        'items': places,
        'items_scraped': len(places),
        'queries_searched': len(search_queries),
    }


def scrape_google_reviews(place_urls, max_reviews=50):
    """
    Scrape Google Maps reviews for specific places via Apify.

    Powers enhanced competitor review monitoring at $0.35/1K reviews.

    Args:
        place_urls: list of Google Maps place URLs
        max_reviews: max reviews per place

    Returns:
        dict with: items (list of review dicts), stats
    """
    try:
        apify = ApifyIntegration()
    except ApifyError as e:
        logger.error(f'Apify not available: {e}')
        return {'items': [], 'items_scraped': 0, 'error': 'api_not_configured'}

    run_input = {
        'startUrls': [{'url': u} for u in place_urls],
        'maxReviews': max_reviews,
        'scrapeReviewsPersonalData': False,
    }

    logger.info(f'[Apify Google Reviews] Scraping reviews for {len(place_urls)} places')

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=300)
    except ApifyError as e:
        logger.error(f'[Apify Google Reviews] Actor run failed: {e}')
        return {'items': [], 'items_scraped': 0, 'error': str(e)}

    # Extract reviews from place results
    all_reviews = []
    for item in items:
        place_name = item.get('title', '') or item.get('name', '')
        place_url = item.get('url', '')
        reviews = item.get('reviews', [])
        if not isinstance(reviews, list):
            continue

        for review in reviews:
            all_reviews.append({
                'place_name': place_name,
                'place_url': place_url,
                'reviewer': review.get('name', '') or review.get('author', ''),
                'rating': review.get('stars', 0) or review.get('rating', 0),
                'text': review.get('text', '') or review.get('review', ''),
                'date': review.get('publishedAtDate', '') or review.get('date', ''),
                'response': review.get('responseFromOwnerText', ''),
            })

    logger.info(f'[Apify Google Reviews] Scraped {len(all_reviews)} reviews')

    return {
        'items': all_reviews,
        'items_scraped': len(all_reviews),
        'places_checked': len(items),
    }
