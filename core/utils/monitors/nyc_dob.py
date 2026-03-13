"""
NYC Department of Buildings monitor for SalesSignal AI.

Uses NYC Open Data SODA API to monitor three DOB datasets:
  1. Job/Permit Applications (ic3t-wcy2) — new building permits
  2. DOB Violations (3h2n-5cm9) — code violations requiring fixes
  3. Certificates of Occupancy (bs8b-p36w) — new tenants moving in

All three produce high-confidence leads from government data:
  - Permits: map job type to trade services needed
  - Violations: urgent leads (must be fixed), map to relevant trade
  - Certificates: new occupants need cleaning, HVAC, pest, security

SODA API pattern:
  https://data.cityofnewyork.us/resource/{DATASET_ID}.json
  ?$where=date_field > '{date}'&$limit=1000
"""
import json
import logging
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NYC Open Data SODA API configuration
# -------------------------------------------------------------------

SODA_BASE = 'https://data.cityofnewyork.us/resource'

DATASET_IDS = {
    'permits': 'ic3t-wcy2',
    'violations': '3h2n-5cm9',
    'certificates': 'bs8b-p36w',
}

# Borough codes used in DOB datasets
BOROUGH_MAP = {
    'manhattan': '1', 'bronx': '2', 'brooklyn': '3',
    'queens': '4', 'staten_island': '5', 'staten island': '5',
}
BOROUGH_NAMES = {
    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
    '4': 'Queens', '5': 'Staten Island',
}

# Permit job_type codes -> service categories
PERMIT_JOB_TYPE_MAP = {
    'a1': ['General Contractor', 'Plumber', 'Electrician', 'HVAC'],
    'a2': ['General Contractor', 'Plumber', 'Electrician'],
    'a3': ['General Contractor', 'Plumber', 'Electrician'],
    'nb': ['General Contractor', 'Plumber', 'Electrician', 'Roofing',
           'HVAC', 'Painter', 'Flooring', 'Landscaping', 'Fencing',
           'Concrete', 'Drywall', 'Insulation'],
    'dm': ['General Contractor', 'Hauling', 'Junk Removal'],
    'si': ['General Contractor', 'Sprinkler'],
    'sd': ['General Contractor', 'Sprinkler'],
    'fo': ['General Contractor'],
    'pl': ['Plumber'],
    'el': ['Electrician'],
    'mh': ['HVAC'],
    'ot': ['General Contractor'],
}

# Permit description keywords -> additional services
PERMIT_DESC_SERVICE_MAP = {
    'plumbing': ['Plumber'],
    'mechanical': ['HVAC'],
    'electrical': ['Electrician'],
    'general construction': ['General Contractor', 'Plumber', 'Electrician', 'HVAC'],
    'new building': ['General Contractor', 'Plumber', 'Electrician', 'Roofer',
                     'HVAC', 'Painter', 'Flooring', 'Landscaper'],
    'alteration': ['General Contractor', 'Plumber', 'Electrician', 'Painter'],
    'demolition': ['General Contractor', 'Hauling'],
    'bathroom': ['Plumber', 'Electrician', 'Tile', 'Painting'],
    'kitchen': ['Plumber', 'Electrician', 'Countertop', 'Cabinet'],
    'roof': ['Roofing'],
    'facade': ['General Contractor', 'Masonry'],
    'elevator': ['Elevator Service'],
    'sprinkler': ['Sprinkler', 'Fire Protection'],
    'boiler': ['HVAC', 'Plumber'],
    'gas': ['Plumber'],
    'fire escape': ['General Contractor', 'Fire Protection'],
    'fire alarm': ['Fire Protection', 'Electrician'],
    'fire suppression': ['Fire Protection'],
    'sidewalk': ['Concrete', 'Paving'],
    'pool': ['Pool', 'Fencing', 'Electrician'],
    'solar': ['Solar', 'Electrician'],
    'sign': ['Signage'],
    'standpipe': ['Fire Protection', 'Plumber'],
}

# Violation type -> service categories
VIOLATION_SERVICE_MAP = {
    'electrical': ['Electrician'],
    'plumbing': ['Plumber'],
    'elevator': ['Elevator Service'],
    'construction': ['General Contractor'],
    'boiler': ['HVAC', 'Plumber'],
    'fire': ['Fire Protection', 'Electrician'],
    'facade': ['Masonry', 'Waterproofing', 'General Contractor'],
    'structural': ['General Contractor', 'Engineer'],
    'gas': ['Plumber'],
    'scaffold': ['General Contractor'],
    'sidewalk': ['Concrete'],
    'zoning': ['Architect', 'Lawyer'],
    'general': ['General Contractor'],
}

