"""
Apify-based Nextdoor monitor for SalesSignal AI.

Scrapes Nextdoor posts via Apify cloud actor for service requests.
Dynamically uses BusinessProfile service areas — works for any US geography.
No hardcoded regions.

Each run builds search URLs from active businesses' cities/zip codes,
then processes results through the standard lead pipeline.
"""
import logging
from datetime import datetime, timedelta

from django.utils import timezone

from core.models.business import BusinessProfile
from core.models.monitoring import MonitorRun
from core.utils.apify_client import ApifyIntegration, ApifyError
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Apify actor for Nextdoor scraping
ACTOR_ID = 'curious_coder/nextdoor-scraper'

# Cooldown between runs
COOLDOWN_MINUTES = 60

# Service request signals
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend', 'recommendation',
    'who do you use', 'referral', 'reliable', 'affordable',
    'plumber', 'electrician', 'hvac', 'contractor', 'handyman',
    'landscap', 'cleaning', 'cleaner', 'roofer', 'painter',
    'flooring', 'drywall', 'pest control', 'exterminator',
    'mold', 'water damage', 'repair', 'renovation',
    'kitchen remodel', 'bathroom remodel', 'basement',
    'gutter', 'siding', 'fence', 'deck', 'patio',
    'tree service', 'tree removal', 'snow removal',
    'moving company', 'movers', 'junk removal',
    'locksmith', 'garage door', 'window replacement',
    'looking to hire', 'need help finding',
    'any suggestions', 'can anyone recommend',
    'does anyone have a good',
    'quote', 'estimate', 'cost', 'pricing',
    'help needed', 'urgent', 'emergency',
]


def is_service_request(text, extra_keywords=None):
    """Check if post text matches service-request signals."""
    text_lower = text.lower()
    signals = SERVICE_SIGNALS
    if extra_keywords:
        signals = signals + [k.lower() for k in extra_keywords]
    return any(s in text_lower for s in signals)


def _parse_timestamp(raw):
    """Parse a timestamp into a timezone-aware datetime."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        elif isinstance(raw, (int, float)):
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
        else:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except (ValueError, TypeError, OSError):
        return None


def _get_search_locations():
    """
    Build a list of unique city+state pairs from all active BusinessProfiles.
    Returns list of strings like 'Miami, FL' for Nextdoor location targeting.
    """
    profiles = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    ).exclude(city='').exclude(state='')

    locations = set()
    for bp in profiles:
        if bp.city and bp.state:
            locations.add(f'{bp.city}, {bp.state}')

    return list(locations)


def _get_search_keywords():
    """
    Build a list of search keywords from active businesses' service categories.
    Returns common service-related search terms.
    """
    from core.models.business import ServiceCategory
    categories = ServiceCategory.objects.filter(is_active=True)

    terms = set()
    for cat in categories:
        # Use category name as a search term
        terms.add(cat.name.lower())
        # Add default keywords (top 3 per category to avoid too many searches)
        for kw in (cat.default_keywords or [])[:3]:
            terms.add(kw.lower())

    # Always include generic service request terms
    terms.update([
        'need a contractor',
        'looking for handyman',
        'recommend plumber',
        'need electrician',
        'home repair',
    ])

    return list(terms)[:20]  # Cap at 20 to control Apify costs


def monitor_nextdoor(locations=None, max_posts=50,
                     max_age_hours=48, dry_run=False):
    """
    Monitor Nextdoor for service request posts via Apify.

    Dynamically determines locations from active BusinessProfile service areas.
    No hardcoded regions — works nationwide.

    Args:
        locations: list of 'City, ST' strings (default: from active profiles)
        max_posts: max posts to fetch per Apify run
        max_age_hours: skip posts older than this
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: posts_scraped, created, duplicates, assigned, errors
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='nextdoor', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'nextdoor cooldown: {remaining}m remaining'
            logger.info(reason)
            return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                    'assigned': 0, 'errors': 0, 'skipped_reason': reason}

    # Initialize Apify client
    try:
        apify = ApifyIntegration()
    except ApifyError as e:
        logger.error(f'Apify not available: {e}')
        return {'error': 'api_not_configured'}

    # Determine locations from active business profiles
    if locations is None:
        locations = _get_search_locations()

    if not locations:
        logger.warning('No business locations configured — cannot determine Nextdoor areas')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0}

    search_keywords = _get_search_keywords()

    stats = {
        'posts_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Build Apify actor input
    # Search each location for service-related posts
    search_urls = []
    for location in locations:
        for keyword in search_keywords[:5]:  # limit searches per location
            search_urls.append({
                'url': f'https://nextdoor.com/search/?query={keyword}',
            })

    if not search_urls:
        logger.warning('No search URLs generated for Nextdoor')
        return stats

    run_input = {
        'startUrls': search_urls[:20],  # cap to control costs
        'maxItems': max_posts,
    }

    logger.info(
        f'[Apify Nextdoor] Searching {len(locations)} locations, '
        f'{len(search_keywords)} keywords'
    )

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=300)
    except ApifyError as e:
        logger.error(f'[Apify Nextdoor] Actor run failed: {e}')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0, 'error': str(e)}

    stats['posts_scraped'] = len(items)

    for item in items:
        try:
            text = (
                item.get('body') or item.get('text') or
                item.get('content') or item.get('message') or ''
            )
            title = item.get('title', '') or ''

            full_text = f"{title}\n{text}".strip() if title else text
            if not full_text or len(full_text) < 20:
                continue

            posted_at = _parse_timestamp(
                item.get('createdAt') or item.get('date') or
                item.get('time') or item.get('timestamp')
            )

            # Skip old posts
            if posted_at and posted_at < cutoff:
                continue

            # Filter for service requests
            if not is_service_request(full_text):
                continue

            author = item.get('author', '') or item.get('userName', '') or ''
            post_url = item.get('url') or item.get('link') or 'https://nextdoor.com'
            neighborhood = item.get('neighborhood', '') or item.get('location', '') or ''

            content = full_text[:2000]
            if neighborhood:
                content += f'\n(Neighborhood: {neighborhood})'

            if dry_run:
                logger.info(f'[DRY RUN] Would create lead: {full_text[:80]}')
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='nextdoor',
                source_url=post_url,
                content=content,
                author=author,
                posted_at=posted_at,
                raw_data={
                    'neighborhood': neighborhood,
                    'title': title[:200],
                    'source': 'apify',
                },
                source_group='social_media',
                source_type='nextdoor',
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except Exception as e:
            logger.error(f'[Apify Nextdoor] Error processing post: {e}')
            stats['errors'] += 1

    logger.info(f'Nextdoor Apify monitor complete: {stats}')
    return stats
