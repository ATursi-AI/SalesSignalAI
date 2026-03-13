"""
NY State Liquor Authority license monitor for SalesSignal AI.

Scrapes the NY State Liquor Authority (SLA) public data for new license
applications and approvals. New liquor licenses indicate a restaurant, bar,
club, or store is opening or changing hands — these businesses need
commercial cleaning, pest control, HVAC, insurance, legal, accounting,
fire protection, and kitchen equipment services.

Two data sources:
  1. NYSLA recent approvals page (HTML table scrape)
  2. NY Open Data SLA dataset via SODA API

Lead categories: Commercial Cleaning, Pest Control, HVAC, Insurance,
                 Accountant, Lawyer, Fire Protection, Kitchen Equipment
"""
import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NYSLA data endpoints
# -------------------------------------------------------------------
NYSLA_APPROVALS_URL = (
    'https://www.tran.sla.ny.gov/servlet/ApplicationServlet'
    '?pageName=com.ibm.nysla.data.publicquery'
    '.PublicQuerySuccessfulApplicationsPage'
)

# NY Open Data — SLA license dataset (SODA API)
NY_OPEN_DATA_BASE = 'https://data.ny.gov/resource'
SLA_DATASET_ID = 'wg8y-fzsj'  # NY SLA Liquor Authority active/recent licenses

# County name normalization for filtering
COUNTY_ALIASES = {
    'nassau': 'NASSAU',
    'suffolk': 'SUFFOLK',
    'queens': 'QUEENS',
    'kings': 'KINGS',
    'brooklyn': 'KINGS',
    'bronx': 'BRONX',
    'manhattan': 'NEW YORK',
    'new york': 'NEW YORK',
    'richmond': 'RICHMOND',
    'staten island': 'RICHMOND',
    'westchester': 'WESTCHESTER',
    'rockland': 'ROCKLAND',
    'orange': 'ORANGE',
    'dutchess': 'DUTCHESS',
    'putnam': 'PUTNAM',
    'erie': 'ERIE',
    'monroe': 'MONROE',
    'albany': 'ALBANY',
    'onondaga': 'ONONDAGA',
}

# License type → business type for lead context
LICENSE_TYPE_MAP = {
    'OP': 'On-Premises Liquor (restaurant/bar)',
    'RL': 'Restaurant Liquor',
    'RW': 'Restaurant Wine',
    'TW': 'Tavern Wine',
    'EB': 'Eating Place Beer',
    'L': 'Liquor Store',
    'W': 'Wine Store',
    'A': 'Liquor/Wine/Beer Store',
    'CL': 'Club Liquor',
    'HL': 'Hotel Liquor',
    'CR': 'Catering',
    'BC': 'Bottle Club',
    'CF': 'Conference/Exhibition Center',
    'DS': 'Drug Store',
    'WB': 'Wholesale',
}

# Services that new liquor-licensed businesses typically need
LIQUOR_LICENSE_SERVICES = [
    'commercial cleaning', 'pest control', 'HVAC', 'insurance',
    'accountant', 'lawyer', 'fire protection', 'kitchen equipment',
    'signage', 'plumber', 'electrician', 'security',
]

# Subset of services depending on establishment type
RESTAURANT_SERVICES = [
    'commercial cleaning', 'pest control', 'HVAC', 'insurance',
    'accountant', 'lawyer', 'fire protection', 'kitchen equipment',
    'grease trap cleaning', 'plumber',
]

RETAIL_LIQUOR_SERVICES = [
    'commercial cleaning', 'insurance', 'accountant', 'lawyer',
    'security', 'signage', 'HVAC',
]


