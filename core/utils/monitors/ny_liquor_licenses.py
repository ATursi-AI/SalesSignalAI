"""
NY State Liquor Authority license monitor for SalesSignal AI.

Queries two datasets on data.ny.gov via the SODA API:
  1. Pending license applications  (f8i8-k2gm)  — 3.4K rows, 16 cols
  2. Recently issued active licenses (9s3h-dpkz) — 58.5K rows, 20 cols

New liquor licenses signal bar/restaurant openings that need
buildout, cleaning, HVAC, pest control, signage, security, etc.

Target counties: NYC boroughs + Long Island (Nassau, Suffolk).
"""
import json
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

SODA_BASE = 'https://data.ny.gov/resource'

DATASET_IDS = {
    'pending': 'f8i8-k2gm',   # Current SLA Pending Licenses
    'active':  '9s3h-dpkz',   # Current Active Licenses
}

# NYC borough county names + Long Island (title-case — matches SODA data)
TARGET_COUNTIES = [
    'New York', 'Bronx', 'Kings', 'Queens', 'Richmond',
    'Nassau', 'Suffolk',
]

COUNTY_ALIASES = {
    'nassau': 'Nassau', 'suffolk': 'Suffolk', 'queens': 'Queens',
    'kings': 'Kings', 'brooklyn': 'Kings', 'bronx': 'Bronx',
    'manhattan': 'New York', 'new york': 'New York',
    'richmond': 'Richmond', 'staten island': 'Richmond',
    'westchester': 'Westchester', 'rockland': 'Rockland',
    'orange': 'Orange', 'dutchess': 'Dutchess', 'putnam': 'Putnam',
}

COUNTY_DISPLAY = {
    'New York': 'Manhattan', 'Bronx': 'Bronx', 'Kings': 'Brooklyn',
    'Queens': 'Queens', 'Richmond': 'Staten Island',
    'Nassau': 'Nassau County', 'Suffolk': 'Suffolk County',
}

# License description keywords -> services likely needed
LICENSE_SERVICE_MAP = {
    'restaurant': ['commercial cleaning', 'pest control', 'HVAC', 'grease trap', 'signage', 'interior design', 'kitchen equipment'],
    'bar': ['commercial cleaning', 'pest control', 'security', 'signage', 'HVAC'],
    'hotel': ['commercial cleaning', 'HVAC', 'pest control', 'security', 'landscaping', 'laundry'],
    'club': ['security', 'commercial cleaning', 'HVAC', 'signage'],
    'tavern': ['commercial cleaning', 'pest control', 'security', 'signage'],
    'catering': ['commercial cleaning', 'pest control', 'equipment repair'],
    'grocery': ['pest control', 'commercial cleaning', 'refrigeration'],
    'liquor store': ['security', 'signage', 'commercial cleaning'],
    'wine': ['commercial cleaning', 'signage', 'security'],
    'beer': ['commercial cleaning', 'signage'],
}

DEFAULT_SERVICES = [
    'Commercial Cleaning', 'Pest Control', 'HVAC', 'Signage',
    'Security', 'Insurance', 'Accountant', 'Fire Protection',
]


class LiquorLicenseScraper(BaseScraper):
    MONITOR_NAME = 'ny_liquor_license'
    DELAY_MIN = 1.0
    DELAY_MAX = 3.0
    MAX_REQUESTS_PER_RUN = 20
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = False  # API endpoint


def _normalize_county(county_str):
    """Normalize county name to uppercase standard form."""
    if not county_str:
        return ''
    return COUNTY_ALIASES.get(county_str.strip().lower(),
                              county_str.strip().upper())


def _query_soda(scraper, dataset_id, where_clause, limit=1000, order=':id'):
    """Query a SODA endpoint on data.ny.gov and return list of dicts."""
    url = f'{SODA_BASE}/{dataset_id}.json'
    params = {
        '$where': where_clause,
        '$limit': limit,
        '$order': order,
    }
    token = getattr(settings, 'SODA_APP_TOKEN', '')
    headers = {}
    if token:
        headers['X-App-Token'] = token

    try:
        resp = scraper.get(url, params=params, headers=headers)
        if not resp:
            return []
        if resp.status_code in (429, 403):
            raise RateLimitHit(f'{resp.status_code} from SODA API')
        if resp.status_code != 200:
            logger.warning(
                f'[ny_liquor] SODA returned {resp.status_code}: '
                f'{resp.text[:200]}'
            )
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_liquor] SODA query error: {e}')
        return []


