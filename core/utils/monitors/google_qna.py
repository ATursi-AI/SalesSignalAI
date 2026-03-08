"""
Google Business Q&A monitor for SalesSignal AI.
Uses Google Places API to check Q&A on TrackedCompetitor listings.
When someone asks a question on a competitor's listing, that's a potential lead signal.
"""
import logging
import random
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from core.models.competitors import TrackedCompetitor
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)


class GoogleQnaScraper(BaseScraper):
    MONITOR_NAME = 'google_qna'
    DELAY_MIN = 2.0
    DELAY_MAX = 5.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 60
    RESPECT_ROBOTS = False  # It's an API, not a website


def get_place_details(scraper, place_id):
    """
    Fetch place details from Google Places API including Q&A.
    Returns place details dict or None.
    """
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        logger.warning("GOOGLE_MAPS_API_KEY not configured")
        return None

    # Places API (New) — Place Details
    url = 'https://places.googleapis.com/v1/places/' + place_id
    headers = {
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': (
            'id,displayName,formattedAddress,rating,userRatingCount,'
            'reviews,websiteUri,nationalPhoneNumber'
        ),
    }

    try:
        resp = scraper.get(url, headers=headers)
        if resp is None:
            return None
        resp.raise_for_status()
        return resp.json()
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f"Google Places API error for {place_id}: {e}")
        return None


def get_place_details_legacy(scraper, place_id):
    """
    Fetch place details from legacy Google Places API.
    Falls back to this if the new API doesn't work.
    """
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        return None

    url = 'https://maps.googleapis.com/maps/api/place/details/json'
    params = {
        'place_id': place_id,
        'fields': 'name,formatted_address,rating,user_ratings_total,reviews,website,formatted_phone_number',
        'key': api_key,
    }

    try:
        resp = scraper.get(url, params=params)
        if resp is None:
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == 'OK':
            return data.get('result', {})
        logger.error(f"Places API returned status: {data.get('status')}")
        return None
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f"Legacy Places API error for {place_id}: {e}")
        return None


def extract_reviews_from_place(place_data, is_new_api=True):
    """
    Extract reviews from place details response.
    Returns list of review dicts.
    """
    reviews = []

    if is_new_api:
        raw_reviews = place_data.get('reviews', [])
        for rev in raw_reviews:
            reviews.append({
                'author': rev.get('authorAttribution', {}).get('displayName', ''),
                'rating': rev.get('rating', 0),
                'text': rev.get('text', {}).get('text', '') if isinstance(rev.get('text'), dict) else rev.get('text', ''),
                'time': rev.get('publishTime', ''),
                'relative_time': rev.get('relativePublishTimeDescription', ''),
            })
    else:
        raw_reviews = place_data.get('reviews', [])
        for rev in raw_reviews:
            reviews.append({
                'author': rev.get('author_name', ''),
                'rating': rev.get('rating', 0),
                'text': rev.get('text', ''),
                'time': rev.get('time', 0),  # Unix timestamp
                'relative_time': rev.get('relative_time_description', ''),
            })

    return reviews


