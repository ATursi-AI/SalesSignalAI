"""
Houzz forum monitor for SalesSignal AI.
Scrapes Houzz discussion forums for service recommendation requests.
Focuses on "Find a Pro" and "Advice" categories.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.houzz.com'

# Houzz discussion categories to scan
HOUZZ_CATEGORIES = [
    # Advice / Find-a-Pro discussions
    {'slug': 'advice', 'url': '/discussions/advice', 'label': 'Advice'},
    {'slug': 'find-a-pro', 'url': '/discussions/find-a-pro', 'label': 'Find a Pro'},
    # Home improvement categories
    {'slug': 'kitchen', 'url': '/discussions/kitchen', 'label': 'Kitchen'},
    {'slug': 'bathroom', 'url': '/discussions/bathroom', 'label': 'Bathroom'},
    {'slug': 'home-remodel', 'url': '/discussions/home-remodeling', 'label': 'Home Remodeling'},
    {'slug': 'landscape', 'url': '/discussions/landscape', 'label': 'Landscape'},
    {'slug': 'plumbing', 'url': '/discussions/plumbing', 'label': 'Plumbing'},
    {'slug': 'electrical', 'url': '/discussions/electrical', 'label': 'Electrical'},
    {'slug': 'hvac', 'url': '/discussions/hvac', 'label': 'HVAC'},
    {'slug': 'roofing', 'url': '/discussions/roofing-siding', 'label': 'Roofing & Siding'},
    {'slug': 'flooring', 'url': '/discussions/flooring', 'label': 'Flooring'},
    {'slug': 'paint', 'url': '/discussions/painting', 'label': 'Painting'},
]

# Signals that a post is a service request
SERVICE_SIGNALS = [
    'looking for', 'need a', 'recommend', 'recommendation', 'anyone know',
    'can anyone suggest', 'who do you use', 'looking to hire', 'need help',
    'contractor', 'estimate', 'quote', 'how much', 'cost to',
    'find a pro', 'good plumber', 'good electrician', 'good contractor',
    'need work done', 'looking for someone', 'hire someone',
]


class HouzzScraper(BaseScraper):
    MONITOR_NAME = 'houzz'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def scrape_houzz_discussions(scraper, category_url):
    """
    Scrape a Houzz discussion category page for threads.
    Returns list of dicts with: title, url, snippet, author, date.
    """
    url = urljoin(BASE_URL, category_url)

    resp = scraper.get(url)
    if not resp or resp.status_code == 404:
        logger.debug(f"Houzz category not found or blocked: {url}")
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # Houzz discussion threads
    threads = soup.select(
        '.hz-discussion-card, .discussion-item, '
        '[data-testid="discussion-card"], .hz-view-discussions-list__item, '
        '.discussion-list-item, article'
    )

    if not threads:
        # Fallback: look for links to discussion pages
        threads = soup.select('a[href*="/discussions/"], a[href*="/ideabooks/"]')

    for thread in threads:
        try:
            if thread.name == 'a':
                link = thread
            else:
                link = thread.select_one('a[href*="/discussions/"], a')
            if not link:
                continue

            href = link.get('href', '')
            if not href:
                continue
            if not href.startswith('http'):
                href = urljoin(BASE_URL, href)

            # Title
            title_el = thread.select_one('h2, h3, .hz-discussion-card__title, .title')
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Snippet
            snippet_el = thread.select_one('p, .hz-discussion-card__body, .snippet, .description')
            snippet = snippet_el.get_text(strip=True) if snippet_el else ''

            # Author
            author_el = thread.select_one('.hz-discussion-card__author, .author, .username')
            author = author_el.get_text(strip=True) if author_el else ''

            # Date
            date_el = thread.select_one('time, .hz-discussion-card__date, .date, .timestamp')
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            results.append({
                'title': title,
                'url': href,
                'snippet': snippet,
                'author': author,
                'date': date_str,
            })
        except Exception as e:
            logger.debug(f"Error parsing Houzz thread: {e}")
            continue

    # Deduplicate by URL
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)

    logger.info(f"Scraped {len(unique)} threads from {url}")
    return unique


def fetch_houzz_thread_detail(scraper, url):
    """Fetch the full text of a Houzz discussion thread."""
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Original post body
    body_el = soup.select_one(
        '.hz-discussion-body, .discussion-body, '
        '[data-testid="discussion-body"], .post-body, article .body'
    )
    body = body_el.get_text(separator='\n', strip=True) if body_el else ''

    # Title
    title_el = soup.select_one('h1')
    title = title_el.get_text(strip=True) if title_el else ''

    # Author
    author_el = soup.select_one('.hz-discussion-author, .author, .username')
    author = author_el.get_text(strip=True) if author_el else ''

    # Date
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


def is_service_request(title, snippet=''):
    """Check if a thread appears to be a service request."""
    combined = f"{title} {snippet}".lower()
    for signal in SERVICE_SIGNALS:
        if signal in combined:
            return True
    return False


def parse_date(date_str):
    """Parse a date string into a timezone-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        return None


def monitor_houzz(categories=None, max_per_category=20,
                  fetch_details=True, max_age_hours=72, dry_run=False):
    """
    Main monitoring function. Scrapes Houzz discussion forums for service requests.

    Args:
        categories: List of category dicts (default: HOUZZ_CATEGORIES)
        max_per_category: Max threads to process per category
        fetch_details: Whether to fetch full thread body
        max_age_hours: Skip posts older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = HouzzScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                'errors': 0, 'skipped_reason': reason}

    if categories is None:
        categories = HOUZZ_CATEGORIES

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize scraping order
    categories = scraper.shuffle(categories)

    for category in categories:
        if scraper.is_stopped:
            break

        logger.info(f"Scanning Houzz: {category['label']}")

        try:
            threads = scrape_houzz_discussions(scraper, category['url'])
        except RateLimitHit:
            break
        stats['scraped'] += len(threads)

        for thread in threads[:max_per_category]:
            if scraper.is_stopped:
                break
            try:
                # Filter: only process threads that look like service requests
                if not is_service_request(thread['title'], thread.get('snippet', '')):
                    continue

                content = thread['title']
                author = thread.get('author', '')
                posted_at = parse_date(thread.get('date'))

                if thread.get('snippet'):
                    content += f"\n\n{thread['snippet']}"

                # Optionally fetch full thread
                if fetch_details and thread.get('url'):
                    try:
                        detail = fetch_houzz_thread_detail(scraper, thread['url'])
                    except RateLimitHit:
                        break
                    if detail:
                        content = detail['full_text'] or content
                        posted_at = detail.get('posted_at') or posted_at
                        author = detail.get('author') or author

                # Skip old posts
                if posted_at and posted_at < cutoff:
                    continue

                source_url = thread.get('url', '')

                if dry_run:
                    logger.info(f"[DRY RUN] Would create lead: {thread['title'][:80]}")
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='houzz',
                    source_url=source_url,
                    content=content,
                    author=author,
                    posted_at=posted_at,
                    raw_data={
                        'category': category['slug'],
                        'category_label': category['label'],
                        'listing_title': thread['title'],
                    },
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except RateLimitHit:
                break
            except Exception as e:
                logger.error(f"Error processing Houzz thread {thread.get('url', '?')}: {e}")
                stats['errors'] += 1
                continue

    logger.info(f"Houzz monitor complete: {stats}")
    return stats
