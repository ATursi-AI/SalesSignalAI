"""
NY property sales monitor for SalesSignal AI.

Uses the NYC ACRIS (Automated City Register Information System) via three
SODA API endpoints on NYC Open Data:

  1. Master (bnx9-e6tj) — recorded documents with amounts/dates
  2. Legals (8h5j-fqxa) — property addresses (block/lot/borough)
  3. Parties (636b-3b5g) — buyer names (party_type=2)

Query flow:
  a) Master: doc_type='DEED', recorded_datetime >= N days, document_amt > 0
  b) Join legals for address
  c) Join parties for buyer (party_type=2)

Borough codes: 1=Manhattan, 2=Bronx, 3=Brooklyn, 4=Queens, 5=Staten Island

Every property sale generates leads for:
  Moving, House Cleaning, Locksmith, Painter, Landscaping, Insurance,
  Mortgage Broker, Handyman, Pest Control
"""
import logging
import re
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NYC Open Data SODA endpoints
# -------------------------------------------------------------------
SODA_BASE = 'https://data.cityofnewyork.us/resource'

DATASETS = {
    'master': 'bnx9-e6tj',
    'legals': '8h5j-fqxa',
    'parties': '636b-3b5g',
}

# Deed-related doc_type codes in ACRIS master
DEED_DOC_TYPES = ['DEED', 'DEEDO']

BOROUGH_NAMES = {
    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
    '4': 'Queens', '5': 'Staten Island',
}
BOROUGH_FILTER = {
    'manhattan': '1', 'bronx': '2', 'brooklyn': '3',
    'queens': '4', 'staten_island': '5', 'staten island': '5',
}

SALE_SERVICES = [
    'Moving', 'House Cleaning', 'Locksmith', 'Painter',
    'Landscaping', 'Insurance', 'Mortgage Broker', 'Handyman',
    'Pest Control', 'HVAC Maintenance', 'Carpet Cleaning',
    'Window Cleaning', 'Gutter Cleaning',
]

PRICE_TIERS = {
    'luxury': 1_000_000,
    'premium': 500_000,
    'standard': 200_000,
}


def _soda_url(key):
    return f'{SODA_BASE}/{DATASETS[key]}.json'


def _headers():
    h = {}
    token = getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    if token:
        h['X-App-Token'] = token
    return h


def _format_currency(value):
    if not value:
        return ''
    try:
        amount = float(re.sub(r'[^\d.]', '', str(value)))
        if amount < 100:
            return ''
        return f'${amount:,.0f}'
    except (ValueError, TypeError):
        return ''


def _price_tier(amount):
    try:
        amount = float(re.sub(r'[^\d.]', '', str(amount)))
    except (ValueError, TypeError):
        return 'standard'
    if amount >= PRICE_TIERS['luxury']:
        return 'luxury'
    if amount >= PRICE_TIERS['premium']:
        return 'premium'
    if amount >= PRICE_TIERS['standard']:
        return 'standard'
    return 'budget'


def _parse_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _fetch_master_records(days, borough=None):
    """Fetch recent deed recordings from the ACRIS master dataset."""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    url = _soda_url('master')

    codes_str = ','.join(f"'{c}'" for c in DEED_DOC_TYPES)
    where_parts = [
        f"recorded_datetime >= '{since}'",
        "document_amt > 0",
        f"doc_type in({codes_str})",
    ]
    if borough:
        boro_code = BOROUGH_FILTER.get(borough.lower(), borough)
        where_parts.append(f"recorded_borough='{boro_code}'")

    params = {
        '$where': ' AND '.join(where_parts),
        '$select': 'document_id,recorded_datetime,document_amt,doc_type,recorded_borough,crfn',
        '$limit': 2000,
        '$order': 'recorded_datetime DESC',
    }

    try:
        resp = requests.get(url, params=params, headers=_headers(), timeout=60)
        if resp.status_code != 200:
            logger.warning(f'[ny_property_sales] Master API returned {resp.status_code}')
            return []
        items = resp.json()
        logger.info(f'[ny_property_sales] Fetched {len(items)} master records')
        return items
    except Exception as e:
        logger.error(f'[ny_property_sales] Error fetching master records: {e}')
        return []


def _fetch_legals(document_ids):
    """Fetch property addresses from the legals dataset for given document IDs."""
    if not document_ids:
        return {}

    legals_map = {}
    # Batch in groups of 50
    for i in range(0, len(document_ids), 50):
        batch = document_ids[i:i + 50]
        ids_str = ','.join(f"'{d}'" for d in batch)
        url = _soda_url('legals')
        params = {
            '$where': f"document_id in({ids_str})",
            '$select': 'document_id,borough,block,lot,street_number,street_name',
            '$limit': 5000,
        }
        try:
            resp = requests.get(url, params=params, headers=_headers(), timeout=30)
            if resp.status_code != 200:
                continue
            for item in resp.json():
                doc_id = item.get('document_id', '')
                if doc_id and doc_id not in legals_map:
                    street_num = item.get('street_number', '').strip()
                    street_name = item.get('street_name', '').strip()
                    addr = f'{street_num} {street_name}'.strip()
                    if addr:
                        legals_map[doc_id] = {
                            'address': addr,
                            'block': item.get('block', ''),
                            'lot': item.get('lot', ''),
                        }
        except Exception as e:
            logger.warning(f'[ny_property_sales] Error fetching legals batch: {e}')

    logger.info(f'[ny_property_sales] Fetched addresses for {len(legals_map)} documents')
    return legals_map