class LiquorLicenseScraper(BaseScraper):
    MONITOR_NAME = 'ny_liquor_license'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 40
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _parse_date(date_str):
    """Parse common date formats from SLA data."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y',
        '%Y-%m-%dT%H:%M:%S', '%m-%d-%Y',
        '%b %d, %Y', '%B %d, %Y',
        '%d-%b-%Y', '%Y/%m/%d',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue

    # ISO format fallback
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        pass

    return None


def _normalize_county(county_str):
    """Normalize county name to uppercase standard form."""
    if not county_str:
        return ''
    return COUNTY_ALIASES.get(county_str.strip().lower(), county_str.strip().upper())


def _detect_services(license_type_code):
    """Map license type code to relevant services."""
    if not license_type_code:
        return LIQUOR_LICENSE_SERVICES[:8]

    code = license_type_code.strip().upper()

    # Restaurant / bar / on-premises types
    if code in ('OP', 'RL', 'RW', 'TW', 'EB', 'HL', 'CR', 'CL'):
        return RESTAURANT_SERVICES

    # Retail liquor store types
    if code in ('L', 'W', 'A', 'DS'):
        return RETAIL_LIQUOR_SERVICES

    return LIQUOR_LICENSE_SERVICES[:8]


def _get_license_type_display(code):
    """Get human-readable license type."""
    if not code:
        return 'Liquor License'
    return LICENSE_TYPE_MAP.get(code.strip().upper(), f'License Type {code}')


def _scrape_sla_approvals(scraper):
    """
    Scrape the NYSLA successful applications page for recent approvals.
    Returns list of license dicts.
    """
    try:
        resp = scraper.get(NYSLA_APPROVALS_URL)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_liquor_license] Error fetching SLA approvals page: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(f'[ny_liquor_license] SLA approvals page returned {resp.status_code if resp else "None"}')
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.select_one('table.data-table') or soup.select_one('table')
    if not table:
        logger.warning('[ny_liquor_license] No table found on SLA approvals page')
        return []

    rows = table.select('tr')
    licenses = []

    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if len(cells) < 4:
            continue

        try:
            license_rec = {
                'business_name': cells[0].get_text(strip=True),
                'address': cells[1].get_text(strip=True) if len(cells) > 1 else '',
                'license_type': cells[2].get_text(strip=True) if len(cells) > 2 else '',
                'county': cells[3].get_text(strip=True) if len(cells) > 3 else '',
                'application_date': cells[4].get_text(strip=True) if len(cells) > 4 else '',
                'approval_date': cells[5].get_text(strip=True) if len(cells) > 5 else '',
                'source': 'sla_website',
            }
            if license_rec['business_name']:
                licenses.append(license_rec)
        except (IndexError, AttributeError):
            continue

    logger.info(f'[ny_liquor_license] Scraped {len(licenses)} records from SLA approvals page')
    return licenses


def _scrape_ny_open_data(scraper, county_filter, days):
    """
    Query the NY Open Data SODA API for recent SLA license records.
    Returns list of license dicts.
    """
    cutoff_date = (timezone.now() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')

    # Build SODA query
    where_clauses = [f"license_effective_date > '{cutoff_date}'"]
    if county_filter:
        normalized = _normalize_county(county_filter)
        where_clauses.append(f"county_name = '{normalized}'")

    where_str = ' AND '.join(where_clauses)
    url = (
        f'{NY_OPEN_DATA_BASE}/{SLA_DATASET_ID}.json'
        f'?$where={where_str}'
        f'&$limit=1000'
        f'&$order=license_effective_date DESC'
    )

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_liquor_license] Error fetching NY Open Data: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[ny_liquor_license] NY Open Data returned '
            f'{resp.status_code if resp else "None"}: {resp.text[:200] if resp else ""}'
        )
        return []

    try:
        data = resp.json()
    except Exception:
        logger.error('[ny_liquor_license] Failed to parse NY Open Data JSON response')
        return []

    if not isinstance(data, list):
        logger.warning('[ny_liquor_license] NY Open Data response is not a list')
        return []

    licenses = []
    for item in data:
        license_rec = {
            'business_name': item.get('premise_name', '') or item.get('dba', ''),
            'address': _build_address(item),
            'license_type': item.get('license_type_code', '') or item.get('license_type', ''),
            'county': item.get('county_name', ''),
            'application_date': item.get('license_effective_date', ''),
            'approval_date': item.get('license_effective_date', ''),
            'license_number': item.get('serial_number', ''),
            'license_class': item.get('license_class', ''),
            'source': 'ny_open_data',
        }
        if license_rec['business_name']:
            licenses.append(license_rec)

    logger.info(f'[ny_liquor_license] Fetched {len(licenses)} records from NY Open Data')
    return licenses


def _build_address(item):
    """Build a full address string from Open Data fields."""
    parts = []
    for field in ('premises_address', 'actual_address', 'address'):
        val = item.get(field, '')
        if val:
            parts.append(val)
            break

    city = item.get('city', '') or item.get('premises_city', '')
    state = item.get('state', 'NY')
    zipcode = item.get('zip', '') or item.get('zip_code', '')

    if city:
        parts.append(city)
    if state:
        parts.append(state)
    if zipcode:
        parts.append(str(zipcode))

    return ', '.join(parts) if parts else ''


def monitor_ny_liquor_licenses(county='nassau', days=30, dry_run=False, remote=False):
    """
    Monitor NY State Liquor Authority for new license applications/approvals.

    New liquor licenses signal a restaurant, bar, or liquor store opening —
    these businesses need commercial cleaning, pest control, HVAC, insurance,
    legal, accounting, fire protection, and kitchen equipment services.

    Args:
        county: County to filter (e.g. 'nassau', 'suffolk', 'queens').
                Set to None to search all counties.
        days: How many days back to search (default: 30)
        dry_run: If True, log matches without creating Lead records
        remote: If True, skip HTML scraping and use only API sources

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = LiquorLicenseScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    stats = {
        'sources_checked': 0,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    all_licenses = []
    cutoff = timezone.now() - timedelta(days=days)

    # Source 1: NY Open Data SODA API (always try this first)
    stats['sources_checked'] += 1
    try:
        open_data_licenses = _scrape_ny_open_data(scraper, county, days)
        all_licenses.extend(open_data_licenses)
    except RateLimitHit:
        logger.warning('[ny_liquor_license] Rate limited on NY Open Data')
    except Exception as e:
        logger.error(f'[ny_liquor_license] Error with NY Open Data: {e}')
        stats['errors'] += 1

    # Source 2: NYSLA approvals page (HTML scrape, skip if remote)
    if not remote and not scraper.is_stopped:
        stats['sources_checked'] += 1
        try:
            sla_licenses = _scrape_sla_approvals(scraper)
            # Filter by county if specified
            if county:
                normalized_county = _normalize_county(county)
                sla_licenses = [
                    lic for lic in sla_licenses
                    if _normalize_county(lic.get('county', '')) == normalized_county
                    or not lic.get('county')
                ]
            all_licenses.extend(sla_licenses)
        except RateLimitHit:
            logger.warning('[ny_liquor_license] Rate limited on SLA website')
        except Exception as e:
            logger.error(f'[ny_liquor_license] Error with SLA website: {e}')
            stats['errors'] += 1

    stats['items_scraped'] = len(all_licenses)

    # Deduplicate by business name + address
    seen = set()
    for license_rec in all_licenses:
        try:
            biz_name = license_rec.get('business_name', '').strip()
            address = license_rec.get('address', '').strip()
            license_type_code = license_rec.get('license_type', '').strip()
            county_name = license_rec.get('county', '').strip()
            app_date_str = license_rec.get('application_date', '')
            approval_date_str = license_rec.get('approval_date', '')

            if not biz_name:
                continue

            # Dedup key
            dedup_key = f'{biz_name.lower()}|{address.lower()}'
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Parse dates
            app_date = _parse_date(app_date_str)
            approval_date = _parse_date(approval_date_str)
            posted_at = approval_date or app_date

            # Skip old licenses
            if posted_at and posted_at < cutoff:
                continue

            # Determine services
            services = _detect_services(license_type_code)
            license_type_display = _get_license_type_display(license_type_code)

            # Build lead content
            content_parts = [
                f'NEW LIQUOR LICENSE: {biz_name}',
                f'License Type: {license_type_display}',
            ]
            if address:
                content_parts.append(f'Address: {address}')
            if county_name:
                content_parts.append(f'County: {county_name}')
            if posted_at:
                days_ago = (timezone.now() - posted_at).days
                content_parts.append(f'Approved: {days_ago} days ago')
            content_parts.append(f'Services likely needed: {", ".join(services[:8])}')
            content_parts.append(
                'New liquor-licensed establishment opening or changing hands. '
                'Needs setup services before opening.'
            )

            content = '\n'.join(content_parts)
            source_url = NYSLA_APPROVALS_URL

            if dry_run:
                logger.info(
                    f'[DRY RUN] Would create liquor license lead: '
                    f'{biz_name} ({license_type_display}) in {county_name}'
                )
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author='',
                posted_at=posted_at,
                raw_data={
                    'source_type': 'liquor_license',
                    'business_name': biz_name,
                    'address': address,
                    'license_type_code': license_type_code,
                    'license_type_display': license_type_display,
                    'county': county_name,
                    'application_date': app_date_str,
                    'approval_date': approval_date_str,
                    'license_number': license_rec.get('license_number', ''),
                    'services_mapped': services,
                    'data_source': license_rec.get('source', ''),
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
            logger.error(f'[ny_liquor_license] Error processing license for {biz_name}: {e}')
            stats['errors'] += 1

    logger.info(f'NY liquor license monitor complete: {stats}')
    return stats
