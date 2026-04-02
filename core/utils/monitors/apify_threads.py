"""
Apify-based Threads monitor for SalesSignal AI.

NEW source — Meta's growing text platform with 275M+ monthly users.
Local service discussions and recommendations are growing here.

Uses Apify's Threads Scraper actor. Dynamically builds search queries
from active BusinessProfile service areas. Works nationwide.
"""
import logging
from datetime import datetime, timedelta

from django.utils import timezone

from core.models.business import BusinessProfile
from core.models.monitoring import MonitorRun
from core.utils.apify_client import ApifyIntegration, ApifyError
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Apify actor for Threads scraping
ACTOR_ID = 'apidojo/threads-scraper'

# Cooldown between runs
COOLDOWN_MINUTES = 120  # 2 hours

# Service request signals
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend', 'recommendation',
    'who do you use', 'referral', 'reliable', 'affordable',
    'plumber', 'electrician', 'hvac', 'contractor', 'handyman',
    'landscap', 'cleaning', 'cleaner', 'roofer', 'painter',
    'flooring', 'drywall', 'pest control', 'exterminator',
    'mold', 'water damage', 'repair', 'renovation',
    'kitchen remodel', 'bathroom remodel',
    'gutter', 'siding', 'fence', 'deck', 'patio',
    'tree service', 'tree removal', 'snow removal',
    'moving company', 'movers', 'junk removal',
    'locksmith', 'garage door', 'window replacement',
    'looking to hire', 'need help finding',
    'any suggestions', 'can anyone recommend',
    'quote', 'estimate',
]


def _is_service_request(text):
    """Check if Threads post matches service-request signals."""
    text_lower = text.lower()
    return any(s in text_lower for s in SERVICE_SIGNALS)


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


def _get_search_queries():
    """
    Build Threads search queries from active business locations and services.
    Returns a list of search query strings.
    """
    profiles = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    ).exclude(city='').exclude(state='')

    locations = set()
    for bp in profiles:
        if bp.city and bp.state:
            locations.add(f'{bp.city} {bp.state}')

    service_terms = [
        'need a plumber',
        'need a contractor',
        'looking for electrician',
        'recommend handyman',
        'need HVAC repair',
        'looking for roofer',
        'need landscaper',
        'looking for painter',
        'need cleaning service',
        'looking for tree service',
    ]

    queries = []
    # Location-specific queries
    for loc in list(locations)[:8]:
        for term in service_terms[:3]:
            queries.append(f'{term} {loc}')

    # Generic service queries
    queries.extend(service_terms)

    return queries[:25]  # Cap to control costs


def monitor_threads(max_posts=50, max_age_hours=72, dry_run=False):
    """
    Monitor Threads for service request posts via Apify.

    Searches for local service discussions and recommendations.
    Dynamically builds queries from active BusinessProfile service areas.

    Args:
        max_posts: max posts to fetch per Apify run
        max_age_hours: skip posts older than this (default: 72h)
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: posts_scraped, created, duplicates, assigned, errors
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='threads', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'threads cooldown: {remaining}m remaining'
            logger.info(reason)
            return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                    'assigned': 0, 'errors': 0, 'skipped_reason': reason}

    # Initialize Apify client
    try:
        apify = ApifyIntegration()
    except ApifyError as e:
        logger.error(f'Apify not available: {e}')
        return {'error': 'api_not_configured'}

    queries = _get_search_queries()

    if not queries:
        logger.warning('No search queries generated for Threads')
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
        'searchTerms': queries,
        'maxItems': max_posts,
    }

    logger.info(f'[Apify Threads] Searching {len(queries)} queries')

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=300)
    except ApifyError as e:
        logger.error(f'[Apify Threads] Actor run failed: {e}')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0, 'error': str(e)}

    stats['posts_scraped'] = len(items)

    for item in items:
        try:
            text = (
                item.get('text') or item.get('caption') or
                item.get('content') or item.get('body') or ''
            )
            if not text or len(text) < 20:
                continue

            posted_at = _parse_timestamp(
                item.get('taken_at') or item.get('createdAt') or
                item.get('date') or item.get('timestamp')
            )

            # Skip old posts
            if posted_at and posted_at < cutoff:
                continue

            # Filter for service requests
            if not _is_service_request(text):
                continue

            author = (
                item.get('user', {}).get('username', '')
                if isinstance(item.get('user'), dict)
                else item.get('username', '') or item.get('author', '') or ''
            )

            post_url = item.get('url') or item.get('link') or 'https://www.threads.net'

            # Engagement metrics
            like_count = item.get('like_count', 0) or item.get('likes', 0) or 0
            reply_count = item.get('reply_count', 0) or item.get('replies', 0) or 0

            content = f"[Threads] {text[:2000]}"

            if dry_run:
                logger.info(f'[DRY RUN] Would create Threads lead: {text[:80]}')
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='threads',
                source_url=post_url,
                content=content,
                author=author,
                posted_at=posted_at,
                raw_data={
                    'post_id': str(item.get('id', '')),
                    'like_count': like_count,
                    'reply_count': reply_count,
                    'source': 'apify',
                },
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except Exception as e:
            logger.error(f'[Apify Threads] Error processing post: {e}')
            stats['errors'] += 1

    logger.info(f'Threads Apify monitor complete: {stats}')
    return stats
