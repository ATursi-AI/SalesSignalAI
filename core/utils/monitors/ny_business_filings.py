"""
NY Department of State business filing monitor for SalesSignal AI.

Scrapes the NY DOS Corporation Search portal for recently filed
LLCs, Corporations, and DBAs. New businesses need services before
they even open — insurance, accounting, legal, cleaning, IT, signage.

Source: appext20.dos.ny.gov/corp_public/CORPSEARCH.ENTITY_SEARCH_ENTRY
The search form POSTs to CORPSEARCH.ENTITY_INFORMATION with date range
and county filters. Results are parsed from HTML tables.

Targeted counties: Nassau, Suffolk, Queens, Kings, New York, Bronx,
Richmond, Westchester — configurable via function parameter.
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
# Configuration
# -------------------------------------------------------------------

DOS_SEARCH_URL = (
    'https://appext20.dos.ny.gov/corp_public/CORPSEARCH.ENTITY_SEARCH_ENTRY'
)
DOS_RESULTS_URL = (
    'https://appext20.dos.ny.gov/corp_public/CORPSEARCH.ENTITY_INFORMATION'
)

# Entity type mapping — what the DOS portal returns
ENTITY_TYPES = {
    'DOMESTIC LIMITED LIABILITY COMPANY': 'LLC',
    'DOMESTIC BUSINESS CORPORATION': 'CORP',
    'DOMESTIC NOT-FOR-PROFIT CORPORATION': 'NONPROFIT',
    'FOREIGN LIMITED LIABILITY COMPANY': 'LLC (Foreign)',
    'FOREIGN BUSINESS CORPORATION': 'CORP (Foreign)',
    'ASSUMED NAME': 'DBA',
    'TRADE NAME': 'DBA',
}

# Counties available on the DOS search form
VALID_COUNTIES = [
    'NASSAU', 'SUFFOLK', 'QUEENS', 'KINGS', 'NEW YORK', 'BRONX',
    'RICHMOND', 'WESTCHESTER', 'ROCKLAND', 'ORANGE', 'DUTCHESS',
    'PUTNAM', 'ALBANY', 'ERIE', 'MONROE', 'ONONDAGA',
]

# New business filing -> lead service categories
NEW_BUSINESS_SERVICES = [
    'Insurance', 'Accountant', 'Lawyer', 'Commercial Cleaning',
    'IT Support', 'Web Design', 'Signage', 'HVAC', 'Security',
]

# Business name keywords -> additional specific services
BUSINESS_NAME_SERVICE_MAP = {
    'dental': ['commercial cleaning', 'medical waste', 'plumber', 'HVAC'],
    'medical': ['commercial cleaning', 'medical waste', 'HVAC', 'IT support'],
    'restaurant': ['commercial cleaning', 'pest control', 'HVAC', 'grease trap', 'signage'],
    'cafe': ['commercial cleaning', 'pest control', 'signage'],
    'bar': ['commercial cleaning', 'pest control', 'security', 'signage'],
    'salon': ['commercial cleaning', 'plumber', 'signage', 'interior design'],
    'spa': ['commercial cleaning', 'plumber', 'HVAC', 'interior design'],
    'gym': ['commercial cleaning', 'HVAC', 'plumber', 'signage', 'security'],
    'fitness': ['commercial cleaning', 'HVAC', 'plumber', 'signage'],
    'retail': ['commercial cleaning', 'security', 'signage', 'IT support'],
    'consulting': ['IT support', 'office cleaning', 'insurance'],
    'law': ['IT support', 'office cleaning', 'insurance', 'security'],
    'accounting': ['IT support', 'office cleaning', 'insurance'],
    'construction': ['insurance', 'accounting', 'IT support'],
    'landscaping': ['insurance', 'accounting', 'equipment repair'],
    'plumbing': ['insurance', 'accounting', 'IT support'],
    'daycare': ['commercial cleaning', 'pest control', 'security', 'insurance'],
    'veterinar': ['commercial cleaning', 'pest control', 'HVAC', 'plumber'],
    'auto': ['commercial cleaning', 'signage', 'security', 'HVAC'],
    'hotel': ['commercial cleaning', 'HVAC', 'pest control', 'security', 'landscaping'],
}


class NYBusinessFilingScraper(BaseScraper):
    MONITOR_NAME = 'ny_business_filing'
    DELAY_MIN = 4.0
    DELAY_MAX = 10.0
    MAX_REQUESTS_PER_RUN = 25
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _detect_services_from_name(business_name):
    """Map a business name to likely services needed."""
    if not business_name:
        return NEW_BUSINESS_SERVICES

    name_lower = business_name.lower()
    services = set()

    for key, service_list in BUSINESS_NAME_SERVICE_MAP.items():
        if key in name_lower:
            services.update(service_list)

    return list(services) if services else NEW_BUSINESS_SERVICES


def _normalize_entity_type(raw_type):
    """Normalize the DOS entity type to a short label."""
    if not raw_type:
        return 'Unknown'
    raw_upper = raw_type.strip().upper()
    for key, label in ENTITY_TYPES.items():
        if key in raw_upper:
            return label
    return raw_type.strip()


def _parse_date(date_str):
    """Parse common date formats from DOS portal."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y',
        '%m-%d-%Y', '%b %d, %Y', '%B %d, %Y',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue

    return None


