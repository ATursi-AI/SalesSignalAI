"""
Apify-based TikTok monitor for SalesSignal AI.

NEW source — people post home disaster videos, renovation content, and
service requests on TikTok. Comments sections are full of people asking
"who did this work" and "I need someone for this."

Uses Apify's TikTok Scraper actor. Dynamically builds search queries
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

# Apify actor for TikTok scraping
ACTOR_ID = 'clockworks/free-tiktok-scraper'

# Cooldown between runs
COOLDOWN_MINUTES = 120  # 2 hours — TikTok content is less time-sensitive

# Service request signals for filtering TikTok content
SERVICE_SIGNALS = [
    'need a plumber', 'need a contractor', 'need an electrician',
    'looking for', 'anyone know', 'recommend', 'recommendation',
    'who did this', 'who do you use', 'help me find',
    'plumber', 'electrician', 'hvac', 'contractor', 'handyman',
    'landscap', 'cleaning', 'roofer', 'painter',
    'flooring', 'drywall', 'pest control',
    'water damage', 'mold', 'repair', 'renovation',
    'kitchen remodel', 'bathroom remodel',
    'tree service', 'tree removal', 'snow removal',
    'garage door', 'window replacement', 'fence',
    'home repair', 'house repair', 'fix my',
    'disaster', 'flood damage', 'storm damage',
    'before and after', 'transformation',
]


def _is_service_content(text):
    """Check if TikTok content matches service-related signals."""
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
    Build TikTok search queries from active business locations and services.
    Returns a list of search query strings.
    """
    profiles = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    ).exclude(city='').exclude(state='').select_related('service_category')

    locations = set()
    for bp in profiles:
        if bp.city and bp.state:
            locations.add(f'{bp.city} {bp.state}')

    # Service-related TikTok search terms
    service_terms = [
        'need a plumber',
        'need a contractor',
        'home repair help',
        'looking for handyman',
        'flood damage',
        'storm damage house',
        'kitchen remodel',
        'bathroom renovation',
        'roof repair',
        'tree removal',
    ]

    queries = []
    # Location-specific queries
    for loc in list(locations)[:8]:
        for term in service_terms[:3]:
            queries.append(f'{term} {loc}')

    # Generic high-engagement queries
    queries.extend(service_terms)

    return queries[:25]  # Cap to control Apify costs


def monitor_tiktok(max_videos=50, max_age_hours=72, dry_run=False):
    """
    Monitor TikTok for service request content via Apify.

    Searches for home repair, renovation, and service request content.
    Dynamically builds queries from active BusinessProfile service areas.

    Args:
        max_videos: max videos to fetch per Apify run
        max_age_hours: skip content older than this (default: 72h, TikTok is less urgent)
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: posts_scraped, created, duplicates, assigned, errors
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='tiktok', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'tiktok cooldown: {remaining}m remaining'
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
        logger.warning('No search queries generated for TikTok')
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
        'searchQueries': queries,
        'resultsPerPage': max_videos,
    }

    logger.info(f'[Apify TikTok] Searching {len(queries)} queries')

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=300)
    except ApifyError as e:
        logger.error(f'[Apify TikTok] Actor run failed: {e}')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0, 'error': str(e)}

    stats['posts_scraped'] = len(items)

    for item in items:
        try:
            # TikTok items can have description, text, or caption
            text = (
                item.get('text') or item.get('desc') or
                item.get('description') or item.get('caption') or ''
            )
            if not text or len(text) < 15:
                continue

            posted_at = _parse_timestamp(
                item.get('createTime') or item.get('created_at') or
                item.get('timestamp') or item.get('date')
            )

            # Skip old content
            if posted_at and posted_at < cutoff:
                continue

            # Filter for service-related content
            if not _is_service_content(text):
                continue

            author = (
                item.get('author', {}).get('uniqueId', '')
                if isinstance(item.get('author'), dict)
                else item.get('authorMeta', {}).get('name', '')
                if isinstance(item.get('authorMeta'), dict)
                else item.get('username', '') or ''
            )

            video_url = item.get('webVideoUrl') or item.get('url') or ''
            if not video_url and item.get('id'):
                video_url = f'https://www.tiktok.com/@{author}/video/{item["id"]}'

            # Engagement metrics
            play_count = item.get('playCount', 0) or item.get('plays', 0) or 0
            like_count = item.get('diggCount', 0) or item.get('likes', 0) or 0
            comment_count = item.get('commentCount', 0) or item.get('comments', 0) or 0

            content = f"[TikTok] {text[:2000]}"
            if play_count > 1000:
                content += f'\n(Views: {play_count:,})'

            if dry_run:
                logger.info(f'[DRY RUN] Would create TikTok lead: {text[:80]}')
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='tiktok',
                source_url=video_url or 'https://www.tiktok.com',
                content=content,
                author=author,
                posted_at=posted_at,
                raw_data={
                    'video_id': str(item.get('id', '')),
                    'play_count': play_count,
                    'like_count': like_count,
                    'comment_count': comment_count,
                    'source': 'apify',
                },
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except Exception as e:
            logger.error(f'[Apify TikTok] Error processing video: {e}')
            stats['errors'] += 1

    logger.info(f'TikTok Apify monitor complete: {stats}')
    return stats
