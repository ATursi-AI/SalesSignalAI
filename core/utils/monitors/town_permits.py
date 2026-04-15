"""
Long Island town building permit monitor for SalesSignal AI.

Scrapes building department websites for 7 Long Island towns:
  - Hempstead, Oyster Bay, Babylon, Islip, Huntington, Smithtown, Brookhaven

Each town has a different website structure. The scraper attempts to
fetch the building department page, find permit tables/listings, and
extract permit details (type, address, applicant, filing date, description).

Permit types are automatically mapped to service categories:
  - bathroom/kitchen -> Plumber, Electrician, Tile, Painting, GC
  - roof -> Roofing
  - pool -> Pool, Fencing, Landscaping, Electrical
  - deck -> Deck/Patio, Landscaping
  - electrical -> Electrician
  - plumbing -> Plumber
  - demolition -> GC, Hauling
  - new construction -> ALL trades
"""
import json
import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Town configuration
# -------------------------------------------------------------------

TOWN_CONFIG = {
    'hempstead': {
        'name': 'Town of Hempstead',
        'county': 'Nassau',
        'url': 'https://hempsteadny.gov/building-department',
        'permits_url': 'https://hempsteadny.gov/building-department/permits',
        'scrape_fn': '_scrape_hempstead',
    },
    'oyster_bay': {
        'name': 'Town of Oyster Bay',
        'county': 'Nassau',
        'url': 'https://oysterbaytown.com/departments/planning-development/building-division/',
        'permits_url': 'https://oysterbaytown.com/departments/planning-development/building-division/permits/',
        'scrape_fn': '_scrape_oyster_bay',
    },
    'babylon': {
        'name': 'Town of Babylon',
        'county': 'Suffolk',
        'url': 'https://www.townofbabylon.com/149/Building-Division',
        'permits_url': 'https://www.townofbabylon.com/149/Building-Division',
        'scrape_fn': '_scrape_babylon',
    },
    'islip': {
        'name': 'Town of Islip',
        'county': 'Suffolk',
        'url': 'https://islipny.gov/departments/planning-development/building-division',
        'permits_url': 'https://islipny.gov/departments/planning-development/building-division',
        'scrape_fn': '_scrape_islip',
    },
    'huntington': {
        'name': 'Town of Huntington',
        'county': 'Suffolk',
        'url': 'https://www.huntingtonny.gov/building',
        'permits_url': 'https://www.huntingtonny.gov/building',
        'scrape_fn': '_scrape_huntington',
    },
    'smithtown': {
        'name': 'Town of Smithtown',
        'county': 'Suffolk',
        'url': 'https://www.smithtownny.gov/180/Building-Department',
        'permits_url': 'https://www.smithtownny.gov/180/Building-Department',
        'scrape_fn': '_scrape_smithtown',
    },
    'brookhaven': {
        'name': 'Town of Brookhaven',
        'county': 'Suffolk',
        'url': 'https://www.brookhavenny.gov/167/Building-Division',
        'permits_url': 'https://www.brookhavenny.gov/167/Building-Division',
        'scrape_fn': '_scrape_brookhaven',
    },
}

