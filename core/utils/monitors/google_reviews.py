"""
Google Maps review monitor for SalesSignal AI.
Expands Google competitor monitoring beyond Q&A to include review scraping.
Uses Google Places API. Flags negative reviews and runs Claude opportunity analysis.
"""
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from core.models.competitors import TrackedCompetitor, CompetitorReview
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)


class GoogleReviewScraper(BaseScraper):
    MONITOR_NAME = 'google_reviews'
    DELAY_MIN = 2.0
    DELAY_MAX = 5.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 60
    RESPECT_ROBOTS = False  # It's an API, not a website


def get_place_reviews(scraper, place_id):
    """
    Fetch reviews for a place via Google Places API.
    Tries new API first, falls back to legacy.
    Returns (reviews_list, place_name, is_new_api).
    """
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        logger.warning("GOOGLE_MAPS_API_KEY not configured")
        return [], '', False

    # Try new Places API first
    url = f'https://places.googleapis.com/v1/places/{place_id}'
    headers = {
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': (
            'id,displayName,formattedAddress,rating,userRatingCount,'
            'reviews'
        ),
    }

    try:
        resp = scraper.get(url, headers=headers)
        if resp is not None and resp.status_code == 200:
            data = resp.json()
            name = data.get('displayName', {}).get('text', '') if isinstance(data.get('displayName'), dict) else data.get('displayName', '')
            reviews = []
            for rev in data.get('reviews', []):
                text = rev.get('text', {})
                if isinstance(text, dict):
                    text = text.get('text', '')
                reviews.append({
                    'author': rev.get('authorAttribution', {}).get('displayName', ''),
                    'rating': rev.get('rating', 0),
                    'text': text,
                    'time': rev.get('publishTime', ''),
                    'relative_time': rev.get('relativePublishTimeDescription', ''),
                })
            return reviews, name, True
    except RateLimitHit:
        raise
    except Exception as e:
        logger.debug(f"New Places API failed for {place_id}: {e}")

    # Fall back to legacy API
    url = 'https://maps.googleapis.com/maps/api/place/details/json'
    params = {
        'place_id': place_id,
        'fields': 'name,reviews,rating,user_ratings_total',
        'key': api_key,
    }

    try:
        resp = scraper.get(url, params=params)
        if resp is None:
            return [], '', False
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') != 'OK':
            logger.error(f"Legacy Places API status: {data.get('status')}")
            return [], '', False

        result = data.get('result', {})
        name = result.get('name', '')
        reviews = []
        for rev in result.get('reviews', []):
            reviews.append({
                'author': rev.get('author_name', ''),
                'rating': rev.get('rating', 0),
                'text': rev.get('text', ''),
                'time': rev.get('time', 0),
                'relative_time': rev.get('relative_time_description', ''),
            })
        return reviews, name, False
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f"Legacy Places API error for {place_id}: {e}")
        return [], '', False


def parse_review_time(time_value):
    """Parse review timestamp to timezone-aware datetime."""
    if not time_value:
        return None
    if isinstance(time_value, str):
        try:
            dt = datetime.fromisoformat(time_value.replace('Z', '+00:00'))
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except (ValueError, TypeError):
            return None
    if isinstance(time_value, (int, float)):
        try:
            return datetime.fromtimestamp(time_value, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    return None


def heuristic_opportunity_check(review_text):
    """Simple keyword-based opportunity detection."""
    text_lower = review_text.lower()
    signals = [
        'never showed', 'no show', 'didn\'t show',
        'looking for another', 'find someone else',
        'do not use', 'don\'t use', 'avoid', 'worst',
        'still broken', 'not fixed', 'need to find',
        'overcharged', 'rip off', 'scam',
        'never again', 'won\'t use again',
        'had to hire another', 'called another',
        'unfinished', 'incomplete', 'left us',
        'emergency', 'urgent', 'flooding', 'leaking',
    ]
    matched = [s for s in signals if s in text_lower]
    return {
        'is_opportunity': len(matched) > 0,
        'analysis': f"Matched: {', '.join(matched)}" if matched else 'No signals',
        'urgency': 'high' if any(w in text_lower for w in ['emergency', 'urgent', 'flooding']) else 'medium',
        'issue_type': 'no-show' if any(w in text_lower for w in ['no show', 'didn\'t show', 'never showed']) else 'quality',
    }


def analyze_review_opportunity(review_text, competitor_name):
    """Use Claude API or heuristic to analyze review opportunity."""
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        return heuristic_opportunity_check(review_text)

    try:
        import anthropic
        import json
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"Analyze this negative Google review of '{competitor_name}'. "
            f"Is the reviewer looking for an alternative service provider?\n\n"
            f"Review:\n\"{review_text}\"\n\n"
            f"Respond JSON only:\n"
            f'{{"is_opportunity": true/false, "reason": "brief", '
            f'"urgency": "high/medium/low", "issue_type": "no-show/quality/price/communication/other"}}'
        )

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=200,
            messages=[{'role': 'user', 'content': prompt}],
        )

        result_text = response.content[0].text.strip()
        if result_text.startswith('```'):
            result_text = result_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        result = json.loads(result_text)

        return {
            'is_opportunity': result.get('is_opportunity', False),
            'analysis': result.get('reason', ''),
            'urgency': result.get('urgency', 'low'),
            'issue_type': result.get('issue_type', 'other'),
        }
    except Exception as e:
        logger.warning(f"Claude API failed for Google review: {e}")
        return heuristic_opportunity_check(review_text)


