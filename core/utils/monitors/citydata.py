"""
City-Data.com forum monitor for SalesSignal AI.
Scrapes local sub-forums for threads matching service keywords.
City-Data has massive forum communities with local sub-forums for every state/metro.
"""
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.city-data.com/forum/'

# City-Data sub-forums for the tri-state area
DEFAULT_FORUMS = [
    {'slug': 'new-york-city/', 'label': 'New York City'},
    {'slug': 'new-york/', 'label': 'New York State'},
    {'slug': 'long-island/', 'label': 'Long Island'},
    {'slug': 'new-jersey/', 'label': 'New Jersey'},
    {'slug': 'connecticut/', 'label': 'Connecticut'},
    {'slug': 'westchester-county/', 'label': 'Westchester County'},
    {'slug': 'hudson-valley/', 'label': 'Hudson Valley'},
    {'slug': 'nassau-county/', 'label': 'Nassau County'},
    {'slug': 'suffolk-county/', 'label': 'Suffolk County'},
]

# Service request signals for filtering threads
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend', 'recommendation',
    'can anyone suggest', 'who do you use', 'looking to hire',
    'need help', 'contractor', 'plumber', 'electrician', 'hvac',
    'landscap', 'cleaning', 'handyman', 'roofer', 'painter',
    'repair', 'estimate', 'quote', 'fix', 'install',
    'broken', 'leak', 'emergency', 'urgent',
    'good company', 'reliable', 'trustworthy', 'affordable',
]


class CityDataScraper(BaseScraper):
    MONITOR_NAME = 'citydata'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def scrape_forum_threads(scraper, forum_url):
    """
    Scrape a City-Data forum page for thread listings.
    Returns list of dicts with: title, url, author, date, replies.
    """
    resp = scraper.get(forum_url)
    if resp is None:
        return []

    if resp.status_code == 404:
        logger.debug(f"City-Data forum not found: {forum_url}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # City-Data uses vBulletin-style thread listings
    threads = soup.select(
        '#threads .threadbit, .threadbit, '
        'li[id^="thread_"], tr[id^="thread_"], '
        '.trow1, .trow2'
    )

    if not threads:
        # Broader fallback for thread links
        threads = soup.select('a[href*="showthread"], a[id^="thread_title"]')
        for link in threads:
            href = link.get('href', '')
            if not href.startswith('http'):
                href = urljoin(forum_url, href)
            title = link.get_text(strip=True)
            if title and len(title) > 10:
                results.append({
                    'title': title,
                    'url': href,
                    'author': '',
                    'date': '',
                    'replies': 0,
                })
        logger.info(f"Scraped {len(results)} threads (fallback) from {forum_url}")
        return results

    for thread in threads:
        try:
            # Thread title link
            link = thread.select_one(
                'a.title, a[id^="thread_title"], '
                'a[href*="showthread"], h3 a, h2 a'
            )
            if not link:
                continue

            href = link.get('href', '')
            if not href.startswith('http'):
                href = urljoin(forum_url, href)

            title = link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Author
            author_el = thread.select_one(
                '.author a, .username, .threadstarter a, '
                'a[href*="member.php"], .smallfont a'
            )
            author = author_el.get_text(strip=True) if author_el else ''

            # Date/time
            date_el = thread.select_one(
                '.threadlastpost .time, .lastpostdate, '
                '.threaddate, .date, .smallfont'
            )
            date_str = date_el.get_text(strip=True) if date_el else ''

            # Reply count
            replies = 0
            reply_el = thread.select_one(
                '.threadstats .replies a, .threadreplies, .views'
            )
            if reply_el:
                text = reply_el.get_text(strip=True)
                nums = re.findall(r'\d+', text.replace(',', ''))
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
            logger.debug(f"Error parsing City-Data thread: {e}")
            continue

    # Deduplicate by URL
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)

    logger.info(f"Scraped {len(unique)} threads from {forum_url}")
    return unique


