"""
Local News monitor for SalesSignal AI.
Flexible scraper that reads MonitoredLocalSite configs from the database.
Supports WordPress comment scraping, Discourse forums, and custom HTML
patterns via configurable CSS selectors.
Scrapes community sections and comment threads for service recommendation requests.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import MonitoredLocalSite
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Service request signals in local news / community comment threads
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend', 'recommendation',
    'who do you use', 'referral', 'reliable', 'affordable',
    'plumber', 'electrician', 'hvac', 'contractor', 'handyman',
    'landscap', 'cleaning', 'roofer', 'painter',
    'flooring', 'drywall', 'pest control', 'exterminator',
    'mold', 'water damage', 'repair', 'renovation',
    'kitchen remodel', 'bathroom remodel', 'basement',
    'gutter', 'siding', 'fence', 'deck', 'patio',
    'tree service', 'tree removal', 'snow removal',
    'moving company', 'movers', 'junk removal',
    'locksmith', 'garage door', 'window replacement',
    'looking to hire', 'need help finding',
    'any suggestions', 'can anyone recommend',
]


class LocalNewsScraper(BaseScraper):
    """Scraper configuration for local news monitors."""
    MONITOR_NAME = 'local_news'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def _sel(selectors, key, default=''):
    """Safely get a CSS selector from the selectors dict."""
    return selectors.get(key, default)


def is_service_request(text):
    """Check if text contains service request signals."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in SERVICE_SIGNALS)


def scrape_article_list(scraper, site):
    """
    Scrape the community section for article links.
    Returns list of dicts: {title, url, date, author}.
    """
    url = site.community_section_url or site.base_url
    selectors = site.css_selectors or {}

    resp = scraper.get(url)
    if resp is None:
        logger.error(f"Failed to fetch {site.name} ({url}): request blocked or skipped")
        return []

    try:
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch {site.name} ({url}): {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # Find article containers
    article_sel = _sel(selectors, 'article_list', 'article, .post')
    articles = soup.select(article_sel)

    if not articles:
        # Fallback: try to find links directly
        link_sel = _sel(selectors, 'article_link', 'a[href]')
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
        return results

    for article in articles:
        try:
            # Find article link
            link_sel = _sel(selectors, 'article_link', 'a[href]')
            link = article.select_one(link_sel)
            if not link:
                # Try any link in the article
                link = article.select_one('a[href]')
            if not link:
                continue

            href = link.get('href', '')
            if not href:
                continue
            if not href.startswith('http'):
                href = urljoin(url, href)

            # Title
            title_sel = _sel(selectors, 'article_title', 'h2, h3')
            title_el = article.select_one(title_sel)
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Date
            date_sel = _sel(selectors, 'article_date', 'time')
            date_el = article.select_one(date_sel)
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            # Author
            author_sel = _sel(selectors, 'article_author', '.author, .byline')
            author_el = article.select_one(author_sel)
            author = author_el.get_text(strip=True) if author_el else ''

            results.append({
                'title': title,
                'url': href,
                'date': date_str,
                'author': author,
            })
        except Exception as e:
            logger.debug(f"Error parsing article from {site.name}: {e}")
            continue

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)

    logger.info(f"Found {len(unique)} articles on {site.name}")
    return unique


def scrape_article_comments(scraper, site, article_url):
    """
    Scrape comments from an individual article page.
    Returns list of comment text strings.
    """
    selectors = site.css_selectors or {}

    resp = scraper.get(article_url)
    if resp is None:
        logger.error(f"Failed to fetch article {article_url}: request blocked or skipped")
        return [], ''

    try:
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch article {article_url}: {e}")
        return [], ''

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Get article body for context
    body_sel = _sel(selectors, 'article_body', 'article p, .entry-content')
    body_els = soup.select(body_sel)
    body_text = ' '.join(el.get_text(strip=True) for el in body_els[:5])

    # Get comments
    comment_sel = _sel(selectors, 'comment_list', '.comment')
    comments = soup.select(comment_sel)

    comment_texts = []
    body_text_sel = _sel(selectors, 'comment_body', '.comment-body, p')

    for comment in comments:
        try:
            body_el = comment.select_one(body_text_sel)
            if body_el:
                text = body_el.get_text(strip=True)
                if text and len(text) > 15:
                    comment_texts.append(text)
        except Exception:
            continue

    return comment_texts, body_text


