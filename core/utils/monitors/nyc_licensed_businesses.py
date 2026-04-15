"""
NYC DCWP Licensed Businesses monitor for SalesSignal AI.

Uses NYC Open Data SODA API to query the Department of Consumer and
Worker Protection (DCWP) Issued Licenses database:

  Dataset: w7w3-xahh (https://data.cityofnewyork.us/resource/w7w3-xahh.json)

50K+ legally operating businesses in NYC with phone numbers.
Key categories for SalesSignalAI prospecting:
  - Home Improvement Contractor (18K+)
  - Locksmith (2.9K)
  - Garage & Parking Lot (2.6K)
  - Hotel (787)
  - Car Wash (267)
  - Electronics Store (4.1K)
  - Tow Truck Company (373)

Two lead types:
  1. EXPIRED LICENSES — businesses with recently expired licenses need
     to renew or close. Orphaned customer signal.
  2. NEW LICENSES — recently issued licenses = new businesses opening,
     need services (insurance, cleaning, signage, HVAC, etc.)

Also useful for SalesSignalAI outreach campaigns targeting trade businesses.
"""
import logging
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NYC Open Data SODA endpoint
# -------------------------------------------------------------------
SODA_URL = 'https://data.cityofnewyork.us/resource/w7w3-xahh.json'

# Categories that produce leads for our customers
LEAD_CATEGORIES = {
    'Home Improvement Contractor',
    'Locksmith',
    'Garage & Parking Lot',
    'Hotel',
    'Car Wash',
    'Electronic & Home Appliance Service Dealer',
    'Tow Truck Company',
    'Third Party Food Delivery Service',
}

# Categories useful for SalesSignalAI outreach (potential customers)
PROSPECT_CATEGORIES = {
    'Home Improvement Contractor',
    'Locksmith',
    'Tow Truck Company',
    'Car Wash',
    'Electronic & Home Appliance Service Dealer',
}

# Borough display names
BOROUGH_DISPLAY = {
    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
    '4': 'Queens', '5': 'Staten Island',
    'Manhattan': 'Manhattan', 'Bronx': 'Bronx',
    'Brooklyn': 'Brooklyn', 'Queens': 'Queens',
    'Staten Island': 'Staten Island',
}

# License category -> services the business likely needs
NEW_LICENSE_SERVICES = {
    'Home Improvement Contractor': ['Insurance', 'Accountant', 'Web Design', 'Signage', 'Vehicle Wrap'],
    'Locksmith': ['Insurance', 'Web Design', 'Signage', 'Vehicle Wrap'],
    'Garage & Parking Lot': ['Commercial Cleaning', 'Security', 'Signage', 'Paving', 'HVAC'],
    'Hotel': ['Commercial Cleaning', 'Pest Control', 'HVAC', 'Landscaping', 'Security', 'Laundry'],
    'Car Wash': ['Insurance', 'Signage', 'Plumber', 'HVAC', 'Equipment Repair'],
    'Electronic & Home Appliance Service Dealer': ['Insurance', 'Web Design', 'Signage'],
    'Tow Truck Company': ['Insurance', 'Vehicle Wrap', 'Web Design'],
    'Third Party Food Delivery Service': ['Insurance', 'Accountant'],
}

DEFAULT_SERVICES = ['Insurance', 'Accountant', 'Web Design', 'Commercial Cleaning']

# Expired license -> services their orphaned customers need
EXPIRED_LICENSE_SERVICE_MAP = {
    'Home Improvement Contractor': ['General Contractor', 'Plumber', 'Electrician', 'HVAC', 'Painter'],
    'Locksmith': ['Locksmith'],
    'Garage & Parking Lot': ['Parking', 'Auto Repair'],
    'Car Wash': ['Car Wash', 'Auto Detailing'],
    'Electronic & Home Appliance Service Dealer': ['Appliance Repair'],
    'Tow Truck Company': ['Towing'],
}


def _headers():
    h = {}
    token = getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    if token:
        h['X-App-Token'] = token
    return h


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


