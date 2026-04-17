"""
NYC Local Law 11 facade inspection monitor for SalesSignal AI.

Monitors NYC Department of Buildings (DOB) facade inspection filings
under Local Law 11 / FISP (Facade Inspection and Safety Program).

Buildings over 6 stories are required to have exterior wall inspections
every 5 years. When a facade is classified as UNSAFE or SWARMP
(Safe With a Repair and Maintenance Program), the building owner is
LEGALLY REQUIRED to make repairs — this is forced, high-urgency demand.

Data sources:
  1. NYC Open Data — Facade Inspection and Safety Program dataset
     (dataset kbkm-k84w or similar DOB facade filing data)
  2. NYC DOB BIS (Building Information System) HTML scrape fallback

Lead categories: Masonry, Waterproofing, Painting, Scaffolding
Urgency: 'hot' — mandatory repairs required by law
"""
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NYC Open Data configuration
# -------------------------------------------------------------------
NYC_OPEN_DATA_BASE = 'https://data.cityofnewyork.us/resource'
FACADE_DATASET_ID = 'kbkm-k84w'  # FISP filings

# DOB BIS fallback URL
DOB_BIS_URL = 'https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp'

# Borough normalization
BOROUGH_MAP = {
    'manhattan': 'MANHATTAN',
    'bronx': 'BRONX',
    'brooklyn': 'BROOKLYN',
    'queens': 'QUEENS',
    'staten island': 'STATEN ISLAND',
    '1': 'MANHATTAN',
    '2': 'BRONX',
    '3': 'BROOKLYN',
    '4': 'QUEENS',
    '5': 'STATEN ISLAND',
    'mn': 'MANHATTAN',
    'bx': 'BRONX',
    'bk': 'BROOKLYN',
    'qn': 'QUEENS',
    'si': 'STATEN ISLAND',
}

# Facade inspection status classifications
# SAFE = no action needed (not a lead)
# SWARMP = Safe With A Repair and Maintenance Program (needs work)
# UNSAFE = requires immediate action
ACTIONABLE_STATUSES = {
    'unsafe': {
        'urgency': 'hot',
        'urgency_score': 95,
        'note': 'UNSAFE facade — mandatory immediate repairs required by NYC DOB',
    },
    'swarmp': {
        'urgency': 'hot',
        'urgency_score': 85,
        'note': 'SWARMP — Safe With Repair and Maintenance Program required',
    },
    'precarious': {
        'urgency': 'hot',
        'urgency_score': 95,
        'note': 'PRECARIOUS condition — emergency facade repairs required',
    },
}

# Services needed for facade repairs
FACADE_SERVICES = [
    'masonry', 'waterproofing', 'painting', 'scaffolding',
    'general contractor', 'structural engineer', 'architect',
]

# More specific services based on facade condition keywords
CONDITION_SERVICE_MAP = {
    'crack': ['masonry', 'structural engineer', 'waterproofing'],
    'spall': ['masonry', 'concrete repair'],
    'water': ['waterproofing', 'masonry'],
    'leak': ['waterproofing', 'plumber'],
    'brick': ['masonry', 'tuckpointing'],
    'mortar': ['masonry', 'tuckpointing'],
    'terra cotta': ['masonry', 'restoration specialist'],
    'cornice': ['masonry', 'scaffolding', 'restoration specialist'],
    'lintel': ['masonry', 'structural engineer'],
    'parapet': ['masonry', 'waterproofing'],
    'balcony': ['masonry', 'structural engineer', 'general contractor'],
    'fire escape': ['structural engineer', 'ironwork', 'painter'],
    'window sill': ['masonry', 'waterproofing'],
    'paint': ['painting', 'exterior contractor'],
    'stucco': ['masonry', 'stucco contractor'],
    'stone': ['masonry', 'stone restoration'],
    'iron': ['ironwork', 'painter'],
    'steel': ['structural engineer', 'ironwork'],
    'concrete': ['concrete repair', 'structural engineer'],
    'scaffold': ['scaffolding'],
}


