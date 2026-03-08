"""
Parent Community monitor for SalesSignal AI.
Scrapes parent network recommendation boards (Park Slope Parents, UrbanBaby,
and similar NY-area parent communities) for service recommendation requests.
Parents frequently ask for: contractors, plumbers, painters, movers, cleaners, etc.
High-intent leads — people actively looking for service providers.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Parent community sites in NY/NJ/CT area
DEFAULT_COMMUNITIES = [
    {
        'name': 'Park Slope Parents',
        'base_url': 'https://www.parkslopeparents.com',
        'search_url': 'https://www.parkslopeparents.com/component/joomlaboard/',
        'selectors': {
            'thread_list': '.topic-row, .joomlaboard-row, tr.sectiontableentry1, tr.sectiontableentry2, .forum-topic',
            'thread_link': 'a[href*="topic"], a[href*="board"], a.topic-title',
            'thread_title': '.topic-title, td a, h3 a',
            'thread_date': '.topic-date, time, .date, td.date',
            'post_body': '.post-body, .joomlaboard-body, .message-body, .entry-content, article p',
        },
        'area': 'Brooklyn, NY',
    },
    {
        'name': 'DC Urban Moms (NY section)',
        'base_url': 'https://www.dcurbanmom.com',
        'search_url': 'https://www.dcurbanmom.com/jforum/forums/show/34.page',
        'selectors': {
            'thread_list': 'tr.highlight1, tr.highlight2, .topic-row, tr[class*="highlight"]',
            'thread_link': 'a[href*="posts"], a.topictitle',
            'thread_title': 'a.topictitle, td a[href*="posts"]',
            'thread_date': 'td:last-child, .postDate, time',
            'post_body': '.postbody, .post-entry, .post-body, .text',
        },
        'area': 'NYC Metro',
    },
    {
        'name': 'Maplewood Online (NJ)',
        'base_url': 'https://www.maplewoodonline.com',
        'search_url': 'https://www.maplewoodonline.com/forum/',
        'selectors': {
            'thread_list': '.topic, .thread, li.threadbit, .discussionListItem',
            'thread_link': 'a.PreviewTooltip, a[href*="threads/"], h3 a',
            'thread_title': 'a.PreviewTooltip, h3 a, .title a',
            'thread_date': '.DateTime, time, .date, .timestamp',
            'post_body': '.messageText, .post-body, article p, .bbCodeBlock',
        },
        'area': 'Maplewood, NJ',
    },
    {
        'name': 'Montclair NJ Forum',
        'base_url': 'https://www.montclairnj.org',
        'search_url': 'https://www.montclairnj.org/forum/',
        'selectors': {
            'thread_list': '.topic, .thread, li.threadbit, .discussionListItem',
            'thread_link': 'a[href*="threads/"], a[href*="topic/"], h3 a',
            'thread_title': 'h3 a, .title a, a[href*="threads/"]',
            'thread_date': '.DateTime, time, .date',
            'post_body': '.messageText, .post-body, article p',
        },
        'area': 'Montclair, NJ',
    },
    {
        'name': 'Hoboken Moms',
        'base_url': 'https://www.hobokenmoms.com',
        'search_url': 'https://www.hobokenmoms.com/forum/',
        'selectors': {
            'thread_list': '.topic, .thread, article, .post-item',
            'thread_link': 'a[href*="topic/"], a[href*="threads/"], h2 a, h3 a',
            'thread_title': 'h2 a, h3 a, .topic-title a',
            'thread_date': 'time, .date, .timestamp',
            'post_body': '.post-body, .entry-content, article p, .message-content',
        },
        'area': 'Hoboken, NJ',
    },
    {
        'name': 'Westchester Family Forum',
        'base_url': 'https://westchesterfamily.com',
        'search_url': 'https://westchesterfamily.com/forum/',
        'selectors': {
            'thread_list': '.topic, .thread, .bbp-body .bbp-topic-title',
            'thread_link': 'a[href*="topic/"], a[href*="threads/"], .bbp-topic-permalink',
            'thread_title': '.bbp-topic-permalink, h3 a, .topic-title',
            'thread_date': '.bbp-topic-freshness, time, .date',
            'post_body': '.bbp-topic-content, .entry-content, .post-body',
        },
        'area': 'Westchester, NY',
    },
    {
        'name': 'Fairfield County Moms',
        'base_url': 'https://fairfieldcountymoms.com',
        'search_url': 'https://fairfieldcountymoms.com/community/',
        'selectors': {
            'thread_list': '.topic, article, .post, .thread-item',
            'thread_link': 'a[href*="topic/"], a[href*="community/"], h2 a, h3 a',
            'thread_title': 'h2 a, h3 a, .topic-title',
            'thread_date': 'time, .date, .timestamp',
            'post_body': '.entry-content, .post-body, article p',
        },
        'area': 'Fairfield County, CT',
    },
]

# Service recommendation request signals specific to parent communities
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend', 'recommendation',
    'who do you use', 'referral', 'reliable', 'affordable', 'trusted',
    'plumber', 'electrician', 'hvac', 'contractor', 'handyman',
    'landscap', 'cleaning', 'cleaner', 'house cleaner',
    'roofer', 'painter', 'flooring', 'drywall',
    'pest control', 'exterminator', 'mold', 'water damage',
    'kitchen remodel', 'bathroom remodel', 'basement finishing',
    'gutter', 'siding', 'fence', 'deck', 'patio',
    'tree service', 'tree removal', 'snow removal', 'lawn care',
    'moving company', 'movers', 'junk removal',
    'locksmith', 'garage door', 'window replacement',
    'chimney', 'driveway', 'paving', 'masonry',
    'interior design', 'organizer', 'home stag',
    'any suggestions for a', 'can anyone recommend',
    'does anyone have a good', 'looking to hire',
    'need help finding', 'please recommend',
]


class ParentCommunityScraper(BaseScraper):
    MONITOR_NAME = 'parent_communities'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def is_service_request(text):
    """Check if text contains parent community service request signals."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in SERVICE_SIGNALS)


