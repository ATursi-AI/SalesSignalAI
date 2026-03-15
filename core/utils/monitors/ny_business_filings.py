"""
NY business filings monitor for SalesSignal AI.

Uses the NY Open Data SODA API to query the Department of State
Corporation and Business Entity Database:

  Dataset: k4vb-judh (https://data.ny.gov/resource/k4vb-judh.json)

Filters:
  - filing_date >= N days ago
  - cnty_prin_ofc IN target counties
  - filing_type containing INCORPORATION, ORGANIZATION, or APPLICATION FOR AUTHORITY

New businesses need services before they even open:
  Insurance, Accounting, Legal, Commercial Cleaning, IT, Signage, HVAC
"""
import logging
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NY Open Data SODA endpoint
# -------------------------------------------------------------------
SODA_URL = 'https://data.ny.gov/resource/k4vb-judh.json'

# Counties we target (SODA field: cnty_prin_ofc — mixed case in API)
DEFAULT_COUNTIES = [
    'New York', 'Bronx', 'Kings', 'Queens',
    'Nassau', 'Suffolk',
]

# Filing types that indicate a NEW business
NEW_BUSINESS_FILING_TYPES = [
    'INCORPORATION',
    'ORGANIZATION',
    'APPLICATION FOR AUTHORITY',
]

# Entity type mapping
ENTITY_TYPES = {
    'DOMESTIC LIMITED LIABILITY COMPANY': 'LLC',
    'DOMESTIC BUSINESS CORPORATION': 'Corp',
    'DOMESTIC NOT-FOR-PROFIT CORPORATION': 'Nonprofit',
    'FOREIGN LIMITED LIABILITY COMPANY': 'LLC (Foreign)',
    'FOREIGN BUSINESS CORPORATION': 'Corp (Foreign)',
    'LIMITED PARTNERSHIP': 'LP',
    'FOREIGN LIMITED PARTNERSHIP': 'LP (Foreign)',
}

# County -> display name (keys match API casing)
COUNTY_DISPLAY = {
    'New York': 'Manhattan',
    'Kings': 'Brooklyn',
    'Queens': 'Queens',
    'Bronx': 'Bronx',
    'Richmond': 'Staten Island',
    'Nassau': 'Nassau County',
    'Suffolk': 'Suffolk County',
}

# Services every new business needs
NEW_BUSINESS_SERVICES = [
    'Insurance', 'Accountant', 'Lawyer', 'Commercial Cleaning',
    'IT Support', 'Web Design', 'Signage', 'HVAC', 'Security',
]

# Business name keywords -> additional services
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
    'construction': ['insurance', 'accounting', 'IT support'],
    'landscaping': ['insurance', 'accounting', 'equipment repair'],
    'daycare': ['commercial cleaning', 'pest control', 'security', 'insurance'],
    'auto': ['commercial cleaning', 'signage', 'security', 'HVAC'],
}


def _detect_services_from_name(business_name):
    if not business_name:
        return NEW_BUSINESS_SERVICES
    name_lower = business_name.lower()
    services = set()
    for key, service_list in BUSINESS_NAME_SERVICE_MAP.items():
        if key in name_lower:
            services.update(service_list)
    return list(services) if services else NEW_BUSINESS_SERVICES


def _normalize_entity_type(raw_type):
    if not raw_type:
        return 'Unknown'
    raw_upper = raw_type.strip().upper()
    for key, label in ENTITY_TYPES.items():
        if key in raw_upper:
            return label
    return raw_type.strip()[:40]


def _parse_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%m/%d/%Y']:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _is_new_business_filing(filing_type):
    """Return True if this filing type indicates a new business."""
    if not filing_type:
        return False
    ft_upper = filing_type.upper()
    return any(t in ft_upper for t in NEW_BUSINESS_FILING_TYPES)


def _headers():
    h = {}
    token = getattr(settings, 'NY_OPEN_DATA_APP_TOKEN', '')
    if token:
        h['X-App-Token'] = token
    return h