def parse_review_time(time_value):
    """Parse review timestamp to timezone-aware datetime."""
    if not time_value:
        return None

    # ISO format string (new API)
    if isinstance(time_value, str):
        try:
            dt = datetime.fromisoformat(time_value.replace('Z', '+00:00'))
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except (ValueError, TypeError):
            return None

    # Unix timestamp (legacy API)
    if isinstance(time_value, (int, float)):
        try:
            return datetime.fromtimestamp(time_value, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    return None


def monitor_google_qna(competitors=None, max_age_hours=168, dry_run=False):
    """
    Main monitoring function. Checks Google Q&A/reviews on tracked competitor listings.

    When someone posts a question or leaves a negative review on a competitor's listing,
    that's a potential lead — they're looking for the service and may not be satisfied.

    Args:
        competitors: QuerySet of TrackedCompetitor (default: all active with google_place_id)
        max_age_hours: Skip reviews older than this (default: 7 days)
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: checked, created, duplicates, assigned
    """
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        logger.error("GOOGLE_MAPS_API_KEY not configured — skipping Google Q&A monitor")
        return {'checked': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    scraper = GoogleQnaScraper()

    # --- Cooldown check ---
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"Skipping Google Q&A monitor: {reason}")
        return {'checked': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    if competitors is None:
        competitors = TrackedCompetitor.objects.filter(
            is_active=True,
        ).exclude(google_place_id='').select_related('business')

    # --- Shuffle competitors for randomized scraping order ---
    competitors = scraper.shuffle(competitors)

    stats = {'checked': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    for competitor in competitors:
        if scraper.is_stopped:
            logger.info("Scraper stopped (rate limit or request cap reached), ending run.")
            break

        logger.info(f"Checking Google Q&A for: {competitor.name} ({competitor.google_place_id})")
        stats['checked'] += 1

        try:
            # Try new API first, fall back to legacy
            place_data = get_place_details(scraper, competitor.google_place_id)
            is_new_api = True
            if not place_data:
                place_data = get_place_details_legacy(scraper, competitor.google_place_id)
                is_new_api = False
        except RateLimitHit:
            logger.warning(f"Rate limit hit while fetching place details for {competitor.name}")
            break

        if not place_data:
            stats['errors'] += 1
            continue

        # Update competitor's current stats
        if is_new_api:
            competitor.current_google_rating = place_data.get('rating')
            competitor.current_review_count = place_data.get('userRatingCount')
        else:
            competitor.current_google_rating = place_data.get('rating')
            competitor.current_review_count = place_data.get('user_ratings_total')
        competitor.last_checked = timezone.now()
        competitor.save(update_fields=['current_google_rating', 'current_review_count', 'last_checked'])

        # Extract reviews (Google Places API returns Q&A mixed with reviews)
        reviews = extract_reviews_from_place(place_data, is_new_api)

        for review in reviews:
            try:
                posted_at = parse_review_time(review.get('time'))

                # Skip old reviews
                if posted_at and posted_at < cutoff:
                    continue

                review_text = review.get('text', '')
                rating = review.get('rating', 0)

                if not review_text:
                    continue

                # Focus on questions (no rating / low rating) and negative reviews
                # Questions on Google Q&A typically have no rating
                is_question = rating == 0
                is_negative = 1 <= rating <= 2
                is_opportunity = is_question or is_negative

                if not is_opportunity:
                    continue

                # Build content
                author = review.get('author', 'Anonymous')
                competitor_name = competitor.name

                if is_question:
                    content = f"Question on {competitor_name}'s Google listing:\n\n{review_text}"
                else:
                    content = (
                        f"Negative review ({rating} star) on {competitor_name}'s Google listing:\n\n"
                        f"{review_text}\n\n"
                        f"Reviewer: {author}"
                    )

                # Build source URL
                source_url = f"https://www.google.com/maps/place/?q=place_id:{competitor.google_place_id}"

                if dry_run:
                    label = "Question" if is_question else f"{rating}-star review"
                    logger.info(f"[DRY RUN] Would create lead: {label} on {competitor_name}")
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='google_qna',
                    source_url=source_url,
                    content=content,
                    author=author,
                    posted_at=posted_at,
                    raw_data={
                        'competitor_id': competitor.id,
                        'competitor_name': competitor_name,
                        'place_id': competitor.google_place_id,
                        'rating': rating,
                        'is_question': is_question,
                        'is_negative': is_negative,
                        'relative_time': review.get('relative_time', ''),
                    },
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except Exception as e:
                logger.error(f"Error processing review on {competitor.name}: {e}")
                stats['errors'] += 1
                continue

    logger.info(f"Google Q&A monitor complete: {stats}")
    return stats