class FacadeInspectionScraper(BaseScraper):
    MONITOR_NAME = 'nyc_facade_inspection'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 40
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 720  # 12 hours — facade filings don't change fast
    RESPECT_ROBOTS = True


def _parse_date(date_str):
    """Parse common date formats from DOB/Open Data."""
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


def _normalize_borough(borough_str):
    """Normalize borough name to uppercase standard form."""
    if not borough_str:
        return ''
    return BOROUGH_MAP.get(borough_str.strip().lower(), borough_str.strip().upper())


def _detect_services(status, condition_text=''):
    """Determine services needed based on facade status and condition."""
    services = set(FACADE_SERVICES)

    if condition_text:
        text_lower = condition_text.lower()
        for key, service_list in CONDITION_SERVICE_MAP.items():
            if key in text_lower:
                services.update(service_list)

    return list(services)


def _get_status_info(status_str):
    """Get urgency info for a given facade inspection status."""
    if not status_str:
        return None

    status_lower = status_str.strip().lower()

    for key, info in ACTIONABLE_STATUSES.items():
        if key in status_lower:
            return info

    return None


def _build_address(record):
    """Build full address from Open Data facade inspection fields."""
    parts = []

    # Try various address field names
    for addr_field in ('address', 'street_address', 'house_number'):
        val = record.get(addr_field, '').strip()
        if val:
            parts.append(val)
            break

    street = record.get('street_name', '').strip()
    if street and parts and not any(street.lower() in p.lower() for p in parts):
        parts[0] = f'{parts[0]} {street}'

    borough = record.get('borough', '') or record.get('boro', '')
    if borough:
        parts.append(_normalize_borough(borough))

    parts.append('NY')

    zipcode = record.get('zip_code', '') or record.get('zipcode', '')
    if zipcode:
        parts.append(str(zipcode).strip())

    return ', '.join(parts) if parts else ''


def _fetch_open_data_filings(scraper, borough_filter):
    """
    Fetch facade inspection filings from NYC Open Data SODA API.
    Returns list of filing dicts for actionable (UNSAFE/SWARMP) statuses.
    """
    # Build SODA query — get recent filings
    where_clauses = []

    # Filter for actionable statuses
    status_filters = []
    for status_key in ACTIONABLE_STATUSES:
        status_filters.append(f"upper(filing_status) LIKE '%{status_key.upper()}%'")
    where_clauses.append(f'({" OR ".join(status_filters)})')

    if borough_filter:
        normalized = _normalize_borough(borough_filter)
        where_clauses.append(f"upper(borough) = '{normalized}'")

    where_str = ' AND '.join(where_clauses)
    url = (
        f'{NYC_OPEN_DATA_BASE}/{FACADE_DATASET_ID}.json'
        f'?$where={where_str}'
        f'&$limit=1000'
        f'&$order=filing_date DESC'
    )

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[nyc_facade_inspection] Error fetching Open Data: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[nyc_facade_inspection] Open Data returned '
            f'{resp.status_code if resp else "None"}: '
            f'{resp.text[:200] if resp else ""}'
        )
        return []

    try:
        data = resp.json()
    except Exception:
        logger.error('[nyc_facade_inspection] Failed to parse Open Data JSON')
        return []

    if not isinstance(data, list):
        return []

    filings = []
    for record in data:
        filing_status = record.get('filing_status', '') or record.get('status', '')
        status_info = _get_status_info(filing_status)

        if not status_info:
            continue

        filing = {
            'address': _build_address(record),
            'borough': _normalize_borough(
                record.get('borough', '') or record.get('boro', '')
            ),
            'block': record.get('block', '').strip(),
            'lot': record.get('lot', '').strip(),
            'bin': record.get('bin', '') or record.get('bin_number', ''),
            'filing_date': record.get('filing_date', ''),
            'inspection_date': record.get('inspection_date', ''),
            'status': filing_status.strip(),
            'status_info': status_info,
            'condition_description': record.get('condition_description', '')
                or record.get('remarks', ''),
            'building_class': record.get('building_class', ''),
            'stories': record.get('stories', '') or record.get('number_of_stories', ''),
            'owner': record.get('owner', '') or record.get('owner_name', ''),
            'source': 'nyc_open_data',
        }
        if filing['address'] or (filing['block'] and filing['lot']):
            filings.append(filing)

    logger.info(
        f'[nyc_facade_inspection] Fetched {len(filings)} actionable filings '
        f'from Open Data (of {len(data)} total records)'
    )
    return filings


