"""
Patch.com community board monitor for SalesSignal AI.
Scrapes "Neighbors" and "Classifieds" sections for service requests
in towns within the tri-state service area.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Patch.com towns to monitor (tri-state area)
# Format: (state-slug, town-slug, display-name)
DEFAULT_PATCH_TOWNS = [
    # Nassau County, Long Island
    ('new-york', 'gardencity', 'Garden City'),
    ('new-york', 'mineola', 'Mineola'),
    ('new-york', 'hicksville', 'Hicksville'),
    ('new-york', 'levittown', 'Levittown'),
    ('new-york', 'massapequa', 'Massapequa'),
    ('new-york', 'freeport', 'Freeport'),
    ('new-york', 'merrick-ny', 'Merrick'),
    ('new-york', 'bellmore', 'Bellmore'),
    ('new-york', 'oceanside-ny', 'Oceanside'),
    ('new-york', 'rockvillecentre', 'Rockville Centre'),
    ('new-york', 'valleystream', 'Valley Stream'),
    ('new-york', 'greatneck', 'Great Neck'),
    ('new-york', 'portWashington', 'Port Washington'),
    ('new-york', 'syosset', 'Syosset'),
    ('new-york', 'farmingdale', 'Farmingdale'),
    ('new-york', 'longbeach', 'Long Beach'),
    ('new-york', 'eastmeadow', 'East Meadow'),
    ('new-york', 'plainview', 'Plainview'),
    # Suffolk County, Long Island
    ('new-york', 'huntington', 'Huntington'),
    ('new-york', 'babylon', 'Babylon'),
    ('new-york', 'smithtown', 'Smithtown'),
    ('new-york', 'commack', 'Commack'),
    ('new-york', 'bayshore', 'Bay Shore'),
    ('new-york', 'patchogue', 'Patchogue'),
    ('new-york', 'portjefferson', 'Port Jefferson'),
    # Westchester
    ('new-york', 'whiteplains', 'White Plains'),
    ('new-york', 'yonkers', 'Yonkers'),
    ('new-york', 'newrochelle', 'New Rochelle'),
    ('new-york', 'scarsdale', 'Scarsdale'),
    ('new-york', 'tarrytown-sleepyhollow', 'Tarrytown'),
    ('new-york', 'mamaroneck', 'Mamaroneck'),
    # New Jersey
    ('new-jersey', 'hoboken', 'Hoboken'),
    ('new-jersey', 'jerseycity', 'Jersey City'),
    ('new-jersey', 'montclair', 'Montclair'),
    ('new-jersey', 'hackensack', 'Hackensack'),
    ('new-jersey', 'paramus', 'Paramus'),
    ('new-jersey', 'teaneck', 'Teaneck'),
    ('new-jersey', 'fortlee', 'Fort Lee'),
    ('new-jersey', 'morristown', 'Morristown'),
    # Connecticut
    ('connecticut', 'stamford', 'Stamford'),
    ('connecticut', 'norwalk', 'Norwalk'),
    ('connecticut', 'greenwich', 'Greenwich'),
    ('connecticut', 'danbury', 'Danbury'),
    ('connecticut', 'fairfield', 'Fairfield'),
    ('connecticut', 'westport', 'Westport'),
]

# Patch sections to scan
PATCH_SECTIONS = [
    '/neighbors',
    '/classifieds',
    '/around-town',
]


class PatchScraper(BaseScraper):
    MONITOR_NAME = 'patch'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def build_patch_url(state, town, section=''):
    """Build a Patch.com URL for a town and section."""
    base = f"https://patch.com/{state}/{town}"
    if section:
        return f"{base}{section}"
    return base


def scrape_patch_page(scraper, url):
    """
    Scrape a Patch.com section page for posts.
    Returns list of dicts with: title, url, snippet, date, author.
    """
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # Patch uses various card layouts — try multiple selectors
    articles = soup.select(
        'article, .styles_Card__*, .card-list-item, '
        '[data-testid="card"], .slot-content a, .story-card'
    )

    if not articles:
        # Fallback: find all links that look like posts
        articles = soup.select('a[href*="/p/"], a[href*="/c/"]')

    for article in articles:
        try:
            # Find the link
            if article.name == 'a':
                link = article
            else:
                link = article.select_one('a[href*="/p/"], a[href*="/c/"], a.styles_Link__*')
            if not link:
                continue

            href = link.get('href', '')
            if not href:
                continue
            if not href.startswith('http'):
                href = urljoin(url, href)

            # Title
            title_el = article.select_one('h2, h3, .styles_Title__*, .card-title')
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Snippet/description
            snippet_el = article.select_one('p, .styles_Description__*, .card-description')
            snippet = snippet_el.get_text(strip=True) if snippet_el else ''

            # Date
            date_el = article.select_one('time, .styles_Date__*, .card-date')
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            # Author
            author_el = article.select_one('.styles_Author__*, .card-author, [data-testid="author"]')
            author = author_el.get_text(strip=True) if author_el else ''

            results.append({
                'title': title,
                'url': href,
                'snippet': snippet,
                'date': date_str,
                'author': author,
            })
        except Exception as e:
            logger.debug(f"Error parsing Patch article: {e}")
            continue

    # Deduplicate by URL
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)

    logger.info(f"Scraped {len(unique)} posts from {url}")
    return unique


def fetch_patch_post_detail(scraper, url):
    """Fetch the full text of a Patch.com post."""
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Post body
    body_el = soup.select_one(
        'article .styles_Content__*, .post-body, '
        '[data-testid="post-content"], .story-content, article'
    )
    body = body_el.get_text(separator='\n', strip=True) if body_el else ''

    # Title
    title_el = soup.select_one('h1')
    title = title_el.get_text(strip=True) if title_el else ''

    # Posted time
    time_el = soup.select_one('time')
    posted_at = None
    if time_el and time_el.get('datetime'):
        try:
            posted_at = datetime.fromisoformat(time_el['datetime'].replace('Z', '+00:00'))
            if timezone.is_naive(posted_at):
                posted_at = timezone.make_aware(posted_at)
        except (ValueError, TypeError):
            pass

    # Author
    author_el = soup.select_one('.styles_Author__*, [data-testid="author"], .byline')
    author = author_el.get_text(strip=True) if author_el else ''

    return {
        'title': title,
        'body': body,
        'posted_at': posted_at,
        'author': author,
        'full_text': f"{title}\n\n{body}".strip(),
    }


def parse_date(date_str):
    """Parse a date string into a timezone-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        return None


