"""
BiggerPockets forum monitor for SalesSignal AI.
Scrapes Property Management, Landlording, and Maintenance/Rehab forums
for service request posts from landlords and property managers.
These are high-value B2B leads — recurring work across multiple properties.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.biggerpockets.com'

# BiggerPockets forum sections relevant to service needs
DEFAULT_FORUMS = [
    {'slug': '/forums/52', 'label': 'Property Management'},
    {'slug': '/forums/48', 'label': 'Landlording & Rental Properties'},
    {'slug': '/forums/50', 'label': 'Contractors'},
    {'slug': '/forums/67', 'label': 'Rehabbing & House Flipping'},
    {'slug': '/forums/51', 'label': 'General Real Estate Investing'},
    {'slug': '/forums/12', 'label': 'Multi-Family & Apartment Investing'},
]

# Service request signals from landlords/property managers
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend', 'recommendation',
    'contractor', 'plumber', 'electrician', 'hvac', 'handyman',
    'landscap', 'cleaning', 'cleaner', 'janitorial',
    'roofer', 'painter', 'flooring', 'drywall',
    'repair', 'maintenance', 'vendor', 'service provider',
    'property manager', 'property management',
    'tenant complaint', 'tenant issue', 'unit needs',
    'rehab crew', 'general contractor', 'gc',
    'who do you use', 'referral', 'reliable',
    'pest control', 'exterminator', 'mold', 'water damage',
    'emergency repair', 'turnover', 'make ready',
    'estimate', 'quote', 'bid',
]

# Location signals for NY/NJ/CT filtering
LOCATION_SIGNALS = [
    'new york', 'ny', 'nyc', 'manhattan', 'brooklyn', 'queens', 'bronx',
    'long island', 'nassau', 'suffolk', 'westchester',
    'new jersey', 'nj', 'jersey city', 'hoboken', 'newark',
    'connecticut', 'ct', 'stamford', 'bridgeport', 'new haven',
    'tri-state', 'tristate',
]


class BiggerPocketsScraper(BaseScraper):
    MONITOR_NAME = 'biggerpockets'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def scrape_bp_forum(scraper, forum_path):
    """
    Scrape a BiggerPockets forum page for thread listings.
    Returns list of dicts with: title, url, author, date, replies.
    """
    url = urljoin(BASE_URL, forum_path)

    resp = scraper.get(url)
    if resp is None:
        return []

    if resp.status_code == 404:
        logger.debug(f"BP forum not found: {url}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # BiggerPockets forum thread cards
    threads = soup.select(
        '.forum-topic-row, .topic-list-item, '
        'tr.topic-list-item, .forum-card, '
        '[data-topic-id], article.topic, .discussion-item'
    )

    if not threads:
        # Fallback: look for topic links
        links = soup.select('a[href*="/forums/"], a[href*="/topics/"]')
        for link in links:
            href = link.get('href', '')
            title = link.get_text(strip=True)
            if '/topics/' in href and title and len(title) > 15:
                if not href.startswith('http'):
                    href = urljoin(BASE_URL, href)
                results.append({
                    'title': title,
                    'url': href,
                    'author': '',
                    'date': '',
                    'replies': 0,
                })

    for thread in threads:
        try:
            link = thread.select_one(
                'a.topic-title, a[href*="/topics/"], '
                '.topic-title a, h3 a, h2 a'
            )
            if not link:
                continue

            href = link.get('href', '')
            if not href.startswith('http'):
                href = urljoin(BASE_URL, href)

            title = link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Author
            author_el = thread.select_one(
                '.topic-author, .username, .user-link, '
                'a[href*="/users/"]'
            )
            author = author_el.get_text(strip=True) if author_el else ''

            # Date
            date_el = thread.select_one('time, .topic-date, .date, .timestamp')
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            # Reply count
            replies = 0
            reply_el = thread.select_one('.topic-replies, .reply-count, .replies')
            if reply_el:
                import re
                nums = re.findall(r'\d+', reply_el.get_text(strip=True).replace(',', ''))
                if nums:
                    replies = int(nums[0])

            results.append({
                'title': title,
                'url': href,
                'author': author,
                'date': date_str,
                'replies': replies,
            })
        except Exception as e:
            logger.debug(f"Error parsing BP thread: {e}")
            continue

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)

    logger.info(f"Scraped {len(unique)} threads from {url}")
    return unique


def fetch_bp_topic_detail(scraper, url):
    """Fetch the full text of a BiggerPockets forum topic (first post)."""
    resp = scraper.get(url)
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    # First post body
    body_el = soup.select_one(
        '.topic-body, .post-body, .forum-post-content, '
        '[data-post-id] .content, article .body, .post-content'
    )
    body = body_el.get_text(separator='\n', strip=True) if body_el else ''

    title_el = soup.select_one('h1')
    title = title_el.get_text(strip=True) if title_el else ''

    author_el = soup.select_one('.post-author a, .username, a[href*="/users/"]')
    author = author_el.get_text(strip=True) if author_el else ''

    time_el = soup.select_one('time')
    posted_at = None
    if time_el and time_el.get('datetime'):
        try:
            posted_at = datetime.fromisoformat(time_el['datetime'].replace('Z', '+00:00'))
            if timezone.is_naive(posted_at):
                posted_at = timezone.make_aware(posted_at)
        except (ValueError, TypeError):
            pass

    return {
        'title': title,
        'body': body,
        'author': author,
        'posted_at': posted_at,
        'full_text': f"{title}\n\n{body}".strip(),
    }


def is_relevant_post(title, body=''):
    """Check if a post is a service request from a landlord/property manager."""
    combined = f"{title} {body}".lower()

    # Must match at least one service signal
    has_service_signal = any(s in combined for s in SERVICE_SIGNALS)
    if not has_service_signal:
        return False

    # Optionally check for location relevance (if body is available)
    # For title-only filtering, accept all service requests
    # since BiggerPockets users often don't mention location in title
    if body:
        has_location = any(loc in combined for loc in LOCATION_SIGNALS)
        # If we have the body, prefer location-filtered results
        # but still accept posts without location (they may be relevant)
        return True

    return True


def parse_date(date_str):
    """Parse a date string into timezone-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        return None


