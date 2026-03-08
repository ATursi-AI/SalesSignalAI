"""
Apify-based Trustpilot monitor for SalesSignal AI.

NEW source — competitor review monitoring on Trustpilot.
Same pattern as Yelp/Angi: negative reviews = opportunity signals.
When a competitor gets a bad review, that customer needs an alternative.

Uses Apify's Trustpilot Scraper actor. Works nationwide.
"""
import logging
from datetime import datetime, timedelta

from django.utils import timezone

from core.models.business import BusinessProfile
from core.models.monitoring import MonitorRun
from core.utils.apify_client import ApifyIntegration, ApifyError
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Apify actor for Trustpilot scraping
ACTOR_ID = 'curious_coder/trustpilot-scraper'

# Cooldown between runs
COOLDOWN_MINUTES = 360  # 6 hours — reviews don't change rapidly

# Negative review signals that indicate the reviewer needs a new provider
OPPORTUNITY_SIGNALS = [
    'terrible', 'horrible', 'worst', 'awful', 'never again',
    'do not use', "don't use", 'avoid', 'scam', 'rip off', 'ripoff',
    'unprofessional', 'unreliable', 'no show', 'no-show',
    'damaged', 'broke', 'ruined', 'destroyed',
    'overcharged', 'overpriced', 'too expensive',
    'didn\'t fix', 'didn\'t work', 'still broken',
    'wouldn\'t recommend', 'would not recommend',
    'looking for alternative', 'need a new', 'switching',
    'never coming back', 'fired them', 'cancelled',
    'unresponsive', 'ignored', 'ghosted',
    'poor quality', 'shoddy', 'cheap work',
    'took my money', 'stole', 'fraud',
    'health hazard', 'unsafe', 'dangerous',
    'incompetent', 'clueless', 'inexperienced',
    '1 star', 'one star', 'zero stars',
]


def _is_negative_review(text, rating=None):
    """Check if review indicates a dissatisfied customer seeking alternatives."""
    # Low rating is a strong signal
    if rating is not None and rating <= 2:
        return True

    text_lower = text.lower()
    return any(s in text_lower for s in OPPORTUNITY_SIGNALS)


def _parse_timestamp(raw):
    """Parse a timestamp into a timezone-aware datetime."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return dt
        elif isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass
    return None


def _get_competitor_urls():
    """
    Build Trustpilot URLs for competitors of active businesses.
    Returns list of Trustpilot company page URLs.
    """
    profiles = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    ).select_related('service_category')

    # Collect competitor Trustpilot URLs from raw_data/config
    urls = set()
    for bp in profiles:
        # Check if business has configured competitor URLs
        config = bp.raw_data if hasattr(bp, 'raw_data') and bp.raw_data else {}
        competitor_urls = config.get('trustpilot_competitors', [])
        for url in competitor_urls:
            if 'trustpilot.com' in url:
                urls.add(url)

    return list(urls)


def monitor_trustpilot(company_urls=None, max_reviews=50,
                       max_age_hours=168, dry_run=False):
    """
    Monitor Trustpilot for negative competitor reviews via Apify.

    Negative reviews on competitor profiles = opportunity signals.
    A customer who left a 1-star review is actively seeking alternatives.

    Args:
        company_urls: list of Trustpilot company URLs to monitor
                     (default: from active business competitor configs)
        max_reviews: max reviews to fetch per company
        max_age_hours: skip reviews older than this (default: 168h/7 days)
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: posts_scraped, created, duplicates, assigned, errors
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='trustpilot', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'trustpilot cooldown: {remaining}m remaining'
            logger.info(reason)
            return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                    'assigned': 0, 'errors': 0, 'skipped_reason': reason}

    # Initialize Apify client
    try:
        apify = ApifyIntegration()
    except ApifyError as e:
        logger.error(f'Apify not available: {e}')
        return {'error': 'api_not_configured'}

    # Determine which companies to monitor
    if company_urls is None:
        company_urls = _get_competitor_urls()

    if not company_urls:
        logger.info('No Trustpilot competitor URLs configured')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0}

    stats = {
        'posts_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    run_input = {
        'startUrls': [{'url': u} for u in company_urls],
        'maxReviews': max_reviews,
    }

    logger.info(f'[Apify Trustpilot] Monitoring {len(company_urls)} companies')

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=300)
    except ApifyError as e:
        logger.error(f'[Apify Trustpilot] Actor run failed: {e}')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0, 'error': str(e)}

    stats['posts_scraped'] = len(items)

    for item in items:
        try:
            review_text = (
                item.get('text') or item.get('reviewBody') or
                item.get('content') or item.get('review') or ''
            )
            title = item.get('title') or item.get('reviewTitle') or ''

            full_text = f"{title}\n{review_text}".strip() if title else review_text
            if not full_text or len(full_text) < 15:
                continue

            posted_at = _parse_timestamp(
                item.get('date') or item.get('createdAt') or
                item.get('publishedDate') or item.get('timestamp')
            )

            # Skip old reviews
            if posted_at and posted_at < cutoff:
                continue

            # Get rating
            rating = item.get('rating') or item.get('stars') or item.get('score')
            try:
                rating = float(rating) if rating is not None else None
            except (ValueError, TypeError):
                rating = None

            # Filter for negative reviews (opportunity signals)
            if not _is_negative_review(full_text, rating):
                continue

            author = item.get('author', '') or item.get('reviewer', '') or item.get('userName', '') or ''
            if isinstance(author, dict):
                author = author.get('name', '') or author.get('username', '') or ''

            company_name = item.get('companyName', '') or item.get('businessName', '') or ''
            review_url = item.get('url') or item.get('link') or 'https://www.trustpilot.com'
            location = item.get('location', '') or item.get('reviewerLocation', '') or ''

            # Build lead content
            content_parts = [f'[Trustpilot Review — {company_name}]' if company_name else '[Trustpilot Review]']
            if rating is not None:
                content_parts.append(f'Rating: {rating}/5')
            if title:
                content_parts.append(f'Title: {title[:200]}')
            content_parts.append(review_text[:1500])
            if location:
                content_parts.append(f'(Reviewer location: {location})')
            content = '\n'.join(content_parts)

            if dry_run:
                logger.info(
                    f'[DRY RUN] Would create Trustpilot lead: '
                    f'{company_name} - {rating}/5 - {title[:60]}'
                )
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='trustpilot',
                source_url=review_url,
                content=content,
                author=author,
                posted_at=posted_at,
                raw_data={
                    'company_name': company_name,
                    'rating': rating,
                    'title': title[:200],
                    'reviewer_location': location,
                    'source': 'apify',
                },
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except Exception as e:
            logger.error(f'[Apify Trustpilot] Error processing review: {e}')
            stats['errors'] += 1

    logger.info(f'Trustpilot Apify monitor complete: {stats}')
    return stats
