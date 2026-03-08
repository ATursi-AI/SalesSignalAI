"""
Trade Forum monitor for SalesSignal AI.
Scrapes homeowner posts on ContractorTalk.com, PlumbingZone.com,
HVAC-Talk.com, and similar trade forums.
Filters for posts from homeowners (not pros) asking for service help
with location info matching NY/NJ/CT service area.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Trade forums where homeowners post asking for help
DEFAULT_FORUMS = [
    {
        'name': 'ContractorTalk',
        'base_url': 'https://www.contractortalk.com',
        'sections': [
            {'slug': '/f6/', 'label': 'General Discussion'},
            {'slug': '/f18/', 'label': 'Residential'},
            {'slug': '/f58/', 'label': 'Remodeling'},
            {'slug': '/f31/', 'label': 'Painting'},
            {'slug': '/f30/', 'label': 'Plumbing'},
            {'slug': '/f28/', 'label': 'Electrical'},
            {'slug': '/f60/', 'label': 'HVAC'},
            {'slug': '/f32/', 'label': 'Roofing'},
        ],
        'selectors': {
            'thread_list': 'li.threadbit, .thread, tr[id^="thread_"], .discussionListItem',
            'thread_link': 'a.title, a[href*="threads/"], h3 a, .PreviewTooltip',
            'thread_title': 'a.title, .PreviewTooltip, h3 a, .listBlock a',
            'thread_date': '.DateTime, time, .date, .lastpostdate',
            'thread_author': '.username, .author a, a[href*="members/"]',
            'post_body': '.postcontent, .messageText, .post-body, .content, article p',
        },
    },
    {
        'name': 'PlumbingZone',
        'base_url': 'https://www.plumbingzone.com',
        'sections': [
            {'slug': '/f2/', 'label': 'General Plumbing Discussion'},
            {'slug': '/f26/', 'label': 'Residential Plumbing'},
            {'slug': '/f3/', 'label': 'Plumbing Problems & Help'},
        ],
        'selectors': {
            'thread_list': 'li.threadbit, .thread, tr[id^="thread_"]',
            'thread_link': 'a.title, a[href*="threads/"], h3 a',
            'thread_title': 'a.title, h3 a',
            'thread_date': '.DateTime, time, .date',
            'thread_author': '.username, .author a',
            'post_body': '.postcontent, .messageText, .post-body',
        },
    },
    {
        'name': 'HVAC-Talk',
        'base_url': 'https://hvac-talk.com',
        'sections': [
            {'slug': '/vbb/forumdisplay.php?f=14', 'label': 'Residential HVAC'},
            {'slug': '/vbb/forumdisplay.php?f=3', 'label': 'General Discussion'},
        ],
        'selectors': {
            'thread_list': 'tr[id^="thread_"], .threadbit, li.threadbit',
            'thread_link': 'a[id^="thread_title_"], a[href*="showthread"], a.title',
            'thread_title': 'a[id^="thread_title_"], a.title',
            'thread_date': '.date, .DateTime, time',
            'thread_author': '.username, a[href*="member"]',
            'post_body': '.postcontent, .post_message, .vb_postbit .content',
        },
    },
    {
        'name': 'DIY Chatroom',
        'base_url': 'https://www.diychatroom.com',
        'sections': [
            {'slug': '/f2/', 'label': 'General DIY Discussions'},
            {'slug': '/f4/', 'label': 'Plumbing'},
            {'slug': '/f5/', 'label': 'Electrical'},
            {'slug': '/f6/', 'label': 'HVAC'},
            {'slug': '/f35/', 'label': 'Painting'},
            {'slug': '/f20/', 'label': 'Roofing/Siding'},
        ],
        'selectors': {
            'thread_list': 'li.threadbit, .thread, tr[id^="thread_"], .discussionListItem',
            'thread_link': 'a.title, a[href*="threads/"], h3 a, .PreviewTooltip',
            'thread_title': 'a.title, .PreviewTooltip, h3 a',
            'thread_date': '.DateTime, time, .date',
            'thread_author': '.username, .author a',
            'post_body': '.postcontent, .messageText, .post-body',
        },
    },
    {
        'name': 'GardenWeb / Houzz Forums',
        'base_url': 'https://www.houzz.com',
        'sections': [
            {'slug': '/discussions/kitchen', 'label': 'Kitchen Forums'},
            {'slug': '/discussions/bathroom', 'label': 'Bathroom Forums'},
            {'slug': '/discussions/home-remodeling', 'label': 'Remodeling'},
        ],
        'selectors': {
            'thread_list': '.hz-discussion-list__item, .discussion-item, article',
            'thread_link': 'a[href*="/discussions/"], a.discussion-title',
            'thread_title': '.hz-discussion-list__title, h3 a, .discussion-title',
            'thread_date': 'time, .date, .timestamp',
            'thread_author': '.author, .username, .hz-discussion-list__author',
            'post_body': '.hz-discussion-body, .discussion-body, .post-body, article p',
        },
    },
]

# Signals that a post is from a homeowner (not a pro)
HOMEOWNER_SIGNALS = [
    'homeowner', 'home owner', 'my house', 'my home',
    'first time', 'newbie', 'not a pro', 'not a contractor',
    'diy', 'do it myself', 'should i hire',
    'how much should', 'what would it cost', 'ballpark',
    'is this normal', 'does this look right',
    'my contractor', 'hired a', 'getting quotes',
    'need help', 'help please', 'advice needed',
    'just bought', 'new house', 'new home',
    'looking for a', 'need a good', 'anyone know a good',
    'recommend', 'recommendation', 'referral',
]

# Service request signals
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend',
    'plumber', 'electrician', 'hvac', 'contractor', 'handyman',
    'roofer', 'painter', 'flooring', 'drywall',
    'repair', 'fix', 'replace', 'install',
    'leak', 'broken', 'damaged', 'cracked',
    'estimate', 'quote', 'bid', 'cost',
    'emergency', 'urgent', 'asap',
    'who do you use', 'reliable', 'affordable',
    'kitchen remodel', 'bathroom remodel', 'basement',
    'gutter', 'siding', 'fence', 'deck',
    'water heater', 'furnace', 'ac unit', 'boiler',
    'pest control', 'mold', 'water damage',
    'inspection', 'code violation',
]

# Location signals for NY/NJ/CT filtering
LOCATION_SIGNALS = [
    'new york', 'ny', 'nyc', 'manhattan', 'brooklyn', 'queens', 'bronx',
    'long island', 'nassau', 'suffolk', 'westchester', 'yonkers',
    'new jersey', 'nj', 'jersey city', 'hoboken', 'newark',
    'connecticut', 'ct', 'stamford', 'bridgeport', 'new haven',
    'tri-state', 'tristate', 'metro area',
    'staten island', 'white plains', 'new rochelle',
    'paramus', 'hackensack', 'morristown', 'princeton',
    'greenwich', 'darien', 'norwalk', 'danbury',
]


class TradeForumScraper(BaseScraper):
    MONITOR_NAME = 'trade_forums'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def is_homeowner_post(title, body=''):
    """Check if a post appears to be from a homeowner (not a trade professional)."""
    combined = f"{title} {body}".lower()
    return any(signal in combined for signal in HOMEOWNER_SIGNALS)


def has_service_signal(title, body=''):
    """Check if a post contains service request signals."""
    combined = f"{title} {body}".lower()
    return any(signal in combined for signal in SERVICE_SIGNALS)


def has_location_signal(title, body=''):
    """Check if a post mentions a NY/NJ/CT location."""
    combined = f"{title} {body}".lower()
    return any(loc in combined for loc in LOCATION_SIGNALS)


def scrape_forum_section(scraper, forum, section):
    """
    Scrape a trade forum section for thread listings.
    Returns list of dicts: {title, url, author, date}.
    """
    url = urljoin(forum['base_url'], section['slug'])
    selectors = forum.get('selectors', {})

    resp = scraper.get(url)
    if not resp or resp.status_code == 404:
        logger.debug(f"Forum section not found or blocked: {url}")
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    thread_sel = selectors.get('thread_list', '.thread, li.threadbit')
    threads = soup.select(thread_sel)

    if not threads:
        # Fallback: find topic links
        link_sel = selectors.get('thread_link', 'a[href]')
        links = soup.select(link_sel)
        for link in links:
            href = link.get('href', '')
            title = link.get_text(strip=True)
            if href and title and len(title) > 10:
                if not href.startswith('http'):
                    href = urljoin(url, href)
                results.append({
                    'title': title,
                    'url': href,
                    'author': '',
                    'date': '',
                })
        return _dedupe(results)

    for thread in threads:
        try:
            link_sel = selectors.get('thread_link', 'a[href]')
            link = thread.select_one(link_sel)
            if not link:
                link = thread.select_one('a[href]')
            if not link:
                continue

            href = link.get('href', '')
            if not href:
                continue
            if not href.startswith('http'):
                href = urljoin(url, href)

            # Title
            title_sel = selectors.get('thread_title', 'a')
            title_el = thread.select_one(title_sel)
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Author
            author_sel = selectors.get('thread_author', '.username, .author')
            author_el = thread.select_one(author_sel)
            author = author_el.get_text(strip=True) if author_el else ''

            # Date
            date_sel = selectors.get('thread_date', 'time, .date')
            date_el = thread.select_one(date_sel)
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            results.append({
                'title': title,
                'url': href,
                'author': author,
                'date': date_str,
            })
        except Exception as e:
            logger.debug(f"Error parsing thread from {forum['name']}: {e}")
            continue

    return _dedupe(results)


def fetch_thread_detail(scraper, url, selectors):
    """Fetch the full text of a trade forum thread (first post)."""
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    body_sel = selectors.get('post_body', '.postcontent, .post-body, article p')
    body_el = soup.select_one(body_sel)
    body = body_el.get_text(separator='\n', strip=True) if body_el else ''

    title_el = soup.select_one('h1, h2')
    title = title_el.get_text(strip=True) if title_el else ''

    author_el = soup.select_one('.username, .author a, a[href*="members/"]')
    author = author_el.get_text(strip=True) if author_el else ''

    time_el = soup.select_one('time, .date')
    posted_at = None
    if time_el:
        dt_str = time_el.get('datetime', '')
        if dt_str:
            try:
                posted_at = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
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


def _dedupe(results):
    """Deduplicate results by URL."""
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)
    return unique


def parse_date(date_str):
    """Parse a date string into timezone-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        return None