# Certificate of Occupancy -> services for new tenants
CO_SERVICES = [
    'Commercial Cleaning', 'HVAC', 'Pest Control', 'Security',
    'Insurance', 'Locksmith', 'Painter', 'Electrician',
]


class NYCDOBScraper(BaseScraper):
    MONITOR_NAME = 'nyc_dob'
    DELAY_MIN = 1.0   # SODA API is lenient
    DELAY_MAX = 3.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = False  # This is an API, not a website


def _soda_url(dataset_id):
    """Build the SODA API URL for a dataset."""
    return f'{SODA_BASE}/{dataset_id}.json'


def _parse_soda_date(date_str):
    """Parse date from NYC Open Data SODA response."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y',
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


def _build_address(house_num, street, borough_name):
    """Build a displayable address from DOB fields."""
    parts = []
    if house_num:
        parts.append(str(house_num).strip())
    if street:
        parts.append(str(street).strip())
    addr = ' '.join(parts)
    if borough_name:
        addr = f'{addr}, {borough_name}' if addr else borough_name
    return f'{addr}, NY' if addr else 'NYC, NY'


def _detect_permit_services(job_type, description=''):
    """Map NYC DOB job type and description to service categories."""
    services = set()

    # Check job type code
    if job_type:
        jt = job_type.strip().lower()
        if jt in PERMIT_JOB_TYPE_MAP:
            services.update(PERMIT_JOB_TYPE_MAP[jt])

    # Check description keywords
    combined = f'{job_type} {description}'.lower()
    for key, svc_list in PERMIT_DESC_SERVICE_MAP.items():
        if key in combined:
            services.update(svc_list)

    return list(services) if services else ['General Contractor']


def _detect_violation_services(violation_type, violation_category=''):
    """Map violation type to service categories."""
    services = set()
    combined = f'{violation_type} {violation_category}'.lower()

    for key, svc_list in VIOLATION_SERVICE_MAP.items():
        if key in combined:
            services.update(svc_list)

    return list(services) if services else ['General Contractor']


def _query_soda(scraper, dataset_id, where_clause, limit=1000):
    """
    Query the NYC Open Data SODA API via the scraper session.

    Args:
        scraper: NYCDOBScraper instance (uses its get() for rate limiting)
        dataset_id: the dataset identifier (e.g. 'ic3t-wcy2')
        where_clause: SoQL $where filter
        limit: max rows to return

    Returns:
        list of dicts or empty list on failure
    """
    url = _soda_url(dataset_id)
    params = {
        '$where': where_clause,
        '$limit': limit,
        '$order': ':id',
    }

    # Add optional app token for higher rate limits
    app_token = getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token

    try:
        resp = scraper.get(url, params=params, headers=headers)
        if not resp or resp.status_code != 200:
            return []
        return resp.json()
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[nyc_dob] SODA API request failed: {e}')
        return []


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


# -------------------------------------------------------------------
# Sub-monitor: Permits
# -------------------------------------------------------------------

def _monitor_permits(scraper, borough, days, dry_run, remote, stats,
                     ingest_url, api_key):
    """Sub-monitor: NYC DOB permit applications (dataset ic3t-wcy2)."""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    where = f"pre__filing_date > '{since}'"

    if borough:
        boro_code = BOROUGH_MAP.get(borough.lower(), '')
        if boro_code:
            where += f" AND borough = '{boro_code}'"

    logger.info(f'[nyc_dob] Querying permits: {where}')

    try:
        items = _query_soda(scraper, DATASET_IDS['permits'], where)
    except RateLimitHit:
        logger.warning('[nyc_dob] Rate limited on permits query')
        return

    stats['items_scraped'] += len(items)
    logger.info(f'[nyc_dob] Permits: fetched {len(items)} filings')

    for item in items:
        if scraper.is_stopped:
            break
        try:
            job_num = item.get('job__', '')
            doc_num = item.get('doc__', '')
            borough_code = item.get('borough', '')
            house_num = item.get('house__', '')
            street = item.get('street_name', '')
            job_type = item.get('job_type', '')
            description = item.get('other_description', '') or item.get('job_status_descrp', '') or ''
            filing_date_str = item.get('pre__filing_date', '') or item.get('fully_permitted', '')
            owner_first = item.get('owner_s_first_name', '')
            owner_last = item.get('owner_s_last_name', '')

            borough_name = BOROUGH_NAMES.get(borough_code, borough_code)
            address = _build_address(house_num, street, borough_name)
            filing_date = _parse_soda_date(filing_date_str)
            owner_name = f'{owner_first} {owner_last}'.strip()
            services = _detect_permit_services(job_type, description)

            if not address or address == 'NYC, NY':
                continue

            content = (
                f'NYC Building Permit Filed: {job_type.upper() if job_type else "Permit"}\n'
                f'Address: {address}\n'
                f'Borough: {borough_name}\n'
                f'Description: {description[:300]}\n'
                f'Owner: {owner_name}\n'
                f'Job #: {job_num}\n'
                f'Services needed: {", ".join(services[:6])}'
            )

            raw_data = {
                'source_type': 'nyc_dob_permit',
                'job_number': job_num,
                'doc_number': doc_num,
                'borough': borough_name,
                'address': address,
                'job_type': job_type,
                'job_description': description[:500],
                'owner': owner_name,
                'filing_date': filing_date_str,
                'services_mapped': services,
            }

            source_url = _soda_url(DATASET_IDS['permits'])

            if dry_run:
                logger.info(
                    f'[DRY RUN] Permit: {address} — {job_type}'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': owner_name,
                    'confidence': 'high',
                    'detected_category': 'BUILDING_PERMIT',
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
                author=owner_name,
                posted_at=filing_date,
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
            logger.error(f'[nyc_dob] Permit processing error: {e}')
            stats['errors'] += 1


# -------------------------------------------------------------------
# Sub-monitor: Violations
# -------------------------------------------------------------------

def _monitor_violations(scraper, borough, days, dry_run, remote, stats,
                        ingest_url, api_key):
    """Sub-monitor: NYC DOB violations (dataset 3h2n-5cm9)."""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    where = f"issue_date > '{since}'"

    if borough:
        boro_code = BOROUGH_MAP.get(borough.lower(), '')
        if boro_code:
            where += f" AND boro = '{boro_code}'"

    logger.info(f'[nyc_dob] Querying violations: {where}')

    try:
        items = _query_soda(scraper, DATASET_IDS['violations'], where)
    except RateLimitHit:
        logger.warning('[nyc_dob] Rate limited on violations query')
        return

    stats['items_scraped'] += len(items)
    logger.info(f'[nyc_dob] Violations: fetched {len(items)} records')

    for item in items:
        if scraper.is_stopped:
            break
        try:
            bis_viol = item.get('isn_dob_bis_viol', '')
            v_type = item.get('violation_type', '')
            v_category = item.get('violation_category', '')
            violation_date_str = item.get('issue_date', '')
            description = item.get('description', '')
            house_num = item.get('house_number', '')
            street = item.get('street', '')
            boro_code = item.get('boro', '')

            borough_name = BOROUGH_NAMES.get(boro_code, 'NYC')
            address = _build_address(house_num, street, borough_name)
            violation_date = _parse_soda_date(violation_date_str)
            services = _detect_violation_services(v_type, v_category)

            if not address or address == 'NYC, NY':
                continue

            content = (
                f'NYC DOB VIOLATION: {v_type}\n'
                f'Category: {v_category}\n'
                f'Address: {address}\n'
                f'Description: {description[:300]}\n'
                f'URGENT: Violations must be corrected or fines increase.\n'
                f'Services needed: {", ".join(services[:6])}'
            )

            raw_data = {
                'source_type': 'nyc_dob_violation',
                'bis_viol': bis_viol,
                'violation_type': v_type,
                'violation_category': v_category,
                'address': address,
                'description': description[:500],
                'issue_date': violation_date_str,
                'services_mapped': services,
            }

            source_url = _soda_url(DATASET_IDS['violations'])

            if dry_run:
                logger.info(
                    f'[DRY RUN] Violation: {address} — {v_type}'
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
                    'urgency': 'hot',
                    'detected_category': 'DOB_VIOLATION',
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
                posted_at=violation_date,
                raw_data=raw_data,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.urgency_level = 'hot'
                lead.urgency_score = 90
                lead.save(update_fields=['confidence', 'urgency_level', 'urgency_score'])
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[nyc_dob] Violation processing error: {e}')
            stats['errors'] += 1


# -------------------------------------------------------------------
# Sub-monitor: Certificates of Occupancy
# -------------------------------------------------------------------

def _monitor_certificates(scraper, borough, days, dry_run, remote, stats,
                          ingest_url, api_key):
    """Sub-monitor: NYC certificates of occupancy (dataset bs8b-p36w)."""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    where = f"c_o_issue_date > '{since}'"

    if borough:
        boro_code = BOROUGH_MAP.get(borough.lower(), '')
        if boro_code:
            where += f" AND borough = '{boro_code}'"

    logger.info(f'[nyc_dob] Querying certificates of occupancy: {where}')

    try:
        items = _query_soda(scraper, DATASET_IDS['certificates'], where)
    except RateLimitHit:
        logger.warning('[nyc_dob] Rate limited on certificates query')
        return

    stats['items_scraped'] += len(items)
    logger.info(f'[nyc_dob] Certificates: fetched {len(items)} records')

    for item in items:
        if scraper.is_stopped:
            break
        try:
            job_num = item.get('job_number', '')
            borough_code = item.get('borough', '')
            house_num = item.get('house_number', '')
            street = item.get('street_name', '')
            co_date_str = item.get('c_o_issue_date', '')
            job_type = item.get('job_type', '')
            issue_type = item.get('issue_type', '')
            postcode = item.get('postcode', '')

            borough_name = BOROUGH_NAMES.get(borough_code, borough_code)
            address = _build_address(house_num, street, borough_name)
            co_date = _parse_soda_date(co_date_str)

            if not address or address == 'NYC, NY':
                continue

            content = (
                f'Certificate of Occupancy Issued\n'
                f'Address: {address}\n'
                f'Borough: {borough_name}\n'
                f'Job Type: {job_type}\n'
                f'Issue Type: {issue_type}\n'
                f'Job #: {job_num}\n'
                f'New tenant moving in — high-value lead window.\n'
                f'Services needed: {", ".join(CO_SERVICES[:6])}'
            )

            raw_data = {
                'source_type': 'nyc_dob_certificate',
                'job_number': job_num,
                'borough': borough_name,
                'address': address,
                'job_type': job_type,
                'issue_type': issue_type,
                'postcode': postcode,
                'c_o_issue_date': co_date_str,
                'services_mapped': CO_SERVICES,
            }

            source_url = _soda_url(DATASET_IDS['certificates'])

            if dry_run:
                logger.info(
                    f'[DRY RUN] CO: {address} — {building_type}'
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
                    'urgency': 'hot',
                    'detected_category': 'CERTIFICATE_OF_OCCUPANCY',
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
                posted_at=co_date,
                raw_data=raw_data,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.urgency_level = 'hot'
                lead.urgency_score = 85
                lead.save(update_fields=['confidence', 'urgency_level', 'urgency_score'])
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[nyc_dob] CO processing error: {e}')
            stats['errors'] += 1


# -------------------------------------------------------------------
# Main monitor function
# -------------------------------------------------------------------

def monitor_nyc_dob(monitor_type='permits', borough=None, days=7,
                    dry_run=False, remote=False):
    """
    Monitor NYC Department of Buildings via Open Data SODA API.

    Three sub-monitors available:
      - permits: new job/permit applications (ic3t-wcy2)
      - violations: DOB violations requiring fixes (3h2n-5cm9)
      - certificates: certificates of occupancy (bs8b-p36w)
      - all: run all three sub-monitors

    Args:
        monitor_type: 'permits', 'violations', 'certificates', or 'all'
        borough: manhattan, brooklyn, queens, bronx, staten_island (optional)
        days: how many days back to query (default: 7)
        dry_run: if True, log matches without creating Lead records
        remote: if True, POST leads to REMOTE_INGEST_URL

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = NYCDOBScraper()

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

    # Determine which sub-monitors to run
    valid_types = {'permits', 'violations', 'certificates', 'all'}
    if monitor_type not in valid_types:
        logger.error(
            f'[nyc_dob] Invalid monitor_type: {monitor_type}. '
            f'Valid: {", ".join(valid_types)}'
        )
        return {
            'sources_checked': 0, 'items_scraped': 0, 'created': 0,
            'duplicates': 0, 'assigned': 0, 'errors': 1,
        }

    type_label = monitor_type.replace('_', ' ').title()
    logger.info(
        f'[nyc_dob] Starting {type_label} monitor — '
        f'borough={borough or "all"}, days={days}'
    )

    dispatch = {
        'permits': _monitor_permits,
        'violations': _monitor_violations,
        'certificates': _monitor_certificates,
    }

    if monitor_type == 'all':
        monitors_to_run = ['permits', 'violations', 'certificates']
    else:
        monitors_to_run = [monitor_type]

    for mt in monitors_to_run:
        if scraper.is_stopped:
            break
        stats['sources_checked'] += 1
        handler = dispatch[mt]
        handler(
            scraper, borough, days, dry_run, remote, stats,
            ingest_url, ingest_key,
        )

    logger.info(f'NYC DOB {type_label} monitor complete: {stats}')
    return stats