def _build_search_payload(county, date_from, date_to):
    """Build the POST form data for the DOS entity search."""
    return {
        'p_entity_name': '',
        'p_name_type': 'STARTS WITH',
        'p_search_type': 'BEGINS',
        'p_filing_date_from': date_from.strftime('%m/%d/%Y'),
        'p_filing_date_to': date_to.strftime('%m/%d/%Y'),
        'p_county': county.upper(),
    }


def _scrape_filings(scraper, county, date_from, date_to):
    """
    POST to the NY DOS search form and parse the results table.
    Returns list of filing dicts.
    """
    payload = _build_search_payload(county, date_from, date_to)

    # First, GET the search page to establish session cookies
    try:
        entry_resp = scraper.get(DOS_SEARCH_URL)
        if not entry_resp or entry_resp.status_code != 200:
            logger.warning(f'[ny_business_filing] Could not load search form')
            return []
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_business_filing] Search form error: {e}')
        return []

    # POST the search — use the session from BaseScraper
    try:
        scraper._session.headers['User-Agent'] = scraper._session.headers.get(
            'User-Agent', 'Mozilla/5.0'
        )
        resp = scraper._session.post(
            DOS_RESULTS_URL,
            data=payload,
            timeout=scraper.TIMEOUT,
        )
        scraper._request_count += 1

        if resp.status_code in (429, 403):
            scraper._stopped = True
            raise RateLimitHit(
                f'{resp.status_code} from DOS portal — run stopped'
            )

        if resp.status_code != 200:
            logger.warning(
                f'[ny_business_filing] Search returned {resp.status_code}'
            )
            return []
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_business_filing] Search POST failed: {e}')
        return []

    # Parse the HTML results
    soup = BeautifulSoup(resp.text, 'html.parser')
    filings = []

    # The DOS portal renders results in a table
    tables = soup.find_all('table')
    result_table = None
    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        if any('entity' in h or 'name' in h for h in headers):
            result_table = table
            break

    if not result_table:
        # Try finding any table with filing data
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) > 2:
                result_table = table
                break

    if not result_table:
        logger.info(f'[ny_business_filing] No results table found for {county}')
        return []

    rows = result_table.find_all('tr')
    for row in rows[1:]:  # skip header
        cells = row.find_all('td')
        if len(cells) < 3:
            continue

        try:
            filing = {
                'business_name': cells[0].get_text(strip=True) if len(cells) > 0 else '',
                'entity_type': cells[1].get_text(strip=True) if len(cells) > 1 else '',
                'filing_date': cells[2].get_text(strip=True) if len(cells) > 2 else '',
                'county': county.upper(),
                'registered_agent': cells[3].get_text(strip=True) if len(cells) > 3 else '',
                'address': cells[4].get_text(strip=True) if len(cells) > 4 else '',
                'status': cells[5].get_text(strip=True) if len(cells) > 5 else '',
            }

            # Also check for detail links
            link = cells[0].find('a')
            if link and link.get('href'):
                filing['detail_url'] = link['href']

            if filing['business_name']:
                filings.append(filing)
        except (IndexError, AttributeError):
            continue

    logger.info(
        f'[ny_business_filing] Parsed {len(filings)} filings for {county}'
    )
    return filings


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

