"""
Apify-based Twitter/X monitor for SalesSignal AI.

Uses Apify's Tweet Scraper V2 actor to search Twitter for service requests.
Replaces the need for a $100/month X API subscription.

Dynamically builds search queries from active BusinessProfile service areas
and service categories. Works for any US geography — no hardcoded regions.
"""
import logging
from datetime import datetime, timedelta

from django.utils import timezone

from core.models.business import BusinessProfile, ServiceCategory
from core.models.monitoring import MonitorRun
from core.utils.apify_client import ApifyIntegration, ApifyError
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Apify actor for Twitter scraping
ACTOR_ID = 'apidojo/tweet-scraper'

# Cooldown between runs
COOLDOWN_MINUTES = 60

# Service request signals for filtering tweets
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
    'help needed', 'urgent', 'emergency',
]


def _is_service_request(text):
    """Check if tweet text matches service-request signals."""
    text_lower = text.lower()
    return any(s in text_lower for s in SERVICE_SIGNALS)


def _parse_timestamp(raw):
    """Parse a timestamp into a timezone-aware datetime."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            # Twitter dates: "Wed Oct 10 20:19:24 +0000 2018" or ISO format
            for fmt in (
                '%a %b %d %H:%M:%S %z %Y',
                '%Y-%m-%dT%H:%M:%S.%fZ',
                '%Y-%m-%dT%H:%M:%SZ',
            ):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return dt
        elif isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass
    return None


def _get_search_locations():
    """Build list of unique city+state pairs from active BusinessProfiles."""
    profiles = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    ).exclude(city='').exclude(state='')

    locations = set()
    for bp in profiles:
        if bp.city and bp.state:
            locations.add(f'{bp.city} {bp.state}')

    return list(locations)


def _get_search_queries(locations):
    """
    Build Twitter search queries combining service terms with locations.
    Returns a list of search query strings.
    """
    service_terms = [
        'need a plumber',
        'need a contractor',
        'looking for electrician',
        'recommend handyman',
        'need HVAC',
        'looking for roofer',
        'need landscaper',
        'looking for painter',
        'need cleaning service',
        'looking for tree service',
    ]

    queries = []
    for loc in locations[:10]:  # Cap locations to control Apify costs
        for term in service_terms[:5]:  # Cap terms per location
            queries.append(f'{term} {loc}')

    # Also add generic high-intent queries
    queries.extend([
        '"need a plumber" near me',
        '"looking for contractor" near me',
        '"need electrician" emergency',
        '"recommend a handyman"',
        '"looking for roofer"',
    ])

    return queries[:30]  # Cap total queries


def monitor_twitter(locations=None, max_tweets=100,
                    max_age_hours=48, dry_run=False):
    """
    Monitor Twitter/X for service request tweets via Apify.

    Dynamically determines locations from active BusinessProfile service areas.
    No hardcoded regions — works nationwide.

    Args:
        locations: list of 'City ST' strings (default: from active profiles)
        max_tweets: max tweets to fetch per Apify run
        max_age_hours: skip tweets older than this
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: posts_scraped, created, duplicates, assigned, errors
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='twitter_apify', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'twitter_apify cooldown: {remaining}m remaining'
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
        logger.warning('No business locations configured — cannot build Twitter queries')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0}

    queries = _get_search_queries(locations)

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
        'maxTweets': max_tweets,
        'sort': 'Latest',
    }

    logger.info(
        f'[Apify Twitter] Searching {len(queries)} queries '
        f'across {len(locations)} locations'
    )

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=300)
    except ApifyError as e:
        logger.error(f'[Apify Twitter] Actor run failed: {e}')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0, 'error': str(e)}

    stats['posts_scraped'] = len(items)

    for item in items:
        try:
            text = (
                item.get('full_text') or item.get('text') or
                item.get('content') or item.get('tweet_text') or ''
            )
            if not text or len(text) < 20:
                continue

            posted_at = _parse_timestamp(
                item.get('created_at') or item.get('date') or
                item.get('timestamp') or item.get('createdAt')
            )

            # Skip old tweets
            if posted_at and posted_at < cutoff:
                continue

            # Filter for service requests
            if not _is_service_request(text):
                continue

            author = (
                item.get('user', {}).get('screen_name', '')
                if isinstance(item.get('user'), dict)
                else item.get('username', '') or item.get('author', '')
            )

            tweet_url = item.get('url') or item.get('tweet_url') or ''
            if not tweet_url and item.get('id_str'):
                tweet_url = f'https://x.com/i/status/{item["id_str"]}'

            location_text = (
                item.get('user', {}).get('location', '')
                if isinstance(item.get('user'), dict)
                else item.get('location', '')
            ) or ''

            content = text[:2000]
            if location_text:
                content += f'\n(Location: {location_text})'

            if dry_run:
                logger.info(f'[DRY RUN] Would create Twitter lead: {text[:80]}')
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='twitter',
                source_url=tweet_url or 'https://x.com',
                content=content,
                author=author,
                posted_at=posted_at,
                raw_data={
                    'tweet_id': item.get('id_str', ''),
                    'user_location': location_text,
                    'retweet_count': item.get('retweet_count', 0),
                    'favorite_count': item.get('favorite_count', 0),
                    'source': 'apify',
                },
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except Exception as e:
            logger.error(f'[Apify Twitter] Error processing tweet: {e}')
            stats['errors'] += 1

    logger.info(f'Twitter Apify monitor complete: {stats}')
    return stats
