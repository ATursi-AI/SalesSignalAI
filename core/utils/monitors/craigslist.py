"""
Craigslist monitor for SalesSignal AI.
Scrapes "services wanted" and "gigs" sections from local Craigslist.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Craigslist subdomains for the tri-state area
DEFAULT_REGIONS = [
    'newyork',       # NYC metro
    'longisland',    # Long Island
    'newjersey',     # North Jersey
    'centraljersey', # Central NJ
    'southjersey',   # South Jersey
    'hudsonvalley',  # Hudson Valley NY
    'westchester',   # Westchester / Fairfield CT
    'hartford',      # Hartford CT area
    'newhaven',      # New Haven CT area
]

# Sections to scrape — "services wanted" = people looking for help
CRAIGSLIST_SECTIONS = {
    'services': '/d/services/search/bbb',     # services offered (competitors)
    'wanted': '/d/wanted/search/wan',          # wanted section
    'gigs': '/d/gigs/search/ggg',             # gig listings
    'household': '/d/household-services/search/hss',  # household services
}

# We focus on sections where people REQUEST services
LEAD_SECTIONS = ['wanted', 'gigs', 'household']


class CraigslistScraper(BaseScraper):
    MONITOR_NAME = 'craigslist'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30


def build_search_url(region, section_path, keywords=None):
    """Build a Craigslist search URL."""
    base = f"https://{region}.craigslist.org"
    url = urljoin(base, section_path)
    if keywords:
        url += f"?query={'+'.join(keywords.split())}"
    return url


def scrape_listing_page(scraper, url):
    """
    Scrape a Craigslist search results page.
    Returns list of dicts with: title, url, date, location, price.
    """
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    listings = soup.select('li.cl-static-search-result, li.result-row')
    if not listings:
        listings = soup.select('.cl-search-result')

    for item in listings:
        try:
            link = item.select_one('a.titlestring, a.posting-title, a.result-title, a')
            if not link:
                continue

            title = link.get_text(strip=True)
            href = link.get('href', '')
            if not href.startswith('http'):
                href = urljoin(url, href)

            date_el = item.select_one('time, .result-date, .meta')
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            location_el = item.select_one('.result-hood, .nearby, .supertitle')
            location = ''
            if location_el:
                location = location_el.get_text(strip=True).strip('()')

            price_el = item.select_one('.result-price, .priceinfo')
            price = price_el.get_text(strip=True) if price_el else ''

            results.append({
                'title': title,
                'url': href,
                'date': date_str,
                'location': location,
                'price': price,
            })
        except Exception as e:
            logger.debug(f"Error parsing listing: {e}")
            continue

    logger.info(f"Scraped {len(results)} listings from {url}")
    return results


def fetch_posting_detail(scraper, url):
    """Fetch the full text of a Craigslist posting."""
    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    body = soup.select_one('#postingbody, .body, section.body')
    if body:
        for el in body.select('.print-qrcode-label, .print-qrcode-container'):
            el.decompose()
        text = body.get_text(separator='\n', strip=True)
    else:
        text = ''

    title_el = soup.select_one('#titletextonly, .postingtitletext')
    title = title_el.get_text(strip=True) if title_el else ''

    loc_el = soup.select_one('.postingtitletext small, .mapaddress')
    location_detail = loc_el.get_text(strip=True).strip('()') if loc_el else ''

    time_el = soup.select_one('time.date, time.timeago')
    posted_at = None
    if time_el and time_el.get('datetime'):
        try:
            posted_at = datetime.fromisoformat(time_el['datetime'].replace('Z', '+00:00'))
            posted_at = timezone.make_aware(posted_at) if timezone.is_naive(posted_at) else posted_at
        except (ValueError, TypeError):
            pass

    return {
        'title': title,
        'body': text,
        'location': location_detail,
        'posted_at': posted_at,
        'full_text': f"{title}\n\n{text}".strip(),
    }


def parse_date(date_str):
    """Parse a Craigslist date string into a timezone-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        return None


def monitor_craigslist(regions=None, sections=None, max_per_section=25,
                       fetch_details=True, max_age_hours=48):
    """
    Main monitoring function. Scrapes Craigslist listings and processes them as leads.

    Returns:
        dict with counts: scraped, created, duplicates, assigned
    """
    scraper = CraigslistScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                'errors': 0, 'skipped_reason': reason}

    if regions is None:
        regions = DEFAULT_REGIONS
    if sections is None:
        sections = LEAD_SECTIONS

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize scraping order
    regions = scraper.shuffle(regions)

    for region in regions:
        if scraper.is_stopped:
            break

        shuffled_sections = scraper.shuffle(sections)
        for section_key in shuffled_sections:
            if scraper.is_stopped:
                break

            section_path = CRAIGSLIST_SECTIONS.get(section_key)
            if not section_path:
                continue

            url = build_search_url(region, section_path)
            logger.info(f"Scanning {region} / {section_key}: {url}")

            try:
                listings = scrape_listing_page(scraper, url)
            except RateLimitHit:
                break
            stats['scraped'] += len(listings)

            for listing in listings[:max_per_section]:
                if scraper.is_stopped:
                    break
                try:
                    content = listing['title']
                    author = ''
                    posted_at = parse_date(listing.get('date'))

                    if fetch_details and listing.get('url'):
                        try:
                            detail = fetch_posting_detail(scraper, listing['url'])
                        except RateLimitHit:
                            break
                        if detail:
                            content = detail['full_text'] or content
                            posted_at = detail.get('posted_at') or posted_at
                            if detail.get('location') and detail['location'] not in content:
                                content += f"\nLocation: {detail['location']}"

                    if posted_at and posted_at < cutoff:
                        continue

                    if listing.get('location') and listing['location'] not in content:
                        content += f"\n({listing['location']})"

                    source_url = listing.get('url', url)

                    lead, created, num_assigned = process_lead(
                        platform='craigslist',
                        source_url=source_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'region': region,
                            'section': section_key,
                            'price': listing.get('price', ''),
                            'listing_title': listing['title'],
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
                    logger.error(f"Error processing listing {listing.get('url', '?')}: {e}")
                    stats['errors'] += 1
                    continue

    logger.info(f"Craigslist monitor complete: {stats}")
    return stats