def _scrape_dob_bis(scraper, borough_filter):
    """
    Scrape NYC DOB BIS for facade inspection filings.
    Fallback if Open Data dataset is unavailable.
    Returns list of filing dicts.
    """
    # DOB BIS search page for facade filings
    search_url = (
        f'{DOB_BIS_URL}?allcount=0001&allborough=0'
        f'&allblock=&alllot=&go10=+GO+&requestid=0'
    )

    try:
        resp = scraper.get(search_url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[nyc_facade_inspection] Error fetching DOB BIS: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[nyc_facade_inspection] DOB BIS returned '
            f'{resp.status_code if resp else "None"}'
        )
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.select_one('table.data') or soup.select_one('table')
    if not table:
        logger.warning('[nyc_facade_inspection] No table found on DOB BIS page')
        return []

    rows = table.select('tr')
    filings = []

    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if len(cells) < 4:
            continue

        try:
            address = cells[0].get_text(strip=True)
            borough = cells[1].get_text(strip=True) if len(cells) > 1 else ''
            filing_date = cells[2].get_text(strip=True) if len(cells) > 2 else ''
            status = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            block = cells[4].get_text(strip=True) if len(cells) > 4 else ''
            lot = cells[5].get_text(strip=True) if len(cells) > 5 else ''

            status_info = _get_status_info(status)
            if not status_info:
                continue

            # Apply borough filter
            if borough_filter:
                normalized_filter = _normalize_borough(borough_filter)
                if _normalize_borough(borough) != normalized_filter:
                    continue

            filings.append({
                'address': f'{address}, {_normalize_borough(borough)}, NY',
                'borough': _normalize_borough(borough),
                'block': block,
                'lot': lot,
                'bin': '',
                'filing_date': filing_date,
                'inspection_date': '',
                'status': status,
                'status_info': status_info,
                'condition_description': '',
                'building_class': '',
                'stories': '',
                'owner': '',
                'source': 'dob_bis',
            })
        except (IndexError, AttributeError):
            continue

    logger.info(f'[nyc_facade_inspection] Scraped {len(filings)} filings from DOB BIS')
    return filings


