"""
NY property sales monitor for SalesSignal AI.

Monitors property sales from two sources:
  a) NYC ACRIS (Automated City Register Information System) via Open Data
     Dataset: bnx9-e6tj (Real Property Master)
     Filters for DEED document types to identify recent sales.
     Also uses usep-8jbt (NYC Rolling Sales Data) for cleaner records.

  b) Long Island county recorder offices (Nassau & Suffolk)
     Nassau: mynassauproperty.com / Open Data portals
     Suffolk: suffolkcountyny.gov/assessor

Every property sale generates leads for:
  Moving, House Cleaning, Locksmith, Painter, Landscaping, Insurance,
  Mortgage Broker, Handyman, Pest Control

New homeowners typically need services within 90 days of closing.
"""
import json
import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

# NYC Open Data SODA endpoints
SODA_BASE = 'https://data.cityofnewyork.us/resource'
DATASET_ACRIS = 'bnx9-e6tj'     # ACRIS Real Property Master
DATASET_SALES = 'usep-8jbt'     # NYC Rolling Sales Data (more accessible)

# ACRIS document types that indicate a sale
DEED_TYPES = [
    'DEED', 'DEEDO', 'DEED, RP', 'DEED, OTHER',
    'DEED, EXECUTOR', 'DEED, REFEREE', 'DEED, TAX',
]

# Borough codes
BOROUGH_MAP = {
    'manhattan': '1', 'bronx': '2', 'brooklyn': '3',
    'queens': '4', 'staten_island': '5', 'staten island': '5',
}
BOROUGH_NAMES = {
    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
    '4': 'Queens', '5': 'Staten Island',
}

# Long Island county assessor sources
LI_COUNTY_SOURCES = {
    'nassau': {
        'name': 'Nassau County',
        'assessor_url': 'https://www.mynassauproperty.com/',
        'search_url': 'https://www.mynassauproperty.com/Search/PropertySearch',
    },
    'suffolk': {
        'name': 'Suffolk County',
        'assessor_url': 'https://www.suffolkcountyny.gov/assessor',
        'search_url': 'https://www.suffolkcountyny.gov/assessor',
    },
}

# Services every new homeowner needs
SALE_SERVICES = [
    'Moving', 'House Cleaning', 'Locksmith', 'Painter',
    'Landscaping', 'Insurance', 'Mortgage Broker', 'Handyman',
    'Pest Control', 'HVAC Maintenance', 'Carpet Cleaning',
    'Window Cleaning', 'Gutter Cleaning',
]

# Property price tiers for lead scoring
PRICE_TIERS = {
    'luxury': 1_000_000,   # $1M+ = luxury (highest value leads)
    'premium': 500_000,    # $500K-$1M = premium
    'standard': 200_000,   # $200K-$500K = standard
}


class PropertySalesScraper(BaseScraper):
    MONITOR_NAME = 'ny_property_sales'
    DELAY_MIN = 1.0
    DELAY_MAX = 3.0
    MAX_REQUESTS_PER_RUN = 25
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 720  # 12 hours — sales data refreshes slowly
    RESPECT_ROBOTS = False  # API endpoints


def _soda_url(dataset_id):
    """Build the SODA API URL for a dataset."""
    return f'{SODA_BASE}/{dataset_id}.json'


