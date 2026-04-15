"""
NYC DCWP License Applications monitor for SalesSignal AI.

Uses NYC Open Data SODA API to query the Department of Consumer and
Worker Protection (DCWP) License Applications database:

  Dataset: ptev-4hud (https://data.cityofnewyork.us/resource/ptev-4hud.json)

Monitors new license applications — businesses in the process of opening.
These are HOTTER leads than issued licenses because they're actively
setting up and need services immediately.

Fields: business_name, contact_phone, business_category, application_type,
        license_type, status, submission_date, address fields
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
SODA_URL = 'https://data.cityofnewyork.us/resource/ptev-4hud.json'

# Target categories (same as licensed businesses monitor)
TARGET_CATEGORIES = {
    'Home Improvement Contractor',
    'Locksmith',
    'Garage & Parking Lot',
    'Hotel',
    'Car Wash',
    'Electronic & Home Appliance Service Dealer',
    'Tow Truck Company',
    'Third Party Food Delivery Service',
}

# Services new businesses need
CATEGORY_SERVICES = {
    'Home Improvement Contractor': ['Insurance', 'Accountant', 'Web Design', 'Signage', 'Vehicle Wrap'],
    'Locksmith': ['Insurance', 'Web Design', 'Signage', 'Vehicle Wrap'],
    'Garage & Parking Lot': ['Commercial Cleaning', 'Security', 'Signage', 'Paving', 'HVAC'],
    'Hotel': ['Commercial Cleaning', 'Pest Control', 'HVAC', 'Landscaping', 'Security'],
    'Car Wash': ['Insurance', 'Signage', 'Plumber', 'HVAC', 'Equipment Repair'],
    'Electronic & Home Appliance Service Dealer': ['Insurance', 'Web Design', 'Signage'],
    'Tow Truck Company': ['Insurance', 'Vehicle Wrap', 'Web Design'],
    'Third Party Food Delivery Service': ['Insurance', 'Accountant'],
}

DEFAULT_SERVICES = ['Insurance', 'Accountant', 'Web Design', 'Commercial Cleaning']

BOROUGH_DISPLAY = {
    'Manhattan': 'Manhattan', 'Bronx': 'Bronx',
    'Brooklyn': 'Brooklyn', 'Queens': 'Queens',
    'Staten Island': 'Staten Island',
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


def monitor_nyc_license_applications(days=14, category=None, borough=None,
                                     dry_run=False):
    """
    Monitor NYC DCWP license applications via SODA API.

    New license applications = businesses actively opening.
    They need services NOW (insurance, cleaning, signage, etc.).

    Args:
        days: lookback period in days (default: 14)
        category: filter by business_category (optional)
        borough: filter by borough (optional)
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

    where_parts = [
        f"submission_date >= '{since}'",
    ]

    if category:
        where_parts.append(f"business_category = '{category}'")
    else:
        cats = "','".join(TARGET_CATEGORIES)
        where_parts.append(f"business_category in('{cats}')")

    if borough:
        where_parts.append(f"borough = '{borough}'")

    params = {
        '$where': ' AND '.join(where_parts),
        '$select': (
            'application_id,business_name,business_category,'
            'application_type,license_type,status,contact_phone,'
            'submission_date,building_number,street,city,state,zip,'
            'borough,latitude,longitude'
        ),
        '$limit': 2000,
        '$order': 'submission_date DESC',
    }

    logger.info(f'[nyc_lic_apps] Querying: days={days}, category={category or "all"}')

    try:
        resp = requests.get(SODA_URL, params=params, headers=_headers(), timeout=60)
        if resp.status_code != 200:
            logger.error(f'[nyc_lic_apps] SODA API returned {resp.status_code}')
            stats['errors'] += 1
            return stats
        items = resp.json()
    except Exception as e:
        logger.error(f'[nyc_lic_apps] SODA API error: {e}')
        stats['errors'] += 1
        return stats

    if not isinstance(items, list):
        logger.error('[nyc_lic_apps] Unexpected API response format')
        stats['errors'] += 1
        return stats

    logger.info(f'[nyc_lic_apps] Fetched {len(items)} applications')
    stats['items_scraped'] = len(items)

    printed = 0
    for item in items:
        biz_name = (item.get('business_name') or '').strip()
        if not biz_name:
            continue

        biz_category = (item.get('business_category') or '').strip()
        phone = (item.get('contact_phone') or '').strip()
        app_type = (item.get('application_type') or '').strip()
        app_status = (item.get('status') or '').strip()
        submission_date_str = item.get('submission_date', '')
        app_id = (item.get('application_id') or '').strip()

        building = (item.get('building_number') or '').strip()
        street = (item.get('street') or '').strip()
        city = (item.get('city') or '').strip()
        state = (item.get('state') or 'NY').strip()
        zipcode = (item.get('zip') or '').strip()
        boro = (item.get('borough') or '').strip()

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
        services = CATEGORY_SERVICES.get(biz_category, DEFAULT_SERVICES)
        submission_date = _parse_date(submission_date_str)

        content_parts = [
            f'NEW LICENSE APPLICATION: {biz_name}',
            f'Category: {biz_category}',
            f'Application Type: {app_type}',
            f'Status: {app_status}',
            f'Address: {full_address}',
        ]
        if boro_display:
            content_parts.append(f'Borough: {boro_display}')
        if phone:
            content_parts.append(f'Phone: {phone}')
        if submission_date:
            days_ago = (timezone.now() - submission_date).days
            content_parts.append(f'Applied: {days_ago} days ago')
        content_parts.append(f'New business likely needs: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 5:
                print(f'\n  [APPLICATION] {biz_name}')
                print(f'    Category: {biz_category}  Type: {app_type}  Status: {app_status}')
                print(f'    Phone: {phone or "(none)"}')
                print(f'    Address: {full_address}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=SODA_URL,
                content=content,
                author='',
                posted_at=submission_date,
                raw_data={
                    'data_source': 'nyc_dcwp_license_applications',
                    'application_id': app_id,
                    'business_name': biz_name,
                    'business_category': biz_category,
                    'application_type': app_type,
                    'status': app_status,
                    'phone': phone,
                    'address': full_address,
                    'borough': boro_display,
                    'services_mapped': services,
                },
                state='NY',
                region=boro_display,
                source_group='public_records',
                source_type='business_filings',
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
            logger.error(f'[nyc_lic_apps] Error processing {biz_name}: {e}')
            stats['errors'] += 1

    logger.info(f'NYC License Applications monitor complete: {stats}')
    return stats