def scrape_community_threads(scraper, community):
    """
    Scrape a parent community forum for thread listings.
    Returns list of dicts: {title, url, date, author}.
    """
    url = community['search_url']
    selectors = community.get('selectors', {})

    resp = scraper.get(url)
    if not resp:
        return []
    if resp.status_code == 404:
        logger.debug(f"Community not found: {url}")
        return []
    if resp.status_code != 200:
        logger.error(f"Failed to fetch {community['name']} ({url}): HTTP {resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # Find thread containers
    thread_sel = selectors.get('thread_list', '.topic, .thread, article')
    threads = soup.select(thread_sel)

    if not threads:
        # Fallback: find links
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
                    'date': '',
                    'author': '',
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

            # Date
            date_sel = selectors.get('thread_date', 'time')
            date_el = thread.select_one(date_sel)
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            results.append({
                'title': title,
                'url': href,
                'date': date_str,
                'author': '',
            })
        except Exception as e:
            logger.debug(f"Error parsing thread from {community['name']}: {e}")
            continue

    return _dedupe(results)


def fetch_thread_detail(scraper, url, selectors):
    """Fetch the full text of a parent community thread."""
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    body_sel = selectors.get('post_body', '.post-body, .entry-content, article p')
    body_els = soup.select(body_sel)
    body = '\n'.join(el.get_text(strip=True) for el in body_els[:3])

    title_el = soup.select_one('h1, h2')
    title = title_el.get_text(strip=True) if title_el else ''

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


def monitor_parent_communities(communities=None, max_per_community=20,
                                fetch_details=True, max_age_hours=72,
                                dry_run=False):
    """
    Main monitoring function. Scrapes parent community forums for service requests.

    Args:
        communities: List of community dicts (default: DEFAULT_COMMUNITIES)
        max_per_community: Max threads to process per community
        fetch_details: Whether to fetch full thread body
        max_age_hours: Skip posts older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = ParentCommunityScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                'errors': 0, 'skipped_reason': reason}

    if communities is None:
        communities = DEFAULT_COMMUNITIES

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize scraping order
    communities = scraper.shuffle(communities)

    for community in communities:
        if scraper.is_stopped:
            break

        logger.info(f"Scanning parent community: {community['name']}")

        try:
            threads = scrape_community_threads(scraper, community)
        except RateLimitHit:
            break
        stats['scraped'] += len(threads)

        for thread in threads[:max_per_community]:
            if scraper.is_stopped:
                break
            try:
                if not is_service_request(thread['title']):
                    continue

                content = thread['title']
                posted_at = parse_date(thread.get('date'))

                if fetch_details and thread.get('url'):
                    try:
                        detail = fetch_thread_detail(
                            scraper,
                            thread['url'],
                            community.get('selectors', {}),
                        )
                    except RateLimitHit:
                        break
                    if detail:
                        content = detail['full_text'] or content
                        posted_at = detail.get('posted_at') or posted_at

                        # Re-check with full body
                        if not is_service_request(detail.get('body', '')):
                            if not is_service_request(thread['title']):
                                continue

                if posted_at and posted_at < cutoff:
                    continue

                content += f"\n(Parent Community: {community['name']} — {community.get('area', '')})"
                source_url = thread.get('url', community['search_url'])

                if dry_run:
                    logger.info(f"[DRY RUN] Would create lead: {thread['title'][:80]}")
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='parent_community',
                    source_url=source_url,
                    content=content,
                    author=thread.get('author', ''),
                    posted_at=posted_at,
                    raw_data={
                        'community': community['name'],
                        'area': community.get('area', ''),
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
                logger.error(f"Error processing thread from {community['name']}: {e}")
                stats['errors'] += 1
                continue

    logger.info(f"Parent community monitor complete: {stats}")
    return stats
