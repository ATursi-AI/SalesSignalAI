"""
Better Business Bureau (BBB) complaint monitor for SalesSignal AI.

Scrapes BBB.org for competitor complaint data. When a competitor gets
a BBB complaint, that customer is looking for an alternative provider.
Same pattern as Yelp/Angi — negative feedback = opportunity signal.

Scrapable with BeautifulSoup — no Apify needed.
Uses BaseScraper for anti-detection. Works nationwide.
"""
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.business import BusinessProfile
from core.models.monitoring import MonitorRun
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# BBB complaint signals that indicate the reviewer needs a new provider
OPPORTUNITY_SIGNALS = [
    'terrible', 'horrible', 'worst', 'awful', 'never again',
    'do not use', "don't use", 'avoid', 'scam', 'rip off', 'ripoff',
    'unprofessional', 'unreliable', 'no show', 'no-show',
    'damaged', 'broke', 'ruined',
    'overcharged', 'overpriced',
    "didn't fix", "didn't work", 'still broken',
    "wouldn't recommend", 'would not recommend',
    'looking for alternative', 'need a new', 'switching',
    'never coming back', 'fired them',
    'unresponsive', 'ignored', 'ghosted',
    'poor quality', 'shoddy',
    'complaint', 'unanswered', 'unresolved',
]


class BBBScraper(BaseScraper):
    MONITOR_NAME = 'bbb'
    DELAY_MIN = 5.0
    DELAY_MAX = 12.0
    MAX_REQUESTS_PER_RUN = 20
    MAX_PER_DOMAIN = 10
    COOLDOWN_MINUTES = 720  # 12 hours
    RESPECT_ROBOTS = True


def _is_opportunity(text, complaint_type=''):
    """Check if BBB complaint indicates customer seeking alternatives."""
    text_lower = (text + ' ' + complaint_type).lower()
    return any(s in text_lower for s in OPPORTUNITY_SIGNALS)


def _parse_date(date_str):
    """Parse BBB date formats."""
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = [
        '%m/%d/%Y', '%Y-%m-%d', '%b %d, %Y', '%B %d, %Y',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _get_competitor_bbb_urls():
    """
    Build BBB profile URLs for competitors of active businesses.
    Returns list of BBB profile URL strings.
    """
    profiles = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    )

    urls = set()
    for bp in profiles:
        config = bp.raw_data if hasattr(bp, 'raw_data') and bp.raw_data else {}
        bbb_urls = config.get('bbb_competitors', [])
        for url in bbb_urls:
            if 'bbb.org' in url:
                urls.add(url)

    return list(urls)


def _scrape_bbb_profile(scraper, url):
    """
    Scrape a BBB business profile page for complaints and reviews.
    Returns list of complaint/review dicts.
    """
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return [], ''

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Extract business name
    name_el = soup.select_one('h1, .business-name, [class*="business-name"]')
    business_name = name_el.get_text(strip=True) if name_el else ''

    complaints = []

    # Try to find complaint/review elements
    # BBB pages have various structures — try common patterns
    review_containers = soup.select(
        '.complaint-item, .review-item, [class*="complaint"], [class*="review"], '
        '.customer-complaint, .customer-review'
    )

    for container in review_containers:
        text_el = container.select_one(
            '.complaint-text, .review-text, p, .text, [class*="text"], [class*="body"]'
        )
        date_el = container.select_one(
            '.date, time, [class*="date"], [datetime]'
        )
        type_el = container.select_one(
            '.complaint-type, .type, [class*="type"], [class*="category"]'
        )

        text = text_el.get_text(strip=True) if text_el else ''
        date_str = ''
        if date_el:
            date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)
        complaint_type = type_el.get_text(strip=True) if type_el else ''

        if text and len(text) > 20:
            complaints.append({
                'text': text[:2000],
                'date': date_str,
                'type': complaint_type,
                'business_name': business_name,
                'url': url,
            })

    # Also check for the complaint summary section
    summary = soup.select_one('.complaint-summary, [class*="complaint-summary"]')
    if summary and not complaints:
        # If no individual complaints found, extract summary info
        text = summary.get_text(strip=True)
        if text and len(text) > 20:
            complaints.append({
                'text': text[:2000],
                'date': '',
                'type': 'complaint_summary',
                'business_name': business_name,
                'url': url,
            })

    return complaints, business_name


def monitor_bbb(bbb_urls=None, max_age_days=30, dry_run=False):
    """
    Monitor BBB.org for competitor complaints and negative reviews.

    Scrapes BBB business profile pages for complaint data.
    Negative complaints = opportunity signals.

    Args:
        bbb_urls: list of BBB profile URLs to monitor
                 (default: from active business competitor configs)
        max_age_days: skip complaints older than this many days
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: items_scraped, created, duplicates,
                         assigned, errors
    """
    scraper = BBBScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'items_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0, 'skipped_reason': reason}

    if bbb_urls is None:
        bbb_urls = _get_competitor_bbb_urls()

    if not bbb_urls:
        logger.info('No BBB competitor URLs configured')
        return {'items_scraped': 0, 'created': 0, 'duplicates': 0,
                'assigned': 0, 'errors': 0}

    stats = {
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    cutoff = timezone.now() - timedelta(days=max_age_days)
    bbb_urls = scraper.shuffle(list(bbb_urls))

    for url in bbb_urls:
        if scraper.is_stopped:
            break

        logger.info(f'[bbb] Scraping: {url}')

        try:
            complaints, business_name = _scrape_bbb_profile(scraper, url)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[bbb] Error scraping {url}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(complaints)

        for complaint in complaints:
            try:
                text = complaint.get('text', '')
                complaint_date = _parse_date(complaint.get('date', ''))
                complaint_type = complaint.get('type', '')
                comp_name = complaint.get('business_name', '') or business_name

                if not text:
                    continue

                # Skip old complaints
                if complaint_date and complaint_date < cutoff:
                    continue

                # Filter for opportunity signals
                if not _is_opportunity(text, complaint_type):
                    continue

                # Build lead content
                content_parts = [
                    f'[BBB Complaint — {comp_name}]' if comp_name else '[BBB Complaint]',
                ]
                if complaint_type:
                    content_parts.append(f'Type: {complaint_type}')
                content_parts.append(text[:1500])

                content = '\n'.join(content_parts)

                if dry_run:
                    logger.info(f'[DRY RUN] Would create BBB lead: {comp_name} — {text[:60]}')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='bbb',
                    source_url=url,
                    content=content,
                    author='',
                    posted_at=complaint_date,
                    raw_data={
                        'business_name': comp_name,
                        'complaint_type': complaint_type,
                        'source': 'bbb_scrape',
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
                logger.error(f'[bbb] Error processing complaint: {e}')
                stats['errors'] += 1

    logger.info(f'BBB monitor complete: {stats}')
    return stats