def monitor_ny_business_filings(county=None, days=30, dry_run=False):
    """
    Monitor NY Department of State for new business filings via SODA API.

    Args:
        county: county filter — single county name, comma-separated list,
                or None for all default counties
        days: how many days back to search (default: 30)
        dry_run: if True, log matches without creating Lead records

    Returns:
        dict with counts
    """
    stats = {
        'sources_checked': 1,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    # Determine counties to query
    if county and county.lower() != 'all':
        counties = [c.strip().title() for c in county.split(',')]
    else:
        counties = DEFAULT_COUNTIES

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

    # Build SODA query
    counties_str = ','.join(f"'{c}'" for c in counties)
    where = (
        f"filing_date >= '{since}' "
        f"AND cnty_prin_ofc in({counties_str})"
    )

    params = {
        '$where': where,
        '$select': (
            'dos_id,filing_type,entity_type,corp_name,filing_date,'
            'cnty_prin_ofc,sop_name,sop_addr1,sop_city,sop_state,sop_zip5,'
            'filer_name'
        ),
        '$limit': 2000,
        '$order': 'filing_date DESC',
    }

    logger.info(
        f'[ny_business_filings] Querying: counties={counties}, days={days}'
    )

    try:
        resp = requests.get(SODA_URL, params=params, headers=_headers(), timeout=60)
        if resp.status_code != 200:
            logger.error(f'[ny_business_filings] SODA API returned {resp.status_code}')
            stats['errors'] += 1
            return stats
        items = resp.json()
    except Exception as e:
        logger.error(f'[ny_business_filings] SODA API error: {e}')
        stats['errors'] += 1
        return stats

    logger.info(f'[ny_business_filings] Fetched {len(items)} filings')
    stats['items_scraped'] = len(items)

    # Filter for new business filings only
    printed = 0
    for item in items:
        filing_type = item.get('filing_type', '')
        if not _is_new_business_filing(filing_type):
            continue

        name = item.get('corp_name', '').strip()
        if not name:
            continue

        filing_date_str = item.get('filing_date', '')
        filing_date = _parse_date(filing_date_str)
        entity_type = _normalize_entity_type(item.get('entity_type', ''))
        county_name = item.get('cnty_prin_ofc', '').strip()
        display_county = COUNTY_DISPLAY.get(county_name, county_name)

        # Service of process address = registered address
        sop_name = item.get('sop_name', '').strip()
        sop_addr = item.get('sop_addr1', '').strip()
        sop_city = item.get('sop_city', '').strip()
        sop_state = item.get('sop_state', '').strip()
        sop_zip = item.get('sop_zip5', '').strip()
        full_address = ', '.join(filter(None, [sop_addr, sop_city, sop_state, sop_zip]))

        filer = item.get('filer_name', '').strip()
        dos_id = item.get('dos_id', '')

        services = _detect_services_from_name(name)

        content_parts = [
            f'New Business Filing: {name}',
            f'Entity Type: {entity_type}',
            f'Filing Type: {filing_type}',
            f'County: {display_county}, NY',
        ]
        if filing_date:
            content_parts.append(f'Filed: {filing_date.strftime("%m/%d/%Y")}')
        if full_address:
            content_parts.append(f'Registered Address: {full_address}')
        if sop_name:
            content_parts.append(f'Registered Agent: {sop_name}')
        content_parts.append(f'Services likely needed: {", ".join(services[:7])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 5:
                print(f'\n  [{display_county}] {name}')
                print(f'    Type: {entity_type}  Filing: {filing_type}')
                print(f'    Address: {full_address or "(none)"}')
                if filing_date:
                    print(f'    Date: {filing_date.strftime("%Y-%m-%d")}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=SODA_URL,
                content=content,
                author='',
                posted_at=filing_date,
                raw_data={
                    'data_source': 'ny_dos_soda',
                    'dos_id': dos_id,
                    'business_name': name,
                    'entity_type': entity_type,
                    'filing_type': filing_type,
                    'county': county_name,
                    'address': full_address,
                    'registered_agent': sop_name,
                    'filer': filer,
                    'services_mapped': services,
                },
                state='NY',
                region=display_county,
                source_group='public_records',
                source_type='business_filings',
                contact_name=sop_name or filer,
                contact_business=name,
                contact_address=full_address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[ny_business_filings] Error processing {name}: {e}')
            stats['errors'] += 1

    logger.info(f'NY business filings monitor complete: {stats}')
    return stats
