"""
Apify-based Quora monitor for SalesSignal AI.

NEW source — people ask "best plumber in [city]" and "how to find a good
contractor in [area]" on Quora. Very high intent — someone asking on Quora
is actively researching service providers.

Uses Apify's Quora Scraper actor. Dynamically builds search queries
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

# Apify actor for Quora scraping
ACTOR_ID = 'curious_coder/quora-scraper'

# Cooldown between runs
COOLDOWN_MINUTES = 120  # 2 hours — Quora content is less time-sensitive

# High-intent question patterns
INTENT_SIGNALS = [
    'best plumber', 'best electrician', 'best contractor',
    'best handyman', 'best roofer', 'best painter',
    'best landscap', 'best cleaning', 'best hvac',
    'good plumber', 'good electrician', 'good contractor',
    'good handyman', 'good roofer', 'good painter',
    'find a plumber', 'find an electrician', 'find a contractor',
    'find a handyman', 'find a roofer', 'find a painter',
    'how to find', 'how to choose', 'how much does',
    'recommend', 'recommendation', 'looking for',
    'need a plumber', 'need an electrician', 'need a contractor',
    'hire a plumber', 'hire an electrician', 'hire a contractor',
    'who is the best', 'where can I find',
    'plumber in', 'electrician in', 'contractor in',
    'hvac in', 'roofer in', 'painter in',
    'landscaper in', 'handyman in', 'cleaning service in',
    'home repair', 'home improvement', 'renovation',
    'remodel', 'water damage', 'mold',
    'pest control', 'tree service', 'roofing',
]


def _is_high_intent(text):
    """Check if Quora question/answer matches high-intent service signals."""
    text_lower = text.lower()
    return any(s in text_lower for s in INTENT_SIGNALS)


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
    Build Quora search queries from active business locations and services.
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

    # High-intent Quora search terms
    service_terms = [
        'best plumber',
        'best contractor',
        'best electrician',
        'best handyman',
        'best roofer',
        'best landscaper',
        'best HVAC',
        'best cleaning service',
        'best painter',
        'best pest control',
    ]

    queries = []
    # Location-specific queries (highest intent)
    for loc in list(locations)[:10]:
        for term in service_terms[:5]:
            queries.append(f'{term} in {loc}')

    # Generic questions (still high intent)
    queries.extend([
        'how to find a good plumber',
        'how to find a good contractor',
        'how to choose an electrician',
        'best way to find a handyman',
        'how much does a plumber cost',
        'how much does a contractor charge',
        'how to find a reliable roofer',
        'best home renovation contractor',
        'how to find pest control near me',
        'how to find good landscaper',
    ])

    return queries[:30]  # Cap to control Apify costs


def monitor_quora(max_questions=50, max_age_hours=168, dry_run=False):
    """
    Monitor Quora for service recommendation questions via Apify.

    Searches for high-intent questions about finding service providers.
    Dynamically builds queries from active BusinessProfile service areas.

    Args:
        max_questions: max questions to fetch per Apify run
        max_age_hours: skip questions older than this (default: 168h/7 days,
                       Quora questions stay relevant longer)
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: posts_scraped, created, duplicates, assigned, errors
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='quora', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'quora cooldown: {remaining}m remaining'
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
        logger.warning('No search queries generated for Quora')
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
        'maxItems': max_questions,
    }

    logger.info(f'[Apify Quora] Searching {len(queries)} queries')

    try:
        items = apify.run_actor(ACTOR_ID, run_input, timeout_secs=300)
    except ApifyError as e:
        logger.error(f'[Apify Quora] Actor run failed: {e}')
        return {'posts_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0, 'error': str(e)}

    stats['posts_scraped'] = len(items)

    for item in items:
        try:
            # Quora items typically have question title and answer text
            question = item.get('question', '') or item.get('title', '') or ''
            answer = item.get('answer', '') or item.get('text', '') or item.get('content', '') or ''

            full_text = f"{question}\n{answer}".strip() if question else answer
            if not full_text or len(full_text) < 20:
                continue

            posted_at = _parse_timestamp(
                item.get('createdAt') or item.get('date') or
                item.get('timestamp') or item.get('time')
            )

            # Skip old questions
            if posted_at and posted_at < cutoff:
                continue

            # Filter for high-intent service questions
            if not _is_high_intent(full_text):
                continue

            author = item.get('author', '') or item.get('userName', '') or ''
            question_url = item.get('url') or item.get('link') or 'https://www.quora.com'

            # Build lead content
            content_parts = ['[Quora]']
            if question:
                content_parts.append(f'Q: {question[:500]}')
            if answer:
                content_parts.append(f'A: {answer[:500]}')
            content = '\n'.join(content_parts)

            # Answer count and follower count indicate engagement
            answer_count = item.get('answerCount', 0) or item.get('answers', 0) or 0
            follower_count = item.get('followerCount', 0) or item.get('followers', 0) or 0

            if dry_run:
                logger.info(f'[DRY RUN] Would create Quora lead: {question[:80] or full_text[:80]}')
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='quora',
                source_url=question_url,
                content=content,
                author=author,
                posted_at=posted_at,
                raw_data={
                    'question': question[:300],
                    'answer_count': answer_count,
                    'follower_count': follower_count,
                    'source': 'apify',
                },
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except Exception as e:
            logger.error(f'[Apify Quora] Error processing question: {e}')
            stats['errors'] += 1

    logger.info(f'Quora Apify monitor complete: {stats}')
    return stats