def scrape_wordpress_comments(scraper, site, article_url):
    """WordPress-specific comment scraper using WP comment structure."""
    resp = scraper.get(article_url)
    if resp is None:
        logger.error(f"Failed to fetch WP article {article_url}: request blocked or skipped")
        return [], ''

    try:
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch WP article {article_url}: {e}")
        return [], ''

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = site.css_selectors or {}

    # Article body
    body_sel = _sel(selectors, 'article_body', '.entry-content, .post-content')
    body_el = soup.select_one(body_sel)
    body_text = body_el.get_text(strip=True)[:500] if body_el else ''

    # WordPress comments
    comment_sel = _sel(selectors, 'comment_list', '.comment, #comments li')
    comments = soup.select(comment_sel)

    comment_texts = []
    body_text_sel = _sel(selectors, 'comment_body', '.comment-content, .comment-body')

    for comment in comments:
        try:
            body = comment.select_one(body_text_sel)
            if body:
                text = body.get_text(strip=True)
                if text and len(text) > 15:
                    comment_texts.append(text)
        except Exception:
            continue

    return comment_texts, body_text


def parse_date(date_str):
    """Parse a date string into timezone-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        return None


def monitor_local_news(site_ids=None, max_per_site=15, fetch_comments=True,
                       max_age_hours=72, dry_run=False):
    """
    Main monitoring function. Scrapes MonitoredLocalSite entries for service requests.

    Args:
        site_ids: List of site IDs to monitor (default: all active)
        max_per_site: Max articles to process per site
        fetch_comments: Whether to fetch article comments
        max_age_hours: Skip articles older than this
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: sites_checked, articles_scraped, created, duplicates, assigned
    """
    scraper = LocalNewsScraper()

    # Cooldown check
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"Skipping local_news monitor: {reason}")
        return {
            'sites_checked': 0, 'articles_scraped': 0,
            'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
            'skipped_reason': reason,
        }

    sites = MonitoredLocalSite.objects.filter(is_active=True)
    if site_ids:
        sites = sites.filter(id__in=site_ids)

    stats = {
        'sites_checked': 0,
        'articles_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize site order
    sites = scraper.shuffle(sites)

    for site in sites:
        if scraper.is_stopped:
            break

        stats['sites_checked'] += 1
        logger.info(f"Scanning local news: {site.name}")

        try:
            articles = scrape_article_list(scraper, site)
        except RateLimitHit:
            logger.warning(f"Rate limited while fetching article list from {site.name}")
            break

        stats['articles_scraped'] += len(articles)

        for article in articles[:max_per_site]:
            if scraper.is_stopped:
                break

            try:
                title = article['title']
                article_url = article['url']
                posted_at = parse_date(article.get('date'))

                # Check title for service signals
                title_relevant = is_service_request(title)

                comment_texts = []
                body_text = ''

                if fetch_comments and article_url:
                    if site.scrape_pattern == 'wordpress_comments':
                        comment_texts, body_text = scrape_wordpress_comments(
                            scraper, site, article_url
                        )
                    else:
                        comment_texts, body_text = scrape_article_comments(
                            scraper, site, article_url
                        )

                # Check for service requests in comments
                relevant_comments = [c for c in comment_texts if is_service_request(c)]

                # Skip if neither title nor comments have service signals
                if not title_relevant and not relevant_comments:
                    continue

                # Build content: article title + relevant comments
                content_parts = [f"[{site.name}] {title}"]
                if body_text:
                    content_parts.append(body_text[:300])
                for comment in relevant_comments[:5]:
                    content_parts.append(f"Comment: {comment[:200]}")

                content = '\n\n'.join(content_parts)

                if posted_at and posted_at < cutoff:
                    continue

                if dry_run:
                    logger.info(f"[DRY RUN] Would create lead: {title[:80]}")
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='local_news',
                    source_url=article_url,
                    content=content,
                    author=article.get('author', ''),
                    posted_at=posted_at,
                    raw_data={
                        'site_name': site.name,
                        'site_id': site.id,
                        'article_title': title,
                        'num_comments': len(comment_texts),
                        'relevant_comments': len(relevant_comments),
                    },
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except RateLimitHit:
                logger.warning(f"Rate limited while processing article from {site.name}")
                break
            except Exception as e:
                logger.error(f"Error processing article from {site.name}: {e}")
                stats['errors'] += 1
                continue

        # Update last_scraped timestamp
        site.last_scraped = timezone.now()
        site.save(update_fields=['last_scraped'])

    logger.info(f"Local news monitor complete: {stats}")
    return stats
