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
    'permits': 'ipu4-2q9a',      # DOB Permit Issuance (current, updated daily)
    'violations': '6bgk-3dad',    # DOB ECB Violations (current, updated daily)
    'certificates': 'bs8b-p36w',  # Certificates of Occupancy (calendar_date type)
}

# Borough codes used in DOB datasets
# Permits dataset uses text names; violations/certificates use numeric codes
BOROUGH_CODE_MAP = {
    'manhattan': '1', 'bronx': '2', 'brooklyn': '3',
    'queens': '4', 'staten_island': '5', 'staten island': '5',
}
BOROUGH_TEXT_MAP = {
    'manhattan': 'MANHATTAN', 'bronx': 'BRONX', 'brooklyn': 'BROOKLYN',
    'queens': 'QUEENS', 'staten_island': 'STATEN ISLAND', 'staten island': 'STATEN ISLAND',
}
BOROUGH_NAMES = {
    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
    '4': 'Queens', '5': 'Staten Island',
    'MANHATTAN': 'Manhattan', 'BRONX': 'Bronx', 'BROOKLYN': 'Brooklyn',
    'QUEENS': 'Queens', 'STATEN ISLAND': 'Staten Island',
    'Manhattan': 'Manhattan', 'Bronx': 'Bronx', 'Brooklyn': 'Brooklyn',
    'Queens': 'Queens', 'Staten Island': 'Staten Island',
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


def _days_ago_text(dt):
    """Human-readable 'X days ago' / 'today' / 'yesterday' from a datetime."""
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


def _query_soda(scraper, dataset_id, where_clause, limit=1000, order=':id'):
    """
    Query the NYC Open Data SODA API via the scraper session.

    Args:
        scraper: NYCDOBScraper instance (uses its get() for rate limiting)
        dataset_id: the dataset identifier (e.g. 'ipu4-2q9a')
        where_clause: SoQL $where filter
        limit: max rows to return
        order: SoQL $order clause (default ':id')

    Returns:
        list of dicts or empty list on failure
    """
    url = _soda_url(dataset_id)
    params = {
        '$where': where_clause,
        '$limit': limit,
        '$order': order,
    }

    # Add optional app token for higher rate limits
    app_token = getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token

    try:
        resp = scraper.get(url, params=params, headers=headers)
        if not resp:
            logger.warning('[nyc_dob] SODA response is None (blocked/skipped by BaseScraper)')
            return []
        if resp.status_code != 200:
            logger.warning(f'[nyc_dob] SODA returned status {resp.status_code}')
            return []
        data = resp.json()
        return data
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
    """Sub-monitor: NYC DOB permit issuance (dataset ipu4-2q9a).

    Dates in this dataset are text (MM/DD/YYYY). We fetch recent records
    ordered by dobrundate DESC and filter client-side by filing_date.
    """
    cutoff = timezone.now() - timedelta(days=days)
    # Dates in this dataset are text (MM/DD/YYYY) — can't filter server-side.
    # Fetch recent INITIAL filings and filter client-side by filing_date.
    where = "filing_status = 'INITIAL'"

    if borough:
        boro_text = BOROUGH_TEXT_MAP.get(borough.lower(), '')
        if boro_text:
            where += f" AND borough = '{boro_text}'"

    logger.info(f'[nyc_dob] Querying permits: {where}')

    try:
        items = _query_soda(scraper, DATASET_IDS['permits'], where, order=':id DESC')
    except RateLimitHit:
        logger.warning('[nyc_dob] Rate limited on permits query')
        return

    stats['items_scraped'] += len(items)
    logger.info(f'[nyc_dob] Permits: fetched {len(items)} raw records')

    for item in items:
        if scraper.is_stopped:
            break
        try:
            # Parse fields from ipu4-2q9a dataset
            job_num = item.get('job__', '')
            borough_raw = item.get('borough', '')
            house_num = item.get('house__', '')
            street = item.get('street_name', '')
            block = item.get('block', '')
            lot = item.get('lot', '')
            job_type = item.get('job_type', '')
            zip_code = item.get('zip_code', '')
            filing_date_str = item.get('filing_date', '')
            permit_status = item.get('permit_status', '')
            owner_biz = item.get('owner_s_business_name', '')
            owner_first = item.get('owner_s_first_name', '')
            owner_last = item.get('owner_s_last_name', '')
            owner_phone = item.get('owner_sphone__', '')
            residential = item.get('residential', '')
            community_board = item.get('community_board', '')
            bldg_type = item.get('bldg_type', '')

            # Client-side date filter
            filing_date = _parse_soda_date(filing_date_str)
            if filing_date and filing_date < cutoff:
                continue

            borough_name = BOROUGH_NAMES.get(borough_raw, borough_raw)
            address = _build_address(house_num, street, borough_name)
            owner_name = f'{owner_first} {owner_last}'.strip()
            owner_display = owner_biz or owner_name
            services = _detect_permit_services(job_type, '')
            has_phone = bool(owner_phone and owner_phone.strip())
            age_text = _days_ago_text(filing_date)

            if not address or address == 'NYC, NY':
                continue

            # Build rich content with contact info and date
            content_parts = [
                f'NYC Building Permit Filed: {job_type.upper() if job_type else "Permit"}',
                f'Filed: {filing_date_str}{f" ({age_text})" if age_text else ""}',
                f'Address: {address}',
            ]
            if zip_code:
                content_parts.append(f'Zip: {zip_code}')
            content_parts.append(f'Borough: {borough_name} | Block: {block} | Lot: {lot}')
            content_parts.append(f'Type: {"Residential" if residential == "YES" else "Commercial"}')

            # Contact info section
            content_parts.append('')  # blank line
            if owner_biz:
                content_parts.append(f'Owner Business: {owner_biz}')
            if owner_name:
                content_parts.append(f'Owner: {owner_name}')
            if has_phone:
                content_parts.append(f'Phone: {owner_phone}')
            if not has_phone:
                content_parts.append('[No phone on file — flag for AI enrichment]')

            content_parts.append(f'Job #: {job_num}')
            content_parts.append(f'Services needed: {", ".join(services[:6])}')
            content = '\n'.join(content_parts)

            raw_data = {
                'source_type': 'nyc_dob_permit',
                'job_number': job_num,
                'borough': borough_name,
                'address': address,
                'block': block,
                'lot': lot,
                'zip_code': zip_code,
                'job_type': job_type,
                'bldg_type': bldg_type,
                'permit_status': permit_status,
                'residential': residential,
                'community_board': community_board,
                'owner_business_name': owner_biz,
                'owner_name': owner_name,
                'owner_phone': owner_phone,
                'has_phone': has_phone,
                'needs_enrichment': not has_phone,
                'filing_date': filing_date_str,
                'services_mapped': services,
            }

            source_url = _soda_url(DATASET_IDS['permits'])

            if dry_run:
                phone_display = owner_phone if has_phone else 'NO PHONE'
                logger.info(
                    f'[DRY RUN] Permit: {address} | {job_type} | '
                    f'Owner: {owner_display} | {phone_display} | '
                    f'Filed: {filing_date_str} ({age_text})'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': owner_display,
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
                author=owner_display,
                posted_at=filing_date,
                raw_data=raw_data,
                state='NY',
                region=borough_name,
                source_group='public_records',
                source_type='permits',
                contact_name=owner_name,
                contact_phone=owner_phone.strip() if has_phone else '',
                contact_business=owner_biz,
                contact_address=address,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.detected_location = f'{address}'
                if zip_code:
                    lead.detected_zip = zip_code
                lead.save(update_fields=[
                    'confidence', 'detected_location', 'detected_zip',
                ])
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
    """Sub-monitor: NYC DOB ECB Violations (dataset 6bgk-3dad).

    Dates are YYYYMMDD text — string comparison works correctly for these.
    API field names (note: penality_imposed is misspelled in the API):
        ecb_violation_number, boro, violation_type, severity,
        respondent_name, respondent_house_number, respondent_street,
        respondent_city, respondent_zip, violation_description,
        penality_imposed, issue_date, ecb_violation_status,
        balance_due, amount_paid, hearing_date, hearing_status,
        section_law_description1, bin, block, lot
    """
    since = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    # Filter: recent + active only (exclude resolved with $0 balance)
    where = (
        f"issue_date > '{since}' "
        f"AND ecb_violation_status != 'RESOLVE'"
    )

    if borough:
        boro_code = BOROUGH_CODE_MAP.get(borough.lower(), '')
        if boro_code:
            where += f" AND boro = '{boro_code}'"

    logger.info(f'[nyc_dob] Querying violations: {where}')

    try:
        items = _query_soda(scraper, DATASET_IDS['violations'], where, order='issue_date DESC')
    except RateLimitHit:
        logger.warning('[nyc_dob] Rate limited on violations query')
        return

    stats['items_scraped'] += len(items)
    logger.info(f'[nyc_dob] Violations: fetched {len(items)} records')

    for item in items:
        if scraper.is_stopped:
            break
        try:
            ecb_num = item.get('ecb_violation_number', '')
            v_type = item.get('violation_type', '')
            severity = item.get('severity', '')
            violation_date_str = item.get('issue_date', '')
            description = item.get('violation_description', '')
            respondent = item.get('respondent_name', '')
            house_num = item.get('respondent_house_number', '')
            street = item.get('respondent_street', '')
            city = item.get('respondent_city', '')
            resp_zip = item.get('respondent_zip', '')
            penalty = item.get('penality_imposed', '')  # API misspelling
            balance = item.get('balance_due', '')
            amount_paid = item.get('amount_paid', '')
            boro_code = item.get('boro', '')
            bin_num = item.get('bin', '')
            block = item.get('block', '')
            lot = item.get('lot', '')
            hearing_date_str = item.get('hearing_date', '')
            hearing_status = item.get('hearing_status', '')
            section_law = item.get('section_law_description1', '')
            ecb_status = item.get('ecb_violation_status', '')

            # Skip fully resolved violations (balance_due = 0)
            try:
                balance_amt = float(balance) if balance else 0
            except (ValueError, TypeError):
                balance_amt = 0
            if ecb_status == 'RESOLVE' and balance_amt == 0:
                continue

            borough_name = BOROUGH_NAMES.get(boro_code, 'NYC')
            # Build full respondent address
            addr_parts = []
            if house_num:
                addr_parts.append(str(house_num).strip())
            if street:
                addr_parts.append(str(street).strip())
            address_line = ' '.join(addr_parts)
            resp_city = city or borough_name
            full_address_parts = [p for p in [address_line, resp_city, 'NY', resp_zip] if p]
            contact_addr = ', '.join(full_address_parts)
            address = _build_address(house_num, street, resp_city)
            violation_date = _parse_soda_date(violation_date_str)
            services = _detect_violation_services(v_type, description or '')
            age_text = _days_ago_text(violation_date)

            if not address or address == 'NYC, NY':
                continue

            penalty_str = f'${int(float(penalty)):,}' if penalty else ''
            balance_str = f'${int(float(balance)):,}' if balance else ''
            paid_str = f'${int(float(amount_paid)):,}' if amount_paid else ''

            # Urgency scoring: penalty amount + severity
            penalty_amount = float(penalty) if penalty else 0
            severity_upper = (severity or '').upper()
            if penalty_amount >= 10_000 or severity_upper in ('HAZARDOUS', 'HZRDOUS'):
                urgency_level = 'hot'
                urgency_score = 95
            elif penalty_amount >= 1_000:
                urgency_level = 'warm'
                urgency_score = 75
            else:
                urgency_level = 'new'
                urgency_score = 50

            # Build rich content with date and contact info
            content_parts = [
                f'NYC DOB VIOLATION: {v_type}',
                f'Issued: {violation_date_str}{f" ({age_text})" if age_text else ""}',
                f'Severity: {severity}',
                f'Status: {ecb_status}',
                f'Address: {address}',
            ]
            if resp_zip:
                content_parts.append(f'Zip: {resp_zip}')
            content_parts.append(f'Block: {block} | Lot: {lot}')
            content_parts.append('')
            if respondent:
                content_parts.append(f'Respondent: {respondent}')
            content_parts.append('[No phone on file — flag for AI enrichment]')
            content_parts.append('')
            if description:
                content_parts.append(f'Description: {description[:400]}')
            if section_law:
                content_parts.append(f'Law: {section_law.strip()}')
            if penalty_str:
                content_parts.append(f'Penalty: {penalty_str}')
            if balance_str:
                content_parts.append(f'Balance Due: {balance_str}')
            if paid_str:
                content_parts.append(f'Amount Paid: {paid_str}')
            if hearing_date_str:
                hearing_date = _parse_soda_date(hearing_date_str)
                h_age = _days_ago_text(hearing_date) if hearing_date else ''
                content_parts.append(f'Hearing: {hearing_date_str} ({hearing_status})')
            content_parts.append(f'ECB #: {ecb_num}')
            content_parts.append('URGENT: Violations must be corrected or fines increase.')
            content_parts.append(f'Services needed: {", ".join(services[:6])}')
            content = '\n'.join(content_parts)

            raw_data = {
                'source_type': 'nyc_dob_violation',
                'ecb_violation_number': ecb_num,
                'violation_type': v_type,
                'severity': severity,
                'ecb_violation_status': ecb_status,
                'address': address,
                'block': block,
                'lot': lot,
                'bin': bin_num,
                'respondent': respondent,
                'respondent_city': city,
                'respondent_zip': resp_zip,
                'description': (description or '')[:500],
                'section_law': section_law,
                'penalty': penalty,
                'balance_due': balance,
                'amount_paid': amount_paid,
                'hearing_date': hearing_date_str,
                'hearing_status': hearing_status,
                'issue_date': violation_date_str,
                'needs_enrichment': True,
                'services_mapped': services,
            }

            source_url = _soda_url(DATASET_IDS['violations'])

            if dry_run:
                logger.info(
                    f'[DRY RUN] Violation: {address} | {v_type} | '
                    f'{severity} | Respondent: {respondent or "N/A"} | '
                    f'Issued: {violation_date_str} ({age_text}) | '
                    f'Penalty: {penalty_str or "N/A"} | Balance: {balance_str or "N/A"} | '
                    f'Urgency: {urgency_level.upper()}'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': respondent,
                    'confidence': 'high',
                    'urgency': urgency_level,
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
                author=respondent,
                posted_at=violation_date,
                raw_data=raw_data,
                state='NY',
                region=borough_name,
                source_group='public_records',
                source_type='violations',
                contact_name=respondent,
                contact_address=contact_addr,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.urgency_level = urgency_level
                lead.urgency_score = urgency_score
                lead.detected_location = address
                if resp_zip:
                    lead.detected_zip = resp_zip
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
            logger.error(f'[nyc_dob] Violation processing error: {e}')
            stats['errors'] += 1


# -------------------------------------------------------------------
# Sub-monitor: Certificates of Occupancy
# -------------------------------------------------------------------

def _monitor_certificates(scraper, borough, days, dry_run, remote, stats,
                          ingest_url, api_key):
    """Sub-monitor: NYC certificates of occupancy (dataset bs8b-p36w).

    c_o_issue_date is a proper calendar_date column — ISO date filtering works.
    Borough values are text names: Manhattan, Bronx, Brooklyn, Queens, Staten Island.
    """
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    where = f"c_o_issue_date > '{since}'"

    if borough:
        # This dataset uses title-case borough names
        boro_name = borough.replace('_', ' ').title()
        where += f" AND borough = '{boro_name}'"

    logger.info(f'[nyc_dob] Querying certificates of occupancy: {where}')

    try:
        items = _query_soda(scraper, DATASET_IDS['certificates'], where, order='c_o_issue_date DESC')
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
            block = item.get('block', '')
            lot = item.get('lot', '')
            bin_num = item.get('bin__', '')
            applicant_first = item.get('applicant_s_first_name', '')
            applicant_last = item.get('applicant_s_last_name', '')
            owner_name_raw = item.get('owner_name', '')

            borough_name = BOROUGH_NAMES.get(borough_code, borough_code)
            address = _build_address(house_num, street, borough_name)
            co_date = _parse_soda_date(co_date_str)
            age_text = _days_ago_text(co_date)
            applicant_name = f'{applicant_first} {applicant_last}'.strip()
            owner_display = owner_name_raw or applicant_name

            if not address or address == 'NYC, NY':
                continue

            # Build rich content with date, contact info, and enrichment flag
            content_parts = [
                f'Certificate of Occupancy Issued: {job_type}',
                f'Issued: {co_date_str[:10] if co_date_str else "N/A"}'
                f'{f" ({age_text})" if age_text else ""}',
                f'Address: {address}',
            ]
            if postcode:
                content_parts.append(f'Zip: {postcode}')
            content_parts.append(f'Borough: {borough_name} | Block: {block} | Lot: {lot}')
            content_parts.append(f'Issue Type: {issue_type}')
            content_parts.append('')
            if owner_display:
                content_parts.append(f'Owner/Applicant: {owner_display}')
            content_parts.append('[No phone on file — flag for AI enrichment]')
            content_parts.append('')
            content_parts.append(f'Job #: {job_num}')
            content_parts.append('New tenant moving in — high-value lead window.')
            content_parts.append(f'Services needed: {", ".join(CO_SERVICES[:6])}')
            content = '\n'.join(content_parts)

            raw_data = {
                'source_type': 'nyc_dob_certificate',
                'job_number': job_num,
                'borough': borough_name,
                'address': address,
                'block': block,
                'lot': lot,
                'bin': bin_num,
                'job_type': job_type,
                'issue_type': issue_type,
                'postcode': postcode,
                'c_o_issue_date': co_date_str,
                'owner_name': owner_display,
                'applicant_name': applicant_name,
                'needs_enrichment': True,
                'services_mapped': CO_SERVICES,
            }

            source_url = _soda_url(DATASET_IDS['certificates'])

            if dry_run:
                logger.info(
                    f'[DRY RUN] CO: {address} | {job_type} | '
                    f'Owner: {owner_display or "N/A"} | '
                    f'Issued: {co_date_str[:10] if co_date_str else "N/A"} ({age_text})'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': owner_display,
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
                author=owner_display,
                posted_at=co_date,
                raw_data=raw_data,
                state='NY',
                region=borough_name,
                source_group='public_records',
                source_type='permits',
                contact_name=owner_display,
                contact_address=address,
            )

            if lead and created:
                lead.confidence = 'high'
                lead.urgency_level = 'hot'
                lead.urgency_score = 85
                lead.detected_location = address
                if postcode:
                    lead.detected_zip = postcode
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