def monitor_google_reviews(competitors=None, max_age_hours=168, dry_run=False):
    """
    Main monitoring function. Checks Google reviews on tracked competitor listings.

    Args:
        competitors: QuerySet of TrackedCompetitor (default: all active with google_place_id)
        max_age_hours: Skip reviews older than this (default: 7 days)
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts
    """
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        logger.error("GOOGLE_MAPS_API_KEY not configured — skipping Google Reviews monitor")
        return {'checked': 0, 'reviews_found': 0, 'opportunities': 0,
                'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    scraper = GoogleReviewScraper()

    # --- Cooldown check ---
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"Skipping Google Reviews monitor: {reason}")
        return {'checked': 0, 'reviews_found': 0, 'opportunities': 0,
                'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    if competitors is None:
        competitors = TrackedCompetitor.objects.filter(
            is_active=True,
        ).exclude(google_place_id='').select_related('business')

    # --- Shuffle competitors for randomized scraping order ---
    competitors = scraper.shuffle(competitors)

    stats = {
        'checked': 0, 'reviews_found': 0, 'opportunities': 0,
        'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
    }
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    for competitor in competitors:
        if scraper.is_stopped:
            logger.info("Scraper stopped (rate limit or request cap reached), ending run.")
            break

        logger.info(f"Checking Google reviews for: {competitor.name}")
        stats['checked'] += 1

        try:
            reviews, place_name, is_new_api = get_place_reviews(scraper, competitor.google_place_id)
        except RateLimitHit:
            logger.warning(f"Rate limit hit while fetching reviews for {competitor.name}")
            break

        stats['reviews_found'] += len(reviews)

        if not reviews:
            continue

        # Update competitor stats
        competitor.last_checked = timezone.now()
        competitor.save(update_fields=['last_checked'])

        for review in reviews:
            try:
                rating = review.get('rating', 0)

                # Only process negative reviews (1-2 stars)
                if rating > 2:
                    continue

                review_text = review.get('text', '')
                if not review_text:
                    continue

                posted_at = parse_review_time(review.get('time'))
                if posted_at and posted_at < cutoff:
                    continue

                analysis = analyze_review_opportunity(review_text, competitor.name)

                # Store CompetitorReview record
                CompetitorReview.objects.get_or_create(
                    competitor=competitor,
                    review_text=review_text,
                    defaults={
                        'platform': 'google',
                        'reviewer_name': review.get('author', ''),
                        'rating': rating,
                        'review_date': posted_at.date() if posted_at else None,
                        'is_negative': True,
                        'is_opportunity': analysis.get('is_opportunity', False),
                        'ai_analysis': analysis.get('analysis', ''),
                    },
                )

                if not analysis.get('is_opportunity'):
                    continue

                stats['opportunities'] += 1

                author = review.get('author', 'Anonymous')
                content = (
                    f"Opportunity: {rating}-star Google review on {competitor.name}\n\n"
                    f"Review by {author}:\n\"{review_text}\"\n\n"
                    f"AI Analysis: {analysis.get('analysis', '')}\n"
                    f"Issue: {analysis.get('issue_type', 'unknown')} | "
                    f"Urgency: {analysis.get('urgency', 'medium')}"
                )

                source_url = f"https://www.google.com/maps/place/?q=place_id:{competitor.google_place_id}"

                if dry_run:
                    logger.info(f"[DRY RUN] Would create lead: {rating}-star Google review on {competitor.name}")
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='google_reviews',
                    source_url=source_url,
                    content=content,
                    author=author,
                    posted_at=posted_at,
                    raw_data={
                        'competitor_id': competitor.id,
                        'competitor_name': competitor.name,
                        'place_id': competitor.google_place_id,
                        'rating': rating,
                        'ai_analysis': analysis,
                        'relative_time': review.get('relative_time', ''),
                    },
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except Exception as e:
                logger.error(f"Error processing Google review on {competitor.name}: {e}")
                stats['errors'] += 1
                continue

    logger.info(f"Google Reviews monitor complete: {stats}")
    return stats