def monitor_nyc_licensed_businesses(mode='expired', days=30,
                                    category=None, borough=None,
                                    dry_run=False):
    """
    Monitor NYC DCWP licensed businesses via SODA API.

    Args:
        mode: 'expired' for recently expired licenses (orphaned customers),
              'new' for recently issued licenses (new businesses needing services)
        days: lookback period in days (default: 30)
        category: filter by business_category (optional)
        borough: filter by address_borough (optional)
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

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

    # Build SODA query
    if mode == 'expired':
        # Recently expired licenses
        where_parts = [
            f"lic_expir_dd >= '{since}'",
            f"lic_expir_dd < '{datetime.now().strftime('%Y-%m-%dT00:00:00')}'",
        ]
    else:
        # Recently issued new licenses
        where_parts = [
            f"license_creation_date >= '{since}'",
        ]

    if category:
        where_parts.append(f"business_category = '{category}'")
    else:
        # Filter to our target categories
        cats = "','".join(LEAD_CATEGORIES)
        where_parts.append(f"business_category in('{cats}')")

    if borough:
        where_parts.append(f"address_borough = '{borough}'")

    params = {
        '$where': ' AND '.join(where_parts),
        '$select': (
            'business_name,business_category,license_type,license_status,'
            'license_creation_date,lic_expir_dd,contact_phone,'
            'address_building,address_street_name,address_city,'
            'address_state,address_zip,address_borough,'
            'latitude,longitude'
        ),
        '$limit': 2000,
        '$order': 'lic_expir_dd DESC' if mode == 'expired' else 'license_creation_date DESC',
    }

    logger.info(f'[nyc_licensed_biz] Querying: mode={mode}, days={days}, category={category or "all"}')

    try:
        resp = requests.get(SODA_URL, params=params, headers=_headers(), timeout=60)
        if resp.status_code != 200:
            logger.error(f'[nyc_licensed_biz] SODA API returned {resp.status_code}')
            stats['errors'] += 1
            return stats
        items = resp.json()
    except Exception as e:
        logger.error(f'[nyc_licensed_biz] SODA API error: {e}')
        stats['errors'] += 1
        return stats

    if not isinstance(items, list):
        logger.error('[nyc_licensed_biz] Unexpected API response format')
        stats['errors'] += 1
        return stats

    logger.info(f'[nyc_licensed_biz] Fetched {len(items)} records')
    stats['items_scraped'] = len(items)

    printed = 0
    for item in items:
        biz_name = (item.get('business_name') or '').strip()
        if not biz_name:
            continue

        biz_category = (item.get('business_category') or '').strip()
        phone = (item.get('contact_phone') or '').strip()
        license_status = (item.get('license_status') or '').strip()
        creation_date_str = item.get('license_creation_date', '')
        expiry_date_str = item.get('lic_expir_dd', '')

        building = (item.get('address_building') or '').strip()
        street = (item.get('address_street_name') or '').strip()
        city = (item.get('address_city') or '').strip()
        state = (item.get('address_state') or 'NY').strip()
        zipcode = (item.get('address_zip') or '').strip()
        boro = (item.get('address_borough') or '').strip()

        addr_parts = []
        if building and street:
            addr_parts.append(f'{building} {street}')
        elif street:
            addr_parts.append(street)
        if city:
            addr_parts.append(city)
        addr_parts.append(state)
        if zipcode:
            addr_parts.append(zipcode)
        full_address = ', '.join(addr_parts)

        boro_display = BOROUGH_DISPLAY.get(boro, boro)

        if mode == 'expired':
            expiry_date = _parse_date(expiry_date_str)
            days_expired = (timezone.now() - expiry_date).days if expiry_date else 0
            services = EXPIRED_LICENSE_SERVICE_MAP.get(biz_category, DEFAULT_SERVICES)

            content_parts = [
                f'EXPIRED LICENSE: {biz_name}',
                f'Category: {biz_category}',
                f'License Status: {license_status}',
                f'Address: {full_address}',
            ]
            if boro_display:
                content_parts.append(f'Borough: {boro_display}')
            if phone:
                content_parts.append(f'Phone: {phone}')
            if expiry_date:
                content_parts.append(f'Expired: {days_expired} days ago')
            content_parts.append(f'Customers of {biz_name} may need a new {biz_category.lower()} provider.')
            content_parts.append(f'Services needed: {", ".join(services[:6])}')

            urgency = 'warm' if days_expired <= 30 else 'new'
            source_type = 'license_expirations'
        else:
            creation_date = _parse_date(creation_date_str)
            services = NEW_LICENSE_SERVICES.get(biz_category, DEFAULT_SERVICES)

            content_parts = [
                f'NEW LICENSED BUSINESS: {biz_name}',
                f'Category: {biz_category}',
                f'License Status: {license_status}',
                f'Address: {full_address}',
            ]
            if boro_display:
                content_parts.append(f'Borough: {boro_display}')
            if phone:
                content_parts.append(f'Phone: {phone}')
            if creation_date:
                days_ago = (timezone.now() - creation_date).days
                content_parts.append(f'Licensed: {days_ago} days ago')
            content_parts.append(f'New business likely needs: {", ".join(services[:6])}')

            urgency = 'warm'
            source_type = 'business_filings'

        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 5:
                print(f'\n  [{mode.upper()}] {biz_name}')
                print(f'    Category: {biz_category}')
                print(f'    Phone: {phone or "(none)"}')
                print(f'    Address: {full_address}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            posted_at = _parse_date(expiry_date_str if mode == 'expired' else creation_date_str)
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=SODA_URL,
                content=content,
                author='',
                posted_at=posted_at,
                raw_data={
                    'data_source': 'nyc_dcwp_licenses',
                    'mode': mode,
                    'business_name': biz_name,
                    'business_category': biz_category,
                    'license_status': license_status,
                    'phone': phone,
                    'address': full_address,
                    'borough': boro_display,
                    'services_mapped': services,
                },
                state='NY',
                region=boro_display,
                source_group='public_records',
                source_type=source_type,
                contact_business=biz_name,
                contact_phone=phone,
                contact_address=full_address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[nyc_licensed_biz] Error processing {biz_name}: {e}')
            stats['errors'] += 1

    logger.info(f'NYC Licensed Businesses monitor ({mode}) complete: {stats}')
    return stats