def monitor_biggerpockets(forums=None, max_per_forum=25, fetch_details=True,
                          max_age_hours=72, dry_run=False):
    """
    Main monitoring function. Scrapes BiggerPockets forums for service requests.

    Args:
        forums: List of forum dicts (default: DEFAULT_FORUMS)
        max_per_forum: Max threads to process per forum
        fetch_details: Whether to fetch full thread body
        max_age_hours: Skip posts older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = BiggerPocketsScraper()

    # Cooldown check
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"BiggerPockets monitor skipped: {reason}")
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    if forums is None:
        forums = DEFAULT_FORUMS

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize forum order
    forums = scraper.shuffle(forums)

    try:
        for forum in forums:
            if scraper.is_stopped:
                break

            forum_url = urljoin(BASE_URL, forum['slug'])
            logger.info(f"Scanning BiggerPockets: {forum['label']}")

            threads = scrape_bp_forum(scraper, forum['slug'])
            stats['scraped'] += len(threads)

            for thread in threads[:max_per_forum]:
                if scraper.is_stopped:
                    break

                try:
                    if not is_relevant_post(thread['title']):
                        continue

                    content = thread['title']
                    author = thread.get('author', '')
                    posted_at = parse_date(thread.get('date'))

                    if fetch_details and thread.get('url'):
                        detail = fetch_bp_topic_detail(scraper, thread['url'])
                        if detail:
                            content = detail['full_text'] or content
                            posted_at = detail.get('posted_at') or posted_at
                            author = detail.get('author') or author

                            # Re-check relevance with full body
                            if not is_relevant_post(thread['title'], detail.get('body', '')):
                                continue

                    if posted_at and posted_at < cutoff:
                        continue

                    content += f"\n(BiggerPockets: {forum['label']})"
                    source_url = thread.get('url', forum_url)

                    if dry_run:
                        logger.info(f"[DRY RUN] Would create lead: {thread['title'][:80]}")
                        stats['created'] += 1
                        continue

                    lead, created, num_assigned = process_lead(
                        platform='biggerpockets',
                        source_url=source_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'forum': forum['slug'],
                            'forum_label': forum['label'],
                            'replies': thread.get('replies', 0),
                            'listing_title': thread['title'],
                            'is_b2b': True,
                        },
                    )

                    if created:
                        stats['created'] += 1
                        stats['assigned'] += num_assigned
                    else:
                        stats['duplicates'] += 1

                except Exception as e:
                    logger.error(f"Error processing BP thread {thread.get('url', '?')}: {e}")
                    stats['errors'] += 1
                    continue

    except RateLimitHit as e:
        logger.warning(f"BiggerPockets monitor stopped early (rate limit): {e}")

    logger.info(f"BiggerPockets monitor complete: {stats}")
    return stats
