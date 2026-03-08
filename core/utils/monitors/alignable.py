"""
Alignable forum monitor for SalesSignal AI.
Scrapes Alignable local community forums for B2B service recommendation requests.
Focuses on property managers, facility managers, and business owners seeking services.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.alignable.com'

# Alignable community forums to monitor (tri-state area)
DEFAULT_COMMUNITIES = [
    # Long Island
    {'slug': 'garden-city-ny', 'label': 'Garden City, NY'},
    {'slug': 'mineola-ny', 'label': 'Mineola, NY'},
    {'slug': 'hicksville-ny', 'label': 'Hicksville, NY'},
    {'slug': 'levittown-ny', 'label': 'Levittown, NY'},
    {'slug': 'massapequa-ny', 'label': 'Massapequa, NY'},
    {'slug': 'freeport-ny', 'label': 'Freeport, NY'},
    {'slug': 'huntington-ny', 'label': 'Huntington, NY'},
    {'slug': 'babylon-ny', 'label': 'Babylon, NY'},
    {'slug': 'smithtown-ny', 'label': 'Smithtown, NY'},
    # NYC
    {'slug': 'new-york-ny', 'label': 'New York, NY'},
    {'slug': 'brooklyn-ny', 'label': 'Brooklyn, NY'},
    {'slug': 'queens-ny', 'label': 'Queens, NY'},
    # Westchester
    {'slug': 'white-plains-ny', 'label': 'White Plains, NY'},
    {'slug': 'yonkers-ny', 'label': 'Yonkers, NY'},
    {'slug': 'new-rochelle-ny', 'label': 'New Rochelle, NY'},
    # New Jersey
    {'slug': 'hoboken-nj', 'label': 'Hoboken, NJ'},
    {'slug': 'jersey-city-nj', 'label': 'Jersey City, NJ'},
    {'slug': 'montclair-nj', 'label': 'Montclair, NJ'},
    {'slug': 'hackensack-nj', 'label': 'Hackensack, NJ'},
    {'slug': 'morristown-nj', 'label': 'Morristown, NJ'},
    # Connecticut
    {'slug': 'stamford-ct', 'label': 'Stamford, CT'},
    {'slug': 'norwalk-ct', 'label': 'Norwalk, CT'},
    {'slug': 'greenwich-ct', 'label': 'Greenwich, CT'},
]

# B2B service request signals
B2B_SIGNALS = [
    'looking for', 'need a', 'recommend', 'recommendation', 'anyone know',
    'looking to hire', 'need help with', 'can anyone recommend',
    'property manager', 'property management', 'landlord', 'tenant',
    'building maintenance', 'office cleaning', 'commercial cleaning',
    'janitorial', 'facility', 'contractor', 'vendor',
    'quote', 'estimate', 'bid', 'service provider',
    'who do you use', 'referral', 'refer',
    'plumber', 'electrician', 'hvac', 'landscap', 'cleaning',
    'repair', 'maintenance', 'renovation', 'remodel',
]


class AlignableScraper(BaseScraper):
    MONITOR_NAME = 'alignable'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def scrape_alignable_community(scraper, community_slug):
    """
    Scrape an Alignable community forum page for posts.
    Returns list of dicts with: title, url, snippet, author, date.
    """
    url = f"{BASE_URL}/community/{community_slug}/feed"

    resp = scraper.get(url)
    if resp is None:
        return []

    if resp.status_code == 404:
        logger.debug(f"Alignable community not found: {url}")
        return []

    try:
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch Alignable {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # Alignable feed posts
    posts = soup.select(
        '.feed-item, .post-card, .community-post, '
        '[data-testid="feed-item"], article, .card'
    )

    if not posts:
        # Try finding any content blocks with links
        posts = soup.select('.content-block, .feed-content, .post')

    for post in posts:
        try:
            # Find the link
            link = post.select_one('a[href*="/post/"], a[href*="/question"], a')
            if not link:
                continue

            href = link.get('href', '')
            if not href:
                continue
            if not href.startswith('http'):
                href = urljoin(BASE_URL, href)

            # Title or post text
            title_el = post.select_one('h2, h3, h4, .post-title, .question-title')
            title = title_el.get_text(strip=True) if title_el else ''

            # Body/snippet
            body_el = post.select_one('p, .post-body, .post-content, .description')
            snippet = body_el.get_text(strip=True) if body_el else ''

            # Use the longer text as content
            if not title and snippet:
                title = snippet[:120]
            if not title:
                title = link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Author
            author_el = post.select_one('.author-name, .username, .user-name, .poster')
            author = author_el.get_text(strip=True) if author_el else ''

            # Date
            date_el = post.select_one('time, .post-date, .timestamp, .date')
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
            logger.debug(f"Error parsing Alignable post: {e}")
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


def fetch_alignable_post_detail(scraper, url):
    """Fetch the full text of an Alignable post."""
    resp = scraper.get(url)
    if resp is None:
        return None

    try:
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch Alignable detail {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    body_el = soup.select_one(
        '.post-body, .post-content, .question-body, '
        '[data-testid="post-body"], article .content'
    )
    body = body_el.get_text(separator='\n', strip=True) if body_el else ''

    title_el = soup.select_one('h1, .post-title, .question-title')
    title = title_el.get_text(strip=True) if title_el else ''

    author_el = soup.select_one('.author-name, .user-name, .poster')
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


def is_b2b_service_request(title, snippet=''):
    """Check if a post is a B2B service request."""
    combined = f"{title} {snippet}".lower()
    for signal in B2B_SIGNALS:
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


def monitor_alignable(communities=None, max_per_community=20,
                      fetch_details=True, max_age_hours=72, dry_run=False):
    """
    Main monitoring function. Scrapes Alignable community forums for B2B service requests.

    Args:
        communities: List of community dicts (default: DEFAULT_COMMUNITIES)
        max_per_community: Max posts to process per community
        fetch_details: Whether to fetch full post body
        max_age_hours: Skip posts older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = AlignableScraper()

    # Check cooldown before starting
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"Alignable monitor skipped: {reason}")
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    if communities is None:
        communities = DEFAULT_COMMUNITIES

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize scraping order
    communities = scraper.shuffle(communities)

    try:
        for community in communities:
            if scraper.is_stopped:
                break

            logger.info(f"Scanning Alignable: {community['label']}")

            posts = scrape_alignable_community(scraper, community['slug'])
            stats['scraped'] += len(posts)

            for post in posts[:max_per_community]:
                if scraper.is_stopped:
                    break

                try:
                    # Filter: only process posts that look like B2B service requests
                    if not is_b2b_service_request(post['title'], post.get('snippet', '')):
                        continue

                    content = post['title']
                    author = post.get('author', '')
                    posted_at = parse_date(post.get('date'))

                    if post.get('snippet'):
                        content += f"\n\n{post['snippet']}"

                    # Optionally fetch full post
                    if fetch_details and post.get('url'):
                        detail = fetch_alignable_post_detail(scraper, post['url'])
                        if detail:
                            content = detail['full_text'] or content
                            posted_at = detail.get('posted_at') or posted_at
                            author = detail.get('author') or author

                    # Skip old posts
                    if posted_at and posted_at < cutoff:
                        continue

                    # Add community context for location detection
                    content += f"\n(Alignable: {community['label']})"

                    source_url = post.get('url', '')

                    if dry_run:
                        logger.info(f"[DRY RUN] Would create lead: {post['title'][:80]}")
                        stats['created'] += 1
                        continue

                    lead, created, num_assigned = process_lead(
                        platform='alignable',
                        source_url=source_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'community': community['slug'],
                            'community_label': community['label'],
                            'listing_title': post['title'],
                        },
                    )

                    if created:
                        stats['created'] += 1
                        stats['assigned'] += num_assigned
                    else:
                        stats['duplicates'] += 1

                except Exception as e:
                    logger.error(f"Error processing Alignable post {post.get('url', '?')}: {e}")
                    stats['errors'] += 1
                    continue

    except RateLimitHit as e:
        logger.warning(f"Alignable monitor stopped due to rate limiting: {e}")

    logger.info(f"Alignable monitor complete: {stats}")
    return stats