def _parse_date(date_str):
    """Parse common date formats from property records."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y',
        '%m-%d-%Y', '%b %d, %Y', '%B %d, %Y',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00').split('T')[0])
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        pass

    return None


def _format_currency(value):
    """Format a numeric value as currency string."""
    if not value:
        return ''
    try:
        if isinstance(value, str):
            cleaned = re.sub(r'[^\d.]', '', value)
            amount = float(cleaned)
        else:
            amount = float(value)
        if amount < 100:
            return ''  # Filter out nominal sales ($0, $1, etc.)
        return f'${amount:,.0f}'
    except (ValueError, TypeError):
        return str(value) if value else ''


def _price_tier(amount):
    """Determine the price tier for lead scoring."""
    try:
        if isinstance(amount, str):
            amount = float(re.sub(r'[^\d.]', '', amount))
        amount = float(amount)
    except (ValueError, TypeError):
        return 'standard'

    if amount >= PRICE_TIERS['luxury']:
        return 'luxury'
    elif amount >= PRICE_TIERS['premium']:
        return 'premium'
    elif amount >= PRICE_TIERS['standard']:
        return 'standard'
    return 'budget'


def _post_lead_remote(ingest_url, api_key, lead_data):
    """POST a lead to a remote SalesSignal instance via the ingest API."""
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(
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
    except requests.RequestException as e:
        logger.error(f'[Remote] POST failed: {e}')
        return False, 0, {'error': str(e)}


def _safe_cell(cells, idx):
    """Safely extract text from a table cell by index."""
    if idx is None or idx < 0 or idx >= len(cells):
        return ''
    return cells[idx].get_text(strip=True)


# -------------------------------------------------------------------
# Source: NYC ACRIS + Rolling Sales via SODA API
# -------------------------------------------------------------------

def _monitor_nyc(scraper, days, dry_run, remote, stats, ingest_url, api_key):
    """NYC property sales via ACRIS / Rolling Sales SODA API."""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

    # Use NYC Rolling Sales dataset — cleaner data with addresses
    url = _soda_url(DATASET_SALES)
    params = {
        '$where': f"sale_date > '{since}' AND sale_price > 10000",
        '$limit': 1000,
        '$order': 'sale_date DESC',
    }

    # Add optional app token for higher rate limits
    app_token = getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    extra_headers = {}
    if app_token:
        extra_headers['X-App-Token'] = app_token

    try:
        resp = scraper.get(url, params=params, headers=extra_headers)
        if not resp or resp.status_code != 200:
            logger.warning(
                f'[ny_property_sales] NYC API returned '
                f'{resp.status_code if resp else "None"}'
            )
            stats['errors'] += 1
            return
        items = resp.json()
    except RateLimitHit:
        logger.warning('[ny_property_sales] Rate limited on NYC API')
        return
    except Exception as e:
        logger.error(f'[ny_property_sales] NYC API error: {e}')
        stats['errors'] += 1
        return

    stats['items_scraped'] += len(items)
    logger.info(f'[ny_property_sales] NYC: fetched {len(items)} sales')

    for item in items:
        if scraper.is_stopped:
            break
        try:
            # Extract address — try multiple field names
            address = str(item.get('address', '')).strip()
            if not address:
                house = item.get('house_number', '')
                street = item.get('street_name', '')
                address = f'{house} {street}'.strip()

            if not address:
                continue

            borough_code = str(item.get('borough', ''))
            borough_name = BOROUGH_NAMES.get(borough_code, borough_code)
            zip_code = item.get('zip_code', '')
            sale_price = item.get('sale_price', '')
            sale_date_str = item.get('sale_date', '')
            sale_date = _parse_date(sale_date_str)
            building_class = item.get('building_class_at_time_of_sale', '')

            # Determine property type from building class
            prop_type = 'residential'
            if building_class and building_class[0] not in 'ABCR':
                prop_type = 'commercial'

            price_display = _format_currency(sale_price)
            tier = _price_tier(sale_price)

            # Build lead content
            content_parts = [
                f'Property Sold: {address}',
                f'Borough: {borough_name}',
            ]
            if price_display:
                content_parts.append(f'Sale Price: {price_display}')
            else:
                content_parts.append('Sale Price: Undisclosed')
            content_parts.append(f'Type: {prop_type.title()}')
            if sale_date:
                days_ago = (timezone.now() - sale_date).days
                content_parts.append(f'Closed: {days_ago} days ago')
            content_parts.append(f'Price Tier: {tier.title()}')
            content_parts.append(
                f'New homeowner needs: {", ".join(SALE_SERVICES[:7])}'
            )

            content = '\n'.join(content_parts)
            source_url = _soda_url(DATASET_SALES)

            raw_data = {
                'source_type': 'property_sale',
                'data_source': 'nyc_rolling_sales',
                'address': address,
                'borough': borough_name,
                'zip_code': zip_code,
                'sale_price': str(sale_price),
                'sale_date': sale_date_str,
                'building_class': building_class,
                'property_type': prop_type,
                'price_tier': tier,
                'region': 'nyc',
                'services_mapped': SALE_SERVICES,
            }

            if dry_run:
                logger.info(
                    f'[DRY RUN] NYC sale: {address}, {borough_name} — '
                    f'{price_display or "undisclosed"}'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': '',
                    'confidence': 'high',
                    'detected_category': 'PROPERTY_SALE',
                    'raw_data': raw_data,
                }
                ok, status_code, body = _post_lead_remote(
                    ingest_url, api_key, payload,
                )
                if ok:
                    if status_code == 201:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                else:
                    stats['errors'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author='',
                posted_at=sale_date,
                raw_data=raw_data,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.detected_location = f'{address}, {borough_name}, NY'
                if zip_code:
                    lead.detected_zip = str(zip_code)
                lead.save(update_fields=['confidence', 'detected_location', 'detected_zip'])
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[ny_property_sales] NYC sale error: {e}')
            stats['errors'] += 1


# -------------------------------------------------------------------
# Source: Long Island county assessor websites
# -------------------------------------------------------------------

def _scrape_county_assessor(scraper, county_key, county_config):
    """
    Scrape a Long Island county assessor website for recent sales.
    Uses generic table parsing as a fallback.

    Returns list of sale dicts.
    """
    url = county_config.get('search_url') or county_config.get('assessor_url')
    if not url:
        return []

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(
            f'[ny_property_sales] Failed to fetch {county_config["name"]}: {e}'
        )
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[ny_property_sales] {county_config["name"]} returned '
            f'{resp.status_code if resp else "no response"}'
        )
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    sales = []

    # Strategy 1: Look for property sales tables
    tables = soup.find_all('table')
    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        if not headers:
            first_row = table.find('tr')
            if first_row:
                headers = [
                    td.get_text(strip=True).lower()
                    for td in first_row.find_all(['td', 'th'])
                ]

        # Check if this looks like a sales/property table
        sale_keywords = ['address', 'price', 'sale', 'date', 'buyer',
                         'parcel', 'grantee', 'consideration']
        if not any(any(kw in h for kw in sale_keywords) for h in headers):
            continue

        # Map header positions
        col_map = {}
        for i, h in enumerate(headers):
            if 'address' in h or 'location' in h or 'property' in h:
                col_map.setdefault('address', i)
            elif 'price' in h or 'amount' in h or 'consideration' in h:
                col_map.setdefault('sale_price', i)
            elif 'date' in h:
                col_map.setdefault('sale_date', i)
            elif 'buyer' in h or 'grantee' in h or 'purchaser' in h:
                col_map.setdefault('buyer', i)
            elif 'type' in h or 'class' in h:
                col_map.setdefault('property_type', i)

        rows = table.find_all('tr')[1:]
        for row in rows:
            cells = row.find_all('td')
            if not cells or len(cells) < 3:
                continue

            sale = {
                'address': _safe_cell(cells, col_map.get('address')),
                'sale_price': _safe_cell(cells, col_map.get('sale_price')),
                'sale_date': _safe_cell(cells, col_map.get('sale_date')),
                'buyer': _safe_cell(cells, col_map.get('buyer')),
                'property_type': _safe_cell(cells, col_map.get('property_type')),
            }
            if sale['address']:
                sales.append(sale)

    # Strategy 2: Try generic column-based extraction
    if not sales:
        for table in tables:
            rows = table.find_all('tr')
            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) < 3:
                    continue
                sale = {
                    'address': cells[0].get_text(strip=True) if len(cells) > 0 else '',
                    'sale_price': cells[1].get_text(strip=True) if len(cells) > 1 else '',
                    'sale_date': cells[2].get_text(strip=True) if len(cells) > 2 else '',
                    'buyer': cells[3].get_text(strip=True) if len(cells) > 3 else '',
                    'property_type': '',
                }
                if sale['address']:
                    sales.append(sale)

    return sales


def _monitor_li_county(scraper, county_key, days, dry_run, remote, stats,
                       ingest_url, api_key):
    """Long Island county property sales via assessor websites."""
    config = LI_COUNTY_SOURCES.get(county_key)
    if not config:
        logger.warning(f'[ny_property_sales] Unknown county: {county_key}')
        stats['errors'] += 1
        return

    county_name = config['name']
    logger.info(f'[ny_property_sales] Scraping {county_name}...')

    try:
        sales = _scrape_county_assessor(scraper, county_key, config)
    except RateLimitHit:
        logger.warning(f'[ny_property_sales] Rate limited on {county_name}')
        return
    except Exception as e:
        logger.error(f'[ny_property_sales] {county_name} error: {e}')
        stats['errors'] += 1
        return

    stats['items_scraped'] += len(sales)
    logger.info(f'[ny_property_sales] {county_name}: found {len(sales)} sales')

    cutoff = timezone.now() - timedelta(days=days)

    for sale in sales:
        if scraper.is_stopped:
            break
        try:
            address = sale.get('address', '')
            if not address:
                continue

            sale_price = sale.get('sale_price', '')
            sale_date = _parse_date(sale.get('sale_date', ''))
            buyer = sale.get('buyer', '')
            prop_type = sale.get('property_type', '')

            # Skip old sales
            if sale_date and sale_date < cutoff:
                continue

            price_display = _format_currency(sale_price)
            tier = _price_tier(sale_price)

            # Build lead content
            content_parts = [
                f'Property Sold: {address}',
                f'County: {county_name}, NY',
            ]
            if price_display:
                content_parts.append(f'Sale Price: {price_display}')
            if sale_date:
                days_ago = (timezone.now() - sale_date).days
                content_parts.append(f'Closed: {days_ago} days ago')
            if buyer:
                content_parts.append(f'Buyer: {buyer}')
            if prop_type:
                content_parts.append(f'Property Type: {prop_type}')
            content_parts.append(f'Price Tier: {tier.title()}')
            content_parts.append(
                f'New homeowner needs: {", ".join(SALE_SERVICES[:7])}'
            )

            content = '\n'.join(content_parts)
            source_url = config.get('assessor_url', '')

            raw_data = {
                'source_type': 'property_sale',
                'data_source': f'li_{county_key}',
                'address': address,
                'sale_price': sale_price,
                'sale_date': sale.get('sale_date', ''),
                'buyer': buyer,
                'property_type': prop_type,
                'county': county_name,
                'state': 'NY',
                'price_tier': tier,
                'region': county_key,
                'services_mapped': SALE_SERVICES,
            }

            if dry_run:
                logger.info(
                    f'[DRY RUN] {county_name} sale: {address} — '
                    f'{price_display or "no price"}'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': buyer,
                    'confidence': 'high',
                    'detected_category': 'PROPERTY_SALE',
                    'raw_data': raw_data,
                }
                ok, status_code, body = _post_lead_remote(
                    ingest_url, api_key, payload,
                )
                if ok:
                    if status_code == 201:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                else:
                    stats['errors'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author=buyer,
                posted_at=sale_date,
                raw_data=raw_data,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.save(update_fields=['confidence'])
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(
                f'[ny_property_sales] {county_name} sale error: {e}'
            )
            stats['errors'] += 1


# -------------------------------------------------------------------
# Main monitor function
# -------------------------------------------------------------------

def monitor_ny_property_sales(source='nyc', county=None, days=30,
                              dry_run=False, remote=False):
    """
    Monitor NY property sales from ACRIS and Long Island county records.

    Two data sources:
      - 'nyc': NYC Rolling Sales / ACRIS via Open Data SODA API
      - 'long_island': Nassau and Suffolk county assessor websites
      - 'all': both sources

    Args:
        source: 'nyc', 'long_island', or 'all'
        county: for Long Island, specific county ('nassau' or 'suffolk').
                None = all available counties.
        days: how many days back to search (default: 30)
        dry_run: if True, log matches without creating Lead records
        remote: if True, POST leads to REMOTE_INGEST_URL

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = PropertySalesScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed and not dry_run:
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

    stats = {
        'sources_checked': 0,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    valid_sources = {'nyc', 'long_island', 'all', 'nassau', 'suffolk'}
    if source not in valid_sources:
        logger.error(
            f'[ny_property_sales] Invalid source: {source}. '
            f'Valid: {", ".join(valid_sources)}'
        )
        return {
            'sources_checked': 0, 'items_scraped': 0, 'created': 0,
            'duplicates': 0, 'assigned': 0, 'errors': 1,
        }

    logger.info(
        f'[ny_property_sales] Starting — source={source}, '
        f'county={county or "all"}, days={days}'
    )

    # ------- NYC ACRIS / Rolling Sales -------
    if source in ('nyc', 'all'):
        stats['sources_checked'] += 1
        _monitor_nyc(
            scraper, days, dry_run, remote, stats, ingest_url, ingest_key,
        )

    # ------- Long Island counties -------
    if source in ('long_island', 'all', 'nassau', 'suffolk'):
        # Backward compat: source='nassau' or 'suffolk' directly
        if source in ('nassau', 'suffolk'):
            counties_to_scrape = [source]
        elif county:
            county_key = county.lower().strip()
            if county_key not in LI_COUNTY_SOURCES:
                logger.error(
                    f'[ny_property_sales] Unknown county: {county}. '
                    f'Valid: {", ".join(LI_COUNTY_SOURCES.keys())}'
                )
                stats['errors'] += 1
                counties_to_scrape = []
            else:
                counties_to_scrape = [county_key]
        else:
            counties_to_scrape = list(LI_COUNTY_SOURCES.keys())

        counties_to_scrape = scraper.shuffle(counties_to_scrape)

        for county_key in counties_to_scrape:
            if scraper.is_stopped:
                break
            stats['sources_checked'] += 1
            _monitor_li_county(
                scraper, county_key, days, dry_run, remote, stats,
                ingest_url, ingest_key,
            )

    logger.info(f'NY property sales monitor complete: {stats}')
    return stats