def monitor_patch(towns=None, sections=None, max_per_section=20,
                  fetch_details=True, max_age_hours=48, dry_run=False):
    """
    Main monitoring function. Scrapes Patch.com towns and processes leads.

    Args:
        towns: List of (state, town, display_name) tuples (default: DEFAULT_PATCH_TOWNS)
        sections: List of section paths (default: PATCH_SECTIONS)
        max_per_section: Max posts to process per section per town
        fetch_details: Whether to fetch full post body
        max_age_hours: Skip posts older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = PatchScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                'errors': 0, 'skipped_reason': reason}

    if towns is None:
        towns = DEFAULT_PATCH_TOWNS
    if sections is None:
        sections = PATCH_SECTIONS

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize scraping order
    towns = scraper.shuffle(towns)

    for state, town_slug, display_name in towns:
        if scraper.is_stopped:
            break

        shuffled_sections = scraper.shuffle(sections)
        for section in shuffled_sections:
            if scraper.is_stopped:
                break

            url = build_patch_url(state, town_slug, section)
            logger.info(f"Scanning Patch: {display_name} {section}")

            try:
                posts = scrape_patch_page(scraper, url)
            except RateLimitHit:
                break
            stats['scraped'] += len(posts)

            for post in posts[:max_per_section]:
                if scraper.is_stopped:
                    break
                try:
                    content = post['title']
                    author = post.get('author', '')
                    posted_at = parse_date(post.get('date'))

                    if post.get('snippet'):
                        content += f"\n\n{post['snippet']}"

                    # Optionally fetch full post
                    if fetch_details and post.get('url'):
                        try:
                            detail = fetch_patch_post_detail(scraper, post['url'])
                        except RateLimitHit:
                            break
                        if detail:
                            content = detail['full_text'] or content
                            posted_at = detail.get('posted_at') or posted_at
                            author = detail.get('author') or author

                    # Skip old posts
                    if posted_at and posted_at < cutoff:
                        continue

                    # Add town context for location detection
                    content += f"\n({display_name})"

                    source_url = post.get('url', url)

                    if dry_run:
                        logger.info(f"[DRY RUN] Would create lead: {post['title'][:80]}")
                        stats['created'] += 1
                        continue

                    lead, created, num_assigned = process_lead(
                        platform='patch',
                        source_url=source_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'state': state,
                            'town': town_slug,
                            'town_display': display_name,
                            'section': section,
                            'listing_title': post['title'],
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
                    logger.error(f"Error processing Patch post {post.get('url', '?')}: {e}")
                    stats['errors'] += 1
                    continue

    logger.info(f"Patch monitor complete: {stats}")
    return stats
