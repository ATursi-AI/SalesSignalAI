"""
Thumbtack monitor for SalesSignal AI.
Scrapes publicly visible project listings and service request pages.
Thumbtack leads are ultra-high-intent — people actively trying to hire.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, quote

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.thumbtack.com'

# Service categories to scan on Thumbtack
DEFAULT_SERVICES = [
    {'slug': 'plumbing', 'label': 'Plumbing'},
    {'slug': 'electricians', 'label': 'Electricians'},
    {'slug': 'hvac', 'label': 'HVAC'},
    {'slug': 'house-cleaning', 'label': 'House Cleaning'},
    {'slug': 'landscaping', 'label': 'Landscaping'},
    {'slug': 'lawn-care', 'label': 'Lawn Care'},
    {'slug': 'painting', 'label': 'Painting'},
    {'slug': 'roofing', 'label': 'Roofing'},
    {'slug': 'handyman', 'label': 'Handyman'},
    {'slug': 'pest-control', 'label': 'Pest Control'},
    {'slug': 'moving', 'label': 'Moving'},
    {'slug': 'junk-removal', 'label': 'Junk Removal'},
    {'slug': 'home-remodeling', 'label': 'Home Remodeling'},
    {'slug': 'flooring', 'label': 'Flooring'},
    {'slug': 'tree-service', 'label': 'Tree Service'},
    {'slug': 'garage-door-repair', 'label': 'Garage Door'},
    {'slug': 'appliance-repair', 'label': 'Appliance Repair'},
    {'slug': 'carpet-cleaning', 'label': 'Carpet Cleaning'},
    {'slug': 'window-cleaning', 'label': 'Window Cleaning'},
    {'slug': 'pressure-washing', 'label': 'Pressure Washing'},
]

# Locations to search (Thumbtack uses city/state or zip)
DEFAULT_LOCATIONS = [
    {'query': 'new-york-ny', 'label': 'New York, NY'},
    {'query': 'long-island-ny', 'label': 'Long Island, NY'},
    {'query': 'garden-city-ny', 'label': 'Garden City, NY'},
    {'query': 'mineola-ny', 'label': 'Mineola, NY'},
    {'query': 'huntington-ny', 'label': 'Huntington, NY'},
    {'query': 'white-plains-ny', 'label': 'White Plains, NY'},
    {'query': 'yonkers-ny', 'label': 'Yonkers, NY'},
    {'query': 'jersey-city-nj', 'label': 'Jersey City, NJ'},
    {'query': 'hoboken-nj', 'label': 'Hoboken, NJ'},
    {'query': 'newark-nj', 'label': 'Newark, NJ'},
    {'query': 'stamford-ct', 'label': 'Stamford, CT'},
    {'query': 'norwalk-ct', 'label': 'Norwalk, CT'},
]


class ThumbtackScraper(BaseScraper):
    MONITOR_NAME = 'thumbtack'
    DELAY_MIN = 4.0
    DELAY_MAX = 10.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 60


def build_thumbtack_url(service_slug, location_query):
    """Build a Thumbtack search URL."""
    return f"{BASE_URL}/k/{service_slug}/{location_query}/"


def scrape_thumbtack_listings(scraper, url):
    """
    Scrape Thumbtack service listings page for project requests.
    Returns list of dicts with: title, url, description, location, service_type.
    """
    resp = scraper.get(url)
    if not resp or resp.status_code == 404:
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []

    # Thumbtack project/request cards
    cards = soup.select(
        '[data-testid="project-card"], .project-card, '
        '.request-card, [class*="ProjectCard"], '
        '[class*="RequestCard"], .service-card'
    )

    if not cards:
        # Try finding structured data
        scripts = soup.select('script[type="application/ld+json"]')
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for item in data:
                        if item.get('@type') in ('Service', 'Offer', 'Product'):
                            results.append({
                                'title': item.get('name', ''),
                                'url': url,
                                'description': item.get('description', ''),
                                'location': '',
                                'service_type': item.get('category', ''),
                            })
                elif isinstance(data, dict) and data.get('@type') in ('Service', 'ItemList'):
                    for item in data.get('itemListElement', []):
                        results.append({
                            'title': item.get('name', ''),
                            'url': item.get('url', url),
                            'description': item.get('description', ''),
                            'location': '',
                            'service_type': '',
                        })
            except (ValueError, TypeError):
                continue

    for card in cards:
        try:
            # Title
            title_el = card.select_one(
                'h2, h3, [class*="title"], [data-testid="project-title"]'
            )
            title = title_el.get_text(strip=True) if title_el else ''

            # Link
            link = card.select_one('a')
            href = ''
            if link:
                href = link.get('href', '')
                if href and not href.startswith('http'):
                    href = urljoin(BASE_URL, href)

            # Description
            desc_el = card.select_one(
                'p, [class*="description"], [data-testid="project-description"]'
            )
            description = desc_el.get_text(strip=True) if desc_el else ''

            if not title and not description:
                continue

            # Location
            loc_el = card.select_one(
                '[class*="location"], [data-testid="location"], .city'
            )
            location = loc_el.get_text(strip=True) if loc_el else ''

            # Service type
            type_el = card.select_one(
                '[class*="category"], [class*="service-type"]'
            )
            service_type = type_el.get_text(strip=True) if type_el else ''

            results.append({
                'title': title or description[:100],
                'url': href or url,
                'description': description,
                'location': location,
                'service_type': service_type,
            })
        except Exception as e:
            logger.debug(f"Error parsing Thumbtack card: {e}")
            continue

    # Also look for "Recent project requests" section
    recent_section = soup.select_one('[class*="recent-projects"], [class*="RecentRequests"]')
    if recent_section:
        items = recent_section.select('li, .project-item, [class*="request"]')
        for item in items:
            text = item.get_text(strip=True)
            if text and len(text) > 20:
                results.append({
                    'title': text[:120],
                    'url': url,
                    'description': text,
                    'location': '',
                    'service_type': '',
                })

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        key = r['title'][:60]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    logger.info(f"Scraped {len(unique)} listings from {url}")
    return unique


def monitor_thumbtack(services=None, locations=None, max_per_combo=15,
                      max_age_hours=72, dry_run=False):
    """
    Main monitoring function. Scrapes Thumbtack for service project listings.

    Args:
        services: List of service dicts (default: DEFAULT_SERVICES)
        locations: List of location dicts (default: DEFAULT_LOCATIONS)
        max_per_combo: Max listings per service+location combination
        max_age_hours: Not used directly (Thumbtack doesn't always show dates)
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts
    """
    scraper = ThumbtackScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                'errors': 0, 'skipped_reason': reason}

    if services is None:
        services = DEFAULT_SERVICES
    if locations is None:
        locations = DEFAULT_LOCATIONS

    stats = {'scraped': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    # Build and shuffle service/location combos
    combos = [(svc, loc) for loc in locations for svc in services]
    combos = scraper.shuffle(combos)

    for service, location in combos:
        if scraper.is_stopped:
            break

        url = build_thumbtack_url(service['slug'], location['query'])
        logger.info(f"Scanning Thumbtack: {service['label']} in {location['label']}")

        try:
            listings = scrape_thumbtack_listings(scraper, url)
        except RateLimitHit:
            break
        stats['scraped'] += len(listings)

        for listing in listings[:max_per_combo]:
            if scraper.is_stopped:
                break

            try:
                content = listing['title']
                if listing.get('description') and listing['description'] != listing['title']:
                    content += f"\n\n{listing['description']}"

                # Add location context
                if listing.get('location'):
                    content += f"\nLocation: {listing['location']}"
                else:
                    content += f"\n({location['label']})"

                if listing.get('service_type'):
                    content += f"\nService: {listing['service_type']}"

                source_url = listing.get('url', url)

                if dry_run:
                    logger.info(f"[DRY RUN] Would create lead: {listing['title'][:80]}")
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='thumbtack',
                    source_url=source_url,
                    content=content,
                    author='',
                    posted_at=None,  # Thumbtack often doesn't expose post dates
                    raw_data={
                        'service': service['slug'],
                        'service_label': service['label'],
                        'location': location['query'],
                        'location_label': location['label'],
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
                logger.error(f"Error processing Thumbtack listing: {e}")
                stats['errors'] += 1
                continue

    logger.info(f"Thumbtack monitor complete: {stats}")
    return stats