def monitor_nyc_facade_inspections(borough=None, dry_run=False, remote=False):
    """
    Monitor NYC Local Law 11 facade inspections for UNSAFE/SWARMP filings.

    When a building facade is classified as UNSAFE or SWARMP, the building
    owner is legally required to hire contractors for repairs. This creates
    guaranteed demand for masonry, waterproofing, painting, and scaffolding.

    Args:
        borough: Filter by borough (e.g. 'manhattan', 'brooklyn', 'queens').
                 Set to None to search all boroughs.
        dry_run: If True, log matches without creating Lead records
        remote: If True, skip HTML scraping and use only API sources

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = FacadeInspectionScraper()

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

    all_filings = []

    # Source 1: NYC Open Data SODA API (primary)
    stats['sources_checked'] += 1
    try:
        open_data_filings = _fetch_open_data_filings(scraper, borough)
        all_filings.extend(open_data_filings)
    except RateLimitHit:
        logger.warning('[nyc_facade_inspection] Rate limited on Open Data')
    except Exception as e:
        logger.error(f'[nyc_facade_inspection] Error with Open Data: {e}')
        stats['errors'] += 1

    # Source 2: DOB BIS scrape (fallback, skip if remote)
    if not remote and not scraper.is_stopped and len(all_filings) == 0:
        stats['sources_checked'] += 1
        try:
            bis_filings = _scrape_dob_bis(scraper, borough)
            all_filings.extend(bis_filings)
        except RateLimitHit:
            logger.warning('[nyc_facade_inspection] Rate limited on DOB BIS')
        except Exception as e:
            logger.error(f'[nyc_facade_inspection] Error with DOB BIS: {e}')
            stats['errors'] += 1

    stats['items_scraped'] = len(all_filings)

    # Process each filing
    seen = set()
    for filing in all_filings:
        try:
            address = filing.get('address', '').strip()
            borough_name = filing.get('borough', '').strip()
            block = filing.get('block', '').strip()
            lot = filing.get('lot', '').strip()
            filing_date_str = filing.get('filing_date', '')
            inspection_date_str = filing.get('inspection_date', '')
            status = filing.get('status', '')
            status_info = filing.get('status_info', {})
            condition_desc = filing.get('condition_description', '')
            stories = filing.get('stories', '')
            owner = filing.get('owner', '')
            bin_number = filing.get('bin', '')

            if not address and not (block and lot):
                continue

            # Dedup by address + block/lot
            dedup_key = f'{address.lower()}|{block}|{lot}'
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Parse dates
            filing_date = _parse_date(filing_date_str)
            inspection_date = _parse_date(inspection_date_str)
            posted_at = filing_date or inspection_date

            # Detect services
            services = _detect_services(status, condition_desc)

            urgency_level = status_info.get('urgency', 'hot')
            urgency_note = status_info.get('note', 'Facade repair required by law')

            # Build lead content
            content_parts = [
                f'FACADE INSPECTION — {status.upper()}: {address or f"Block {block}, Lot {lot}"}',
            ]
            if borough_name:
                content_parts.append(f'Borough: {borough_name}')
            if block and lot:
                content_parts.append(f'Block: {block}, Lot: {lot}')
            if bin_number:
                content_parts.append(f'BIN: {bin_number}')
            if stories:
                content_parts.append(f'Stories: {stories}')
            if owner:
                content_parts.append(f'Owner: {owner}')
            if filing_date:
                days_ago = (timezone.now() - filing_date).days
                content_parts.append(f'Filed: {days_ago} days ago')
            if condition_desc:
                content_parts.append(f'Condition: {condition_desc[:500]}')

            content_parts.append(f'Status: {urgency_note}')
            content_parts.append(f'Services needed: {", ".join(services[:8])}')
            content_parts.append(
                'Building owner is legally required to make facade repairs '
                'under NYC Local Law 11 / FISP.'
            )

            content = '\n'.join(content_parts)

            source_url = (
                f'{NYC_OPEN_DATA_BASE}/{FACADE_DATASET_ID}'
                if filing.get('source') == 'nyc_open_data'
                else DOB_BIS_URL
            )

            if dry_run:
                logger.info(
                    f'[DRY RUN] Would create facade lead: '
                    f'{address} — {status.upper()}'
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
                    'source_type': 'facade_inspection',
                    'address': address,
                    'borough': borough_name,
                    'block': block,
                    'lot': lot,
                    'bin': bin_number,
                    'filing_date': filing_date_str,
                    'inspection_date': inspection_date_str,
                    'status': status,
                    'urgency_level': urgency_level,
                    'condition_description': condition_desc[:500],
                    'stories': stories,
                    'owner': owner,
                    'building_class': filing.get('building_class', ''),
                    'services_mapped': services,
                    'data_source': filing.get('source', ''),
                },
                state='NY',
                region=borough_name,
                source_group='public_records',
                source_type='building_violations',
                contact_name=owner,
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
                f'[nyc_facade_inspection] Error processing filing at '
                f'{filing.get("address", "unknown")}: {e}'
            )
            stats['errors'] += 1

    logger.info(f'NYC facade inspection monitor complete: {stats}')
    return stats