def monitor_ny_business_filings(county='nassau', days=7, dry_run=False, remote=False):
    """
    Monitor NY Department of State for new business filings.

    Searches the DOS Corporation Search portal by county and date range,
    parses results, and creates leads for each new filing.

    Args:
        county: county name (default: nassau). Use 'all' for all valid counties.
        days: how many days back to search (default: 7)
        dry_run: if True, log matches without creating Lead records
        remote: if True, POST leads to REMOTE_INGEST_URL instead of local DB

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = NYBusinessFilingScraper()

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

    # Determine counties to search
    if county.lower() == 'all':
        counties = VALID_COUNTIES
    else:
        counties = [c.strip().upper() for c in county.split(',')]

    stats = {
        'sources_checked': 0,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)

    counties = scraper.shuffle(counties)

    for county_name in counties:
        if scraper.is_stopped:
            break

        if county_name not in VALID_COUNTIES:
            logger.warning(
                f'[ny_business_filing] Unknown county: {county_name}, skipping'
            )
            continue

        stats['sources_checked'] += 1
        logger.info(
            f'[ny_business_filing] Searching {county_name} county '
            f'({date_from.strftime("%m/%d/%Y")} - {date_to.strftime("%m/%d/%Y")})'
        )

        try:
            filings = _scrape_filings(scraper, county_name, date_from, date_to)
        except RateLimitHit:
            logger.warning('[ny_business_filing] Rate limited, stopping run')
            break
        except Exception as e:
            logger.error(f'[ny_business_filing] Error scraping {county_name}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(filings)

        for filing in filings:
            try:
                name = filing.get('business_name', '')
                if not name:
                    continue

                filing_date = _parse_date(filing.get('filing_date', ''))
                entity_type = _normalize_entity_type(filing.get('entity_type', ''))
                address = filing.get('address', '')
                agent = filing.get('registered_agent', '')

                # Detect services from business name
                services = _detect_services_from_name(name)

                # Build lead content
                content_parts = [
                    f'New Business Filing: {name}',
                    f'Entity Type: {entity_type}',
                    f'County: {county_name}, NY',
                ]
                if filing_date:
                    content_parts.append(
                        f'Filed: {filing_date.strftime("%m/%d/%Y")}'
                    )
                if address:
                    content_parts.append(f'Registered Address: {address}')
                if agent:
                    content_parts.append(f'Registered Agent: {agent}')
                content_parts.append(
                    f'Services likely needed: {", ".join(services[:7])}'
                )

                content = '\n'.join(content_parts)
                source_url = DOS_SEARCH_URL

                if dry_run:
                    logger.info(
                        f'[DRY RUN] Would create filing lead: '
                        f'{name} ({entity_type}) in {county_name}'
                    )
                    stats['created'] += 1
                    continue

                # Remote mode
                if remote:
                    payload = {
                        'platform': 'public_records',
                        'source_url': source_url,
                        'source_content': content,
                        'author': '',
                        'confidence': 'high',
                        'detected_category': 'NEW_BUSINESS_FILING',
                        'raw_data': {
                            'source_type': 'ny_business_filing',
                            'business_name': name,
                            'entity_type': entity_type,
                            'county': county_name,
                            'state': 'NY',
                            'address': address,
                            'registered_agent': agent,
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
                    author='',
                    posted_at=filing_date,
                    raw_data={
                        'source_type': 'ny_business_filing',
                        'business_name': name,
                        'entity_type': entity_type,
                        'county': county_name,
                        'state': 'NY',
                        'address': address,
                        'registered_agent': agent,
                        'services_mapped': services,
                        'detected_category': 'NEW_BUSINESS_FILING',
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
                logger.error(
                    f'[ny_business_filing] Error processing filing '
                    f'{filing.get("business_name", "?")}: {e}'
                )
                stats['errors'] += 1

    logger.info(f'NY business filing monitor complete: {stats}')
    return stats