# Permit type keywords -> service categories
PERMIT_SERVICE_MAP = {
    'bathroom': ['Plumber', 'Electrician', 'Tile', 'Painting', 'General Contractor'],
    'kitchen': ['Plumber', 'Electrician', 'Tile', 'Painting', 'General Contractor',
                'Countertop', 'Cabinet'],
    'roof': ['Roofing'],
    'roofing': ['Roofing'],
    'pool': ['Pool', 'Fencing', 'Landscaping', 'Electrician'],
    'swimming': ['Pool', 'Fencing', 'Landscaping', 'Electrician'],
    'deck': ['Deck/Patio', 'Landscaping', 'General Contractor'],
    'patio': ['Deck/Patio', 'Landscaping'],
    'electrical': ['Electrician'],
    'electric': ['Electrician'],
    'plumbing': ['Plumber'],
    'plumb': ['Plumber'],
    'demolition': ['General Contractor', 'Hauling', 'Junk Removal'],
    'demo': ['General Contractor', 'Hauling'],
    'new construction': ['General Contractor', 'Plumber', 'Electrician', 'Roofing',
                         'HVAC', 'Painter', 'Flooring', 'Landscaping', 'Fencing',
                         'Concrete', 'Drywall', 'Insulation'],
    'addition': ['General Contractor', 'Plumber', 'Electrician', 'Roofing'],
    'renovation': ['General Contractor', 'Plumber', 'Electrician', 'Painter',
                   'Flooring', 'Drywall'],
    'remodel': ['General Contractor', 'Plumber', 'Electrician', 'Painter',
                'Flooring', 'Drywall'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'air conditioning': ['HVAC'],
    'a/c': ['HVAC'],
    'fence': ['Fencing'],
    'siding': ['Siding', 'General Contractor'],
    'window': ['Window Installation', 'General Contractor'],
    'solar': ['Solar', 'Electrician'],
    'fire': ['Fire Protection', 'General Contractor', 'Electrician'],
    'foundation': ['General Contractor', 'Concrete'],
    'driveway': ['Paving', 'Concrete'],
    'garage': ['General Contractor', 'Electrician'],
    'basement': ['General Contractor', 'Plumber', 'Electrician', 'Waterproofing'],
    'water heater': ['Plumber'],
    'boiler': ['Plumber', 'HVAC'],
}

DEFAULT_SERVICES = ['General Contractor']


class TownPermitScraper(BaseScraper):
    MONITOR_NAME = 'town_permit'
    DELAY_MIN = 4.0
    DELAY_MAX = 10.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _detect_services_from_permit(permit_type, description=''):
    """Map permit type and description to relevant service categories."""
    if not permit_type and not description:
        return DEFAULT_SERVICES

    combined = f'{permit_type} {description}'.lower()
    services = set()

    for key, service_list in PERMIT_SERVICE_MAP.items():
        if key in combined:
            services.update(service_list)

    return list(services) if services else DEFAULT_SERVICES


def _parse_date(date_str):
    """Parse common date formats from town permit pages."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y',
        '%m-%d-%Y', '%b %d, %Y', '%B %d, %Y',
        '%Y-%m-%dT%H:%M:%S', '%d-%b-%Y',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        pass

    return None


def _generic_table_scrape(scraper, url, town_name):
    """
    Generic building permit table scraper. Fetches the page and looks for
    HTML tables containing permit data. Works as a fallback for any town.

    Returns list of permit dicts with keys:
        permit_type, address, applicant, filing_date, description
    """
    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[town_permit] Failed to fetch {town_name}: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[town_permit] {town_name} returned '
            f'{resp.status_code if resp else "no response"}'
        )
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    permits = []

    # Strategy 1: Look for tables with permit-related headers
    tables = soup.find_all('table')
    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        if not headers:
            # Try first row as header
            first_row = table.find('tr')
            if first_row:
                headers = [td.get_text(strip=True).lower() for td in first_row.find_all(['td', 'th'])]

        # Check if this looks like a permit table
        permit_keywords = ['permit', 'address', 'type', 'date', 'applicant', 'description']
        if not any(any(kw in h for kw in permit_keywords) for h in headers):
            continue

        # Map header positions
        col_map = {}
        for i, h in enumerate(headers):
            if 'type' in h or 'permit' in h:
                col_map.setdefault('permit_type', i)
            elif 'address' in h or 'location' in h:
                col_map.setdefault('address', i)
            elif 'applicant' in h or 'owner' in h or 'name' in h:
                col_map.setdefault('applicant', i)
            elif 'date' in h or 'filed' in h or 'issued' in h:
                col_map.setdefault('filing_date', i)
            elif 'description' in h or 'work' in h or 'scope' in h:
                col_map.setdefault('description', i)

        rows = table.find_all('tr')[1:]  # skip header
        for row in rows:
            cells = row.find_all('td')
            if not cells:
                continue

            permit = {
                'permit_type': _safe_cell(cells, col_map.get('permit_type')),
                'address': _safe_cell(cells, col_map.get('address')),
                'applicant': _safe_cell(cells, col_map.get('applicant')),
                'filing_date': _safe_cell(cells, col_map.get('filing_date')),
                'description': _safe_cell(cells, col_map.get('description')),
            }
            if permit['address'] or permit['permit_type']:
                permits.append(permit)

    # Strategy 2: Look for list-based permit displays
    if not permits:
        # Some towns use definition lists or divs
        permit_sections = soup.find_all(
            ['div', 'section', 'article'],
            class_=re.compile(r'permit|filing|application', re.I)
        )
        for section in permit_sections:
            text = section.get_text(strip=True)
            if len(text) > 20:
                permits.append({
                    'permit_type': '',
                    'address': text[:200],
                    'applicant': '',
                    'filing_date': '',
                    'description': text[:500],
                })

    return permits


def _safe_cell(cells, idx):
    """Safely extract text from a table cell by index."""
    if idx is None or idx < 0 or idx >= len(cells):
        return ''
    return cells[idx].get_text(strip=True)


# -------------------------------------------------------------------
# Town-specific scrapers (all fall back to generic table parsing)
# -------------------------------------------------------------------

def _scrape_hempstead(scraper, config):
    """Scrape Town of Hempstead building department permits."""
    return _generic_table_scrape(
        scraper, config['permits_url'], config['name']
    )


def _scrape_oyster_bay(scraper, config):
    """Scrape Town of Oyster Bay building division permits."""
    return _generic_table_scrape(
        scraper, config['permits_url'], config['name']
    )


def _scrape_babylon(scraper, config):
    """Scrape Town of Babylon building division permits."""
    return _generic_table_scrape(
        scraper, config['permits_url'], config['name']
    )


def _scrape_islip(scraper, config):
    """Scrape Town of Islip building division permits."""
    return _generic_table_scrape(
        scraper, config['permits_url'], config['name']
    )


def _scrape_huntington(scraper, config):
    """Scrape Town of Huntington building department permits."""
    return _generic_table_scrape(
        scraper, config['permits_url'], config['name']
    )


def _scrape_smithtown(scraper, config):
    """Scrape Town of Smithtown building department permits."""
    return _generic_table_scrape(
        scraper, config['permits_url'], config['name']
    )


def _scrape_brookhaven(scraper, config):
    """Scrape Town of Brookhaven building division permits."""
    return _generic_table_scrape(
        scraper, config['permits_url'], config['name']
    )


# Dispatch map for town-specific scrapers
SCRAPE_FUNCTIONS = {
    '_scrape_hempstead': _scrape_hempstead,
    '_scrape_oyster_bay': _scrape_oyster_bay,
    '_scrape_babylon': _scrape_babylon,
    '_scrape_islip': _scrape_islip,
    '_scrape_huntington': _scrape_huntington,
    '_scrape_smithtown': _scrape_smithtown,
    '_scrape_brookhaven': _scrape_brookhaven,
}


def _post_lead_remote(ingest_url, api_key, lead_data):
    """POST a lead to a remote SalesSignal instance via the ingest API."""
    import requests as req
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = req.post(
            ingest_url,
            data=json.dumps(lead_data),
            headers=headers,
            timeout=15,
        )
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = {'raw': resp.text[:200]}
        return resp.status_code in (201, 409), resp.status_code, body
    except req.RequestException as e:
        logger.error(f'[Remote] POST failed: {e}')
        return False, 0, {'error': str(e)}


# -------------------------------------------------------------------
# Main monitor function
# -------------------------------------------------------------------

def monitor_town_permits(town=None, all_towns=False, days=7,
                         dry_run=False, remote=False):
    """
    Monitor Long Island town building department websites for new permits.

    Scrapes each configured town's building department page, parses
    permit tables, and creates leads with service category mapping.

    Args:
        town: specific town key (e.g. 'hempstead'). Ignored if all_towns=True.
        all_towns: if True, scrape all 7 configured towns.
        days: how many days back to consider permits (default: 7)
        dry_run: if True, log matches without creating Lead records
        remote: if True, POST leads to REMOTE_INGEST_URL

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = TownPermitScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {
            'sources_checked': 0, 'items_scraped': 0, 'created': 0,
            'duplicates': 0, 'assigned': 0, 'errors': 0,
            'skipped_reason': reason,
        }

    # Resolve remote config
    ingest_url = ''
    ingest_key = ''
    if remote:
        ingest_url = getattr(settings, 'REMOTE_INGEST_URL', '')
        ingest_key = getattr(settings, 'INGEST_API_KEY', '')
        if not ingest_url or not ingest_key:
            logger.error(
                '[Remote] REMOTE_INGEST_URL and INGEST_API_KEY must be set '
                'in .env for --remote mode'
            )
            return {
                'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 1,
            }

    # Determine which towns to scrape
    if all_towns:
        towns_to_scrape = list(TOWN_CONFIG.keys())
    elif town:
        town_key = town.lower().replace(' ', '_')
        if town_key not in TOWN_CONFIG:
            logger.error(
                f'[town_permit] Unknown town: {town}. '
                f'Valid: {", ".join(TOWN_CONFIG.keys())}'
            )
            return {
                'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 1,
            }
        towns_to_scrape = [town_key]
    else:
        logger.error(
            '[town_permit] Must specify town= or all_towns=True'
        )
        return {
            'sources_checked': 0, 'items_scraped': 0, 'created': 0,
            'duplicates': 0, 'assigned': 0, 'errors': 1,
        }

    stats = {
        'sources_checked': 0,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    cutoff = timezone.now() - timedelta(days=days)
    towns_to_scrape = scraper.shuffle(towns_to_scrape)

    for town_key in towns_to_scrape:
        if scraper.is_stopped:
            break

        config = TOWN_CONFIG[town_key]
        stats['sources_checked'] += 1
        logger.info(
            f'[town_permit] Scraping {config["name"]} ({config["county"]} County)'
        )

        # Dispatch to town-specific scraper
        scrape_fn_name = config.get('scrape_fn', '')
        scrape_fn = SCRAPE_FUNCTIONS.get(scrape_fn_name)

        try:
            if scrape_fn:
                permits = scrape_fn(scraper, config)
            else:
                permits = _generic_table_scrape(
                    scraper, config['permits_url'], config['name']
                )
        except RateLimitHit:
            logger.warning(
                f'[town_permit] Rate limited on {config["name"]}, stopping'
            )
            break
        except Exception as e:
            logger.error(
                f'[town_permit] Error scraping {config["name"]}: {e}'
            )
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(permits)
        logger.info(
            f'[town_permit] Found {len(permits)} permits for {config["name"]}'
        )

        for permit in permits:
            try:
                address = permit.get('address', '')
                permit_type = permit.get('permit_type', '')
                description = permit.get('description', '')
                applicant = permit.get('applicant', '')
                filing_date = _parse_date(permit.get('filing_date', ''))

                if not address and not permit_type:
                    continue

                # Skip old permits
                if filing_date and filing_date < cutoff:
                    continue

                # Map permit type to services
                services = _detect_services_from_permit(permit_type, description)

                # Build lead content
                content_parts = [
                    f'Building Permit: {permit_type}' if permit_type else 'Building Permit Filed',
                    f'Address: {address}' if address else '',
                    f'Town: {config["name"]}, {config["county"]} County, NY',
                ]
                if applicant:
                    content_parts.append(f'Applicant: {applicant}')
                if filing_date:
                    content_parts.append(
                        f'Filed: {filing_date.strftime("%m/%d/%Y")}'
                    )
                if description:
                    content_parts.append(f'Description: {description[:300]}')
                content_parts.append(
                    f'Services likely needed: {", ".join(services[:8])}'
                )

                content = '\n'.join(p for p in content_parts if p)
                source_url = config['permits_url']

                if dry_run:
                    logger.info(
                        f'[DRY RUN] Would create permit lead: '
                        f'{address or permit_type} in {config["name"]}'
                    )
                    stats['created'] += 1
                    continue

                # Remote mode
                if remote:
                    payload = {
                        'platform': 'public_records',
                        'source_url': source_url,
                        'source_content': content,
                        'author': applicant,
                        'confidence': 'high',
                        'detected_category': 'BUILDING_PERMIT',
                        'raw_data': {
                            'source_type': 'town_building_permit',
                            'permit_type': permit_type,
                            'address': address,
                            'applicant': applicant,
                            'description': description[:500],
                            'town': config['name'],
                            'county': config['county'],
                            'state': 'NY',
                            'services_mapped': services,
                        },
                    }
                    ok, status_code, body = _post_lead_remote(
                        ingest_url, ingest_key, payload,
                    )
                    if ok:
                        if status_code == 201:
                            stats['created'] += 1
                        else:
                            stats['duplicates'] += 1
                    else:
                        stats['errors'] += 1
                    continue

                # Local mode — create lead via standard pipeline
                lead, created, num_assigned = process_lead(
                    platform='public_records',
                    source_url=source_url,
                    content=content,
                    author=applicant,
                    posted_at=filing_date,
                    raw_data={
                        'source_type': 'town_building_permit',
                        'permit_type': permit_type,
                        'address': address,
                        'applicant': applicant,
                        'description': description[:500],
                        'town': config['name'],
                        'county': config['county'],
                        'state': 'NY',
                        'services_mapped': services,
                    },
                    contact_name=applicant,
                    contact_address=address,
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except RateLimitHit:
                break
            except Exception as e:
                logger.error(
                    f'[town_permit] Error processing permit '
                    f'{permit.get("address", "?")}: {e}'
                )
                stats['errors'] += 1

    logger.info(f'Town permit monitor complete: {stats}')
    return stats