def _detect_services(description):
    """Map license description to likely services needed."""
    if not description:
        return DEFAULT_SERVICES
    desc_lower = description.lower()
    services = set()
    for key, svc_list in LICENSE_SERVICE_MAP.items():
        if key in desc_lower:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


def _days_ago_text(dt):
    """Return human-readable age text."""
    if not dt:
        return ''
    now = timezone.now()
    delta = (now - dt).days
    if delta == 0:
        return 'today'
    elif delta == 1:
        return 'yesterday'
    elif delta < 7:
        return f'{delta} days ago'
    elif delta < 30:
        weeks = delta // 7
        return f'{weeks} week{"s" if weeks > 1 else ""} ago'
    else:
        months = delta // 30
        return f'{months} month{"s" if months > 1 else ""} ago'


def _parse_soda_date(date_str):
    """Parse SODA floating timestamp to aware datetime."""
    if not date_str:
        return None
    formats = [
        '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str[:26], fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _post_lead_remote(ingest_url, api_key, lead_data):
    """POST a lead to a remote SalesSignal instance."""
    import requests as req
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = req.post(ingest_url, data=json.dumps(lead_data),
                        headers=headers, timeout=15)
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = {'raw': resp.text[:200]}
        return resp.status_code in (201, 409), resp.status_code, body
    except req.RequestException as e:
        logger.error(f'[Remote] POST failed: {e}')
        return False, 0, {'error': str(e)}


# -------------------------------------------------------------------
# Sub-monitor: Pending Applications (f8i8-k2gm)
# -------------------------------------------------------------------
# Fields: application_id, premises_county, type, class, description,
#         legalname, dba, actual_address_of_premises,
#         additional_address_information, city, state_name, zip_code,
#         received_date, status, georeference

def _monitor_pending(scraper, county, days, dry_run, remote, stats,
                     ingest_url, api_key):
    """Monitor pending liquor license applications."""
    since = (timezone.now() - timedelta(days=days)).strftime(
        '%Y-%m-%dT00:00:00.000'
    )

    county_norm = _normalize_county(county)
    where = (
        f"received_date >= '{since}' "
        f"AND premises_county = '{county_norm}'"
    )

    logger.info(f'[ny_liquor] Querying pending licenses: {where}')
    records = _query_soda(scraper, DATASET_IDS['pending'], where,
                          limit=1000, order='received_date DESC')
    stats['items_scraped'] += len(records)
    logger.info(f'[ny_liquor] Pending: fetched {len(records)} records')

    for rec in records:
        try:
            app_id = rec.get('application_id', '')
            dba = rec.get('dba', '').strip()
            legal_name = rec.get('legalname', '').strip()
            description = rec.get('description', '').strip()
            address = rec.get('actual_address_of_premises', '').strip()
            city = rec.get('city', '').strip()
            state = rec.get('state_name', 'NY').strip()
            zipcode = rec.get('zip_code', '').strip()
            received = rec.get('received_date', '')
            status_val = rec.get('status', '').strip()
            add_info = rec.get('additional_address_information', '').strip()
            lic_type = rec.get('type', '').strip()
            lic_class = rec.get('class', '').strip()

            display_name = dba or legal_name or 'Unknown'
            received_dt = _parse_soda_date(received)
            age_text = _days_ago_text(received_dt)
            services = _detect_services(description)
            county_display = COUNTY_DISPLAY.get(county_norm, county_norm)

            location_parts = [p for p in [address, city, state, zipcode] if p]
            location = ', '.join(location_parts)

            # Pending apps are HOT for restaurants/bars, WARM for others
            desc_lower = (description or '').lower()
            if any(k in desc_lower for k in ('restaurant', 'bar', 'tavern', 'club', 'hotel')):
                urgency_level = 'hot'
                urgency_score = 90
            else:
                urgency_level = 'warm'
                urgency_score = 70

            # Build content
            content_parts = [
                f'NEW LIQUOR LICENSE APPLICATION: {display_name}',
                f'License Type: {description} (Type {lic_type}, Class {lic_class})',
                f'Status: {status_val}',
                f'Location: {location}',
                f'County: {county_display}',
            ]
            if add_info:
                content_parts.append(f'Additional Info: {add_info}')
            if received_dt:
                content_parts.append(
                    f'Received: {received_dt.strftime("%m/%d/%Y")} ({age_text})'
                )
            if legal_name and dba and legal_name != dba:
                content_parts.append(f'Legal Entity: {legal_name}')
            content_parts.append(
                f'Services likely needed: {", ".join(services[:7])}'
            )
            content = '\n'.join(content_parts)

            source_url = (
                f'https://data.ny.gov/resource/{DATASET_IDS["pending"]}.json'
                f'?application_id={app_id}'
            )

            raw_data = {
                'source_type': 'ny_liquor_license_pending',
                'application_id': app_id,
                'dba': dba,
                'legal_name': legal_name,
                'license_description': description,
                'license_type': lic_type,
                'license_class': lic_class,
                'status': status_val,
                'address': address,
                'city': city,
                'state': state,
                'zip': zipcode,
                'county': county_norm,
                'received_date': received,
                'services_mapped': services,
                'urgency': urgency_level,
            }

            if dry_run:
                logger.info(
                    f'[DRY RUN] Pending: {display_name} | {description} | '
                    f'{location} | Received: {age_text} | '
                    f'Urgency: {urgency_level.upper()}'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': display_name,
                    'confidence': 'high',
                    'urgency': urgency_level,
                    'detected_category': 'LIQUOR_LICENSE_PENDING',
                    'raw_data': raw_data,
                }
                ok, sc, body = _post_lead_remote(ingest_url, api_key, payload)
                if ok:
                    stats['created' if sc == 201 else 'duplicates'] += 1
                else:
                    stats['errors'] += 1
                continue

            # Local mode
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author=display_name,
                posted_at=received_dt,
                raw_data=raw_data,
                state='NY',
                region=county_display,
                source_group='public_records',
                source_type='liquor_licenses',
                contact_name=legal_name or dba,
                contact_business=display_name,
                contact_address=location,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.urgency_level = urgency_level
                lead.urgency_score = urgency_score
                lead.detected_location = location
                if zipcode:
                    lead.detected_zip = zipcode
                lead.save(update_fields=[
                    'confidence', 'urgency_level', 'urgency_score',
                    'detected_location', 'detected_zip',
                ])
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[ny_liquor] Pending processing error: {e}')
            stats['errors'] += 1


# -------------------------------------------------------------------
# Sub-monitor: Active Licenses (9s3h-dpkz)
# -------------------------------------------------------------------
# Fields differ from pending: premisescounty, actualaddressofpremises,
#         premisesname, dba, premisescity, premisesstate, premiseszip,
#         lastissuedate, licensetypecode, licensetypedescription,
#         licenseclass, licensepermitid, serialnumber

def _monitor_active(scraper, county, days, dry_run, remote, stats,
                    ingest_url, api_key):
    """Monitor recently issued/renewed active liquor licenses."""
    since = (timezone.now() - timedelta(days=days)).strftime(
        '%Y-%m-%dT00:00:00.000'
    )

    county_norm = _normalize_county(county)
    where = (
        f"lastissuedate >= '{since}' "
        f"AND premisescounty = '{county_norm}'"
    )

    logger.info(f'[ny_liquor] Querying active licenses: {where}')
    records = _query_soda(scraper, DATASET_IDS['active'], where,
                          limit=1000, order='lastissuedate DESC')
    stats['items_scraped'] += len(records)
    logger.info(f'[ny_liquor] Active: fetched {len(records)} records')

    for rec in records:
        try:
            lic_id = (rec.get('licensepermitid', '')
                      or rec.get('legacyserialnumber', ''))
            legal_name = rec.get('legalname', '').strip()
            description = rec.get('description', '').strip()
            address = rec.get('actualaddressofpremises', '').strip()
            city = rec.get('city', '').strip()
            state = rec.get('statename', 'NY').strip()
            zipcode = rec.get('zipcode', '').strip()
            issue_date = rec.get('lastissuedate', '')
            lic_type = rec.get('type', '').strip()
            lic_class = rec.get('class', '').strip()
            dba = ''  # active dataset has no dba field

            display_name = legal_name or 'Unknown'
            issue_dt = _parse_soda_date(issue_date)
            age_text = _days_ago_text(issue_dt)
            services = _detect_services(description)
            county_display = COUNTY_DISPLAY.get(county_norm, county_norm)

            location_parts = [p for p in [address, city, state, zipcode] if p]
            location = ', '.join(location_parts)

            # Recently issued = warm (already got their license)
            urgency_level = 'warm'
            urgency_score = 65

            content_parts = [
                f'RECENTLY ISSUED LIQUOR LICENSE: {display_name}',
                f'License Type: {description} (Type {lic_type}, Class {lic_class})',
                f'Location: {location}',
                f'County: {county_display}',
            ]
            if issue_dt:
                content_parts.append(
                    f'Issued: {issue_dt.strftime("%m/%d/%Y")} ({age_text})'
                )
            if legal_name and dba and legal_name != dba:
                content_parts.append(f'Legal Entity: {legal_name}')
            content_parts.append(
                f'Services likely needed: {", ".join(services[:7])}'
            )
            content = '\n'.join(content_parts)

            source_url = (
                f'https://data.ny.gov/resource/{DATASET_IDS["active"]}.json'
                f'?licensepermitid={lic_id}'
            )

            raw_data = {
                'source_type': 'ny_liquor_license_active',
                'license_id': lic_id,
                'dba': dba,
                'legal_name': legal_name,
                'license_description': description,
                'license_type': lic_type,
                'license_class': lic_class,
                'address': address,
                'city': city,
                'state': state,
                'zip': zipcode,
                'county': county_norm,
                'issue_date': issue_date,
                'services_mapped': services,
                'urgency': urgency_level,
            }

            if dry_run:
                logger.info(
                    f'[DRY RUN] Active: {display_name} | {description} | '
                    f'{location} | Issued: {age_text}'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': display_name,
                    'confidence': 'medium',
                    'urgency': urgency_level,
                    'detected_category': 'LIQUOR_LICENSE_ACTIVE',
                    'raw_data': raw_data,
                }
                ok, sc, body = _post_lead_remote(ingest_url, api_key, payload)
                if ok:
                    stats['created' if sc == 201 else 'duplicates'] += 1
                else:
                    stats['errors'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author=display_name,
                posted_at=issue_dt,
                raw_data=raw_data,
                state='NY',
                region=county_display,
                source_group='public_records',
                source_type='liquor_licenses',
                contact_name=legal_name,
                contact_business=display_name,
                contact_address=location,
            )

            if lead and created:
                lead.confidence = 'medium'
                lead.urgency_level = urgency_level
                lead.urgency_score = urgency_score
                lead.detected_location = location
                if zipcode:
                    lead.detected_zip = zipcode
                lead.save(update_fields=[
                    'confidence', 'urgency_level', 'urgency_score',
                    'detected_location', 'detected_zip',
                ])
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[ny_liquor] Active processing error: {e}')
            stats['errors'] += 1


# -------------------------------------------------------------------
# Main monitor function
# -------------------------------------------------------------------

def monitor_ny_liquor_licenses(county='nassau', days=30, dry_run=False,
                               remote=False):
    """
    Monitor NY SLA for liquor license applications and recent issuances.

    Queries two SODA datasets on data.ny.gov:
      - Pending applications (f8i8-k2gm): new bars/restaurants about to open
      - Active licenses (9s3h-dpkz): recently issued/renewed licenses

    Args:
        county: county name or 'all' for all NYC + LI counties
        days: lookback period in days (default: 30)
        dry_run: if True, log matches without creating leads
        remote: if True, POST leads to REMOTE_INGEST_URL

    Returns:
        dict with sources_checked, items_scraped, created, duplicates,
        assigned, errors keys
    """
    scraper = LiquorLicenseScraper()

    # Check cooldown (skip for dry runs)
    if not dry_run:
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
                '[Remote] REMOTE_INGEST_URL and INGEST_API_KEY required'
            )
            return {
                'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 1,
            }

    # Determine counties
    if county.lower() == 'all':
        counties = TARGET_COUNTIES
    else:
        counties = [c.strip() for c in county.split(',')]

    stats = {
        'sources_checked': 0, 'items_scraped': 0, 'created': 0,
        'duplicates': 0, 'assigned': 0, 'errors': 0,
    }

    for county_name in counties:
        if scraper.is_stopped:
            break

        norm_county = _normalize_county(county_name)
        if norm_county not in TARGET_COUNTIES:
            logger.warning(f'[ny_liquor] Unknown county: {county_name}')
            continue

        logger.info(
            f'[ny_liquor] Starting {norm_county} county, days={days}'
        )

        # Query pending applications
        stats['sources_checked'] += 1
        _monitor_pending(scraper, county_name, days, dry_run, remote, stats,
                         ingest_url, ingest_key)

        if scraper.is_stopped:
            break

        # Query recently issued active licenses
        stats['sources_checked'] += 1
        _monitor_active(scraper, county_name, days, dry_run, remote, stats,
                        ingest_url, ingest_key)

    logger.info(f'NY Liquor License monitor complete: {stats}')
    return stats