def _fetch_parties(document_ids):
    """Fetch buyer names from the parties dataset (party_type=2 = buyer/grantee)."""
    if not document_ids:
        return {}

    parties_map = {}
    for i in range(0, len(document_ids), 50):
        batch = document_ids[i:i + 50]
        ids_str = ','.join(f"'{d}'" for d in batch)
        url = _soda_url('parties')
        params = {
            '$where': f"document_id in({ids_str}) AND party_type='2'",
            '$select': 'document_id,name',
            '$limit': 5000,
        }
        try:
            resp = requests.get(url, params=params, headers=_headers(), timeout=30)
            if resp.status_code != 200:
                continue
            for item in resp.json():
                doc_id = item.get('document_id', '')
                name = item.get('name', '').strip()
                if doc_id and name and doc_id not in parties_map:
                    parties_map[doc_id] = name
        except Exception as e:
            logger.warning(f'[ny_property_sales] Error fetching parties batch: {e}')

    logger.info(f'[ny_property_sales] Fetched buyer names for {len(parties_map)} documents')
    return parties_map


def monitor_ny_property_sales(days=30, borough=None, dry_run=False):
    """
    Monitor NYC property sales via ACRIS SODA API.

    Queries three ACRIS datasets: master for recent deed recordings,
    legals for addresses, parties for buyer names.

    Args:
        days: how many days back to search (default: 30)
        borough: filter by borough name (manhattan/bronx/brooklyn/queens)
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

    # Step 1: Fetch master records (deeds only)
    master_records = _fetch_master_records(days, borough=borough)
    if not master_records:
        logger.info('[ny_property_sales] No master records found')
        return stats

    stats['items_scraped'] = len(master_records)
    doc_ids = [r.get('document_id', '') for r in master_records if r.get('document_id')]

    # Step 2: Fetch addresses and buyer names
    legals_map = _fetch_legals(doc_ids)
    parties_map = _fetch_parties(doc_ids)

    # Step 3: Process each sale
    printed = 0
    for record in master_records:
        doc_id = record.get('document_id', '')
        if not doc_id:
            continue

        legal = legals_map.get(doc_id, {})
        address = legal.get('address', '')
        if not address:
            continue

        buyer = parties_map.get(doc_id, '')
        boro_code = str(record.get('recorded_borough', ''))
        boro_name = BOROUGH_NAMES.get(boro_code, boro_code)
        sale_price = record.get('document_amt', '')
        sale_date_str = record.get('recorded_datetime', '')
        sale_date = _parse_date(sale_date_str)
        price_display = _format_currency(sale_price)
        tier = _price_tier(sale_price)

        full_address = f'{address}, {boro_name}, NY'

        content_parts = [
            f'Property Sold: {full_address}',
            f'Borough: {boro_name}',
        ]
        if price_display:
            content_parts.append(f'Sale Price: {price_display}')
        if sale_date:
            days_ago = (timezone.now() - sale_date).days
            content_parts.append(f'Closed: {days_ago} days ago')
        if buyer:
            content_parts.append(f'Buyer: {buyer}')
        content_parts.append(f'Price Tier: {tier.title()}')
        content_parts.append(f'New homeowner needs: {", ".join(SALE_SERVICES[:7])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 5:
                print(f'\n  [{boro_name}] {full_address}')
                print(f'    Price: {price_display or "undisclosed"}  Buyer: {buyer or "unknown"}')
                if sale_date:
                    print(f'    Recorded: {sale_date.strftime("%Y-%m-%d")}  Tier: {tier}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=_soda_url('master'),
                content=content,
                author=buyer,
                posted_at=sale_date,
                raw_data={
                    'data_source': 'acris',
                    'document_id': doc_id,
                    'address': full_address,
                    'borough': boro_name,
                    'sale_price': str(sale_price),
                    'buyer': buyer,
                    'price_tier': tier,
                    'block': legal.get('block', ''),
                    'lot': legal.get('lot', ''),
                },
                state='NY',
                region=boro_name,
                source_group='public_records',
                source_type='property_sales',
                contact_name=buyer,
                contact_address=full_address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[ny_property_sales] Error processing sale: {e}')
            stats['errors'] += 1

    logger.info(f'NY property sales monitor complete: {stats}')
    return stats