def monitor_trade_forums(forums=None, max_per_section=20, fetch_details=True,
                          max_age_hours=72, dry_run=False):
    """
    Main monitoring function. Scrapes trade forums for homeowner service requests.

    Args:
        forums: List of forum dicts (default: DEFAULT_FORUMS)
        max_per_section: Max threads to process per forum section
        fetch_details: Whether to fetch full thread body
        max_age_hours: Skip posts older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = TradeForumScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                'errors': 0, 'skipped_reason': reason}

    if forums is None:
        forums = DEFAULT_FORUMS

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize scraping order
    forums = scraper.shuffle(forums)

    for forum in forums:
        if scraper.is_stopped:
            break

        logger.info(f"Scanning trade forum: {forum['name']}")

        shuffled_sections = scraper.shuffle(forum['sections'])
        for section in shuffled_sections:
            if scraper.is_stopped:
                break

            logger.info(f"  Section: {section['label']}")

            try:
                threads = scrape_forum_section(scraper, forum, section)
            except RateLimitHit:
                break
            stats['scraped'] += len(threads)

            for thread in threads[:max_per_section]:
                if scraper.is_stopped:
                    break
                try:
                    # Quick title-level filter: must look like a service request
                    if not has_service_signal(thread['title']):
                        continue

                    content = thread['title']
                    author = thread.get('author', '')
                    posted_at = parse_date(thread.get('date'))
                    body = ''

                    if fetch_details and thread.get('url'):
                        try:
                            detail = fetch_thread_detail(
                                scraper,
                                thread['url'],
                                forum.get('selectors', {}),
                            )
                        except RateLimitHit:
                            break
                        if detail:
                            content = detail['full_text'] or content
                            body = detail.get('body', '')
                            posted_at = detail.get('posted_at') or posted_at
                            author = detail.get('author') or author

                    # Must be from a homeowner (not a pro)
                    if not is_homeowner_post(thread['title'], body):
                        continue

                    # Prefer posts with NY/NJ/CT location signals
                    if body and not has_location_signal(thread['title'], body):
                        continue

                    if posted_at and posted_at < cutoff:
                        continue

                    content += f"\n(Trade Forum: {forum['name']} — {section['label']})"
                    source_url = thread.get('url', urljoin(forum['base_url'], section['slug']))

                    if dry_run:
                        logger.info(f"[DRY RUN] Would create lead: {thread['title'][:80]}")
                        stats['created'] += 1
                        continue

                    lead, created, num_assigned = process_lead(
                        platform='trade_forum',
                        source_url=source_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'forum': forum['name'],
                            'section': section['label'],
                            'listing_title': thread['title'],
                            'is_homeowner': True,
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
                    logger.error(f"Error processing thread from {forum['name']}: {e}")
                    stats['errors'] += 1
                    continue

    logger.info(f"Trade forum monitor complete: {stats}")
    return stats