def fetch_thread_detail(scraper, url):
    """Fetch the full text of a City-Data forum thread (first post)."""
    resp = scraper.get(url)
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    # First post body (vBulletin format)
    post_el = soup.select_one(
        '.postbody, .post_message, [id^="post_message_"], '
        '.postcontent, .post-body, .message'
    )
    body = post_el.get_text(separator='\n', strip=True) if post_el else ''

    # Title
    title_el = soup.select_one('h1, .threadtitle, #thread-title')
    title = title_el.get_text(strip=True) if title_el else ''

    # Author
    author_el = soup.select_one(
        '.username, .bigusername, .postauthor a, '
        'a[href*="member.php"]'
    )
    author = author_el.get_text(strip=True) if author_el else ''

    # Date
    date_el = soup.select_one('.postdate, .date, .postcontent .smallfont')
    date_str = date_el.get_text(strip=True) if date_el else ''

    posted_at = parse_vbulletin_date(date_str)

    return {
        'title': title,
        'body': body,
        'author': author,
        'posted_at': posted_at,
        'full_text': f"{title}\n\n{body}".strip(),
    }


def parse_vbulletin_date(date_str):
    """Parse vBulletin-style date strings."""
    if not date_str:
        return None

    # Clean up
    date_str = date_str.strip()
    date_str = re.sub(r'\s+', ' ', date_str)

    # Handle "Yesterday" and "Today"
    now = timezone.now()
    if 'today' in date_str.lower():
        return now
    if 'yesterday' in date_str.lower():
        return now - timedelta(days=1)

    # Try common formats
    formats = [
        '%m-%d-%Y, %I:%M %p',
        '%m-%d-%Y',
        '%d-%m-%Y, %I:%M %p',
        '%b %d, %Y',
        '%B %d, %Y',
        '%m/%d/%Y',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt)
        except ValueError:
            continue

    return None


def is_service_request(title):
    """Check if a thread title looks like a service request."""
    title_lower = title.lower()
    for signal in SERVICE_SIGNALS:
        if signal in title_lower:
            return True
    return False


def monitor_citydata(forums=None, max_per_forum=25, fetch_details=True,
                     max_age_hours=72, dry_run=False):
    """
    Main monitoring function. Scrapes City-Data forums for service request threads.

    Args:
        forums: List of forum dicts (default: DEFAULT_FORUMS)
        max_per_forum: Max threads to process per forum
        fetch_details: Whether to fetch full thread body
        max_age_hours: Skip posts older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = CityDataScraper()

    # Cooldown check
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"City-Data monitor skipped: {reason}")
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                'errors': 0, 'skipped_reason': reason}

    if forums is None:
        forums = DEFAULT_FORUMS

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    forums = scraper.shuffle(forums)

    try:
        for forum in forums:
            if scraper.is_stopped:
                break

            forum_url = urljoin(BASE_URL, forum['slug'])
            logger.info(f"Scanning City-Data: {forum['label']}")

            threads = scrape_forum_threads(scraper, forum_url)
            stats['scraped'] += len(threads)

            for thread in threads[:max_per_forum]:
                if scraper.is_stopped:
                    break

                try:
                    # Filter: only process threads that look like service requests
                    if not is_service_request(thread['title']):
                        continue

                    content = thread['title']
                    author = thread.get('author', '')
                    posted_at = parse_vbulletin_date(thread.get('date', ''))

                    # Fetch full thread if enabled
                    if fetch_details and thread.get('url'):
                        detail = fetch_thread_detail(scraper, thread['url'])
                        if detail:
                            content = detail['full_text'] or content
                            posted_at = detail.get('posted_at') or posted_at
                            author = detail.get('author') or author

                    # Skip old posts
                    if posted_at and posted_at < cutoff:
                        continue

                    # Add forum context for location detection
                    content += f"\n(City-Data Forum: {forum['label']})"

                    source_url = thread.get('url', forum_url)

                    if dry_run:
                        logger.info(f"[DRY RUN] Would create lead: {thread['title'][:80]}")
                        stats['created'] += 1
                        continue

                    lead, created, num_assigned = process_lead(
                        platform='citydata',
                        source_url=source_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'forum': forum['slug'],
                            'forum_label': forum['label'],
                            'replies': thread.get('replies', 0),
                            'listing_title': thread['title'],
                        },
                    )

                    if created:
                        stats['created'] += 1
                        stats['assigned'] += num_assigned
                    else:
                        stats['duplicates'] += 1

                except Exception as e:
                    logger.error(f"Error processing City-Data thread {thread.get('url', '?')}: {e}")
                    stats['errors'] += 1
                    continue

    except RateLimitHit as e:
        logger.warning(f"City-Data monitor stopped early (rate limit): {e}")

    logger.info(f"City-Data monitor complete: {stats}")
    return stats
