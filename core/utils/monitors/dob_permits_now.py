"""
NYC DOB NOW: Build – Approved Permits monitor for SalesSignal AI.

Queries dataset rbx6-tga4 on data.cityofnewyork.us via the SODA API.
907K rows, 46 columns, updated daily.

DOB NOW permits use real Floating Timestamp fields (issued_date) that
support proper SODA $where date filtering — unlike the legacy BIS
dataset (ipu4-2q9a) which has text date columns.

Key advantages over the legacy BIS dataset:
  - issued_date is a real timestamp (SODA-filterable)
  - has estimated_job_costs for scoring
  - has lat/long coordinates
  - has filing_reason to distinguish Initial vs Renewal permits
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

SODA_BASE = 'https://data.cityofnewyork.us/resource'
DATASET_ID = 'rbx6-tga4'  # DOB NOW: Build – Approved Permits

# Borough names used in this dataset (uppercase full names)
VALID_BOROUGHS = {
    'manhattan': 'MANHATTAN', 'bronx': 'BRONX', 'brooklyn': 'BROOKLYN',
    'queens': 'QUEENS', 'staten island': 'STATEN ISLAND',
    'staten_island': 'STATEN ISLAND',
}

BOROUGH_DISPLAY = {
    'MANHATTAN': 'Manhattan', 'BRONX': 'Bronx', 'BROOKLYN': 'Brooklyn',
    'QUEENS': 'Queens', 'STATEN ISLAND': 'Staten Island',
}

# Work type → service categories mapping
WORK_TYPE_SERVICES = {
    'structural': ['structural engineer', 'general contractor', 'architect'],
    'general construction': ['general contractor', 'architect', 'demolition'],
    'plumbing': ['plumber', 'pipe fitter'],
    'sprinklers': ['fire protection', 'sprinkler contractor'],
    'standpipe': ['fire protection', 'plumber'],
    'mechanical': ['HVAC', 'mechanical contractor'],
    'elevator': ['elevator contractor', 'elevator inspection'],
    'boiler': ['boiler repair', 'HVAC', 'mechanical contractor'],
    'fire alarm': ['fire alarm contractor', 'electrician'],
    'fire suppression': ['fire protection', 'sprinkler contractor'],
    'fuel burning': ['HVAC', 'boiler repair'],
    'fuel storage': ['environmental', 'tank removal'],
    'construction fence': ['general contractor', 'fencing'],
    'sidewalk shed': ['general contractor', 'scaffolding'],
    'scaffold': ['scaffolding', 'general contractor'],
    'sign': ['signage', 'electrician'],
    'curb cut': ['general contractor', 'paving'],
}

# Job description keywords → additional services
JOB_DESCRIPTION_SERVICES = {
    'renovation': ['general contractor', 'architect', 'demolition'],
    'alteration': ['general contractor', 'architect'],
    'demolition': ['demolition', 'environmental', 'asbestos'],
    'new building': ['general contractor', 'architect', 'engineer'],
    'kitchen': ['kitchen equipment', 'plumber', 'HVAC'],
    'bathroom': ['plumber', 'tile contractor'],
    'roof': ['roofer', 'general contractor'],
    'facade': ['facade contractor', 'scaffolding'],
    'electrical': ['electrician'],
    'hvac': ['HVAC', 'mechanical contractor'],
    'plumbing': ['plumber'],
    'sprinkler': ['fire protection', 'sprinkler contractor'],
    'asbestos': ['asbestos abatement', 'environmental'],
    'concrete': ['concrete contractor', 'general contractor'],
}

DEFAULT_SERVICES = [
    'General Contractor', 'Architect', 'Plumber', 'Electrician',
    'HVAC', 'Insurance',
]


class DOBPermitsNowScraper(BaseScraper):
    MONITOR_NAME = 'dob_permits_now'
    DELAY_MIN = 1.0
    DELAY_MAX = 3.0
    MAX_REQUESTS_PER_RUN = 20
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = False  # API endpoint


def _query_soda(scraper, where_clause, limit=1000, order='issued_date DESC'):
    """Query the DOB NOW permits dataset via SODA."""
    url = f'{SODA_BASE}/{DATASET_ID}.json'
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
                f'[dob_permits_now] SODA returned {resp.status_code}: '
                f'{resp.text[:200]}'
            )
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[dob_permits_now] SODA query error: {e}')
        return []


def _parse_soda_date(date_str):
    """Parse SODA floating timestamp to aware datetime."""
    if not date_str:
        return None
    from datetime import datetime
    formats = [
        '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d', '%m/%d/%Y',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str[:26], fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _days_ago_text(dt):
    """Return human-readable age text."""
    if not dt:
        return ''
    delta = (timezone.now() - dt).days
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


def _parse_cost(cost_str):
    """Parse estimated_job_costs (text field) to integer."""
    if not cost_str:
        return 0
    try:
        cleaned = cost_str.replace('$', '').replace(',', '').strip()
        return int(float(cleaned))
    except (ValueError, TypeError):
        return 0


def _detect_services(work_type, job_description):
    """Map work type and description to likely services needed."""
    services = set()

    # Check work type
    if work_type:
        wt_lower = work_type.lower()
        for key, svc_list in WORK_TYPE_SERVICES.items():
            if key in wt_lower:
                services.update(svc_list)

    # Check job description keywords
    if job_description:
        desc_lower = job_description.lower()
        for key, svc_list in JOB_DESCRIPTION_SERVICES.items():
            if key in desc_lower:
                services.update(svc_list)

    return list(services) if services else DEFAULT_SERVICES


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
# Main query and processing
# -------------------------------------------------------------------

def monitor_dob_permits_now(borough=None, days=30, dry_run=False,
                            remote=False, min_cost=0, work_type=None):
    """
    Monitor DOB NOW: Build – Approved Permits for new construction activity.

    Queries dataset rbx6-tga4 which has real timestamp fields and
    supports proper SODA $where date filtering.

    Args:
        borough: borough name (optional, e.g. 'queens', 'brooklyn')
        days: lookback period in days (default: 30)
        dry_run: if True, log matches without creating leads
        remote: if True, POST leads to REMOTE_INGEST_URL
        min_cost: minimum estimated job cost to include (default: 0)
        work_type: filter by specific work type (optional)

    Returns:
        dict with sources_checked, items_scraped, created, duplicates,
        assigned, errors keys
    """
    scraper = DOBPermitsNowScraper()

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

    stats = {
        'sources_checked': 0, 'items_scraped': 0, 'created': 0,
        'duplicates': 0, 'assigned': 0, 'errors': 0,
    }

    # Build SODA query
    since = (timezone.now() - timedelta(days=days)).strftime(
        '%Y-%m-%dT00:00:00.000'
    )
    where_parts = [
        f"issued_date >= '{since}'",
        "filing_reason = 'Initial Permit'",
    ]

    if borough:
        borough_upper = VALID_BOROUGHS.get(borough.lower().strip(), '')
        if not borough_upper:
            borough_upper = borough.upper().strip()
        where_parts.append(f"borough = '{borough_upper}'")

    if work_type:
        where_parts.append(f"work_type = '{work_type}'")

    where = ' AND '.join(where_parts)

    logger.info(
        f'[dob_permits_now] Starting DOB NOW permits monitor — '
        f'borough={borough or "all"}, days={days}'
    )
    logger.info(f'[dob_permits_now] Query: {where}')

    stats['sources_checked'] = 1
    records = _query_soda(scraper, where, limit=1000)
    stats['items_scraped'] = len(records)
    logger.info(f'[dob_permits_now] Fetched {len(records)} records')

    for rec in records:
        try:
            # Extract fields
            house_no = rec.get('house_no', '').strip()
            street_name = rec.get('street_name', '').strip()
            boro = rec.get('borough', '').strip()
            zipcode = rec.get('zip_code', '').strip()
            wt = rec.get('work_type', '').strip()
            job_desc = rec.get('job_description', '').strip()
            cost_str = rec.get('estimated_job_costs', '')
            permit_status = rec.get('permit_status', '').strip()
            filing_reason = rec.get('filing_reason', '').strip()
            issued_date = rec.get('issued_date', '')

            # Owner info
            owner_name = rec.get('owner_name', '').strip()
            owner_biz = rec.get('owner_business_name', '').strip()
            owner_street = rec.get('owner_street_address', '').strip()
            owner_city = rec.get('owner_city', '').strip()
            owner_state = rec.get('owner_state', '').strip()
            owner_zip = rec.get('owner_zip_code', '').strip()

            # Applicant (contractor) info
            app_first = rec.get('applicant_first_name', '').strip()
            app_last = rec.get('applicant_last_name', '').strip()
            app_biz = rec.get('applicant_business_name', '').strip()

            # Coordinates
            lat = rec.get('latitude', '')
            lng = rec.get('longitude', '')

            # Job filing number for dedup
            job_filing = (rec.get('job_filing_number', '')
                          or rec.get('work_permit', '')
                          or rec.get('job__', ''))

            # Parse fields
            cost = _parse_cost(cost_str)
            issued_dt = _parse_soda_date(issued_date)
            age_text = _days_ago_text(issued_dt)
            boro_display = BOROUGH_DISPLAY.get(boro, boro)

            # Cost filter
            if min_cost and cost < min_cost:
                continue

            # Build address
            address_parts = []
            if house_no:
                address_parts.append(house_no)
            if street_name:
                address_parts.append(street_name)
            address = ' '.join(address_parts)
            location = f'{address}, {boro_display}'
            if zipcode:
                location += f' {zipcode}'

            # Owner display
            owner_display = owner_name or owner_biz or 'N/A'
            owner_addr_parts = [p for p in [owner_street, owner_city,
                                            owner_state, owner_zip] if p]
            owner_addr = ', '.join(owner_addr_parts)

            # Applicant display
            applicant = f'{app_first} {app_last}'.strip() or app_biz or ''

            # Cost display
            cost_display = f'${cost:,}' if cost else 'N/A'

            # Detect services
            services = _detect_services(wt, job_desc)

            # Scoring
            # HOT: > $100K or structural/general construction
            # WARM: $10K-$100K
            # NEW: < $10K or renewals
            wt_lower = (wt or '').lower()
            if cost >= 100_000 or 'structural' in wt_lower or 'general construction' in wt_lower:
                urgency_level = 'hot'
                urgency_score = 90
                confidence = 'high'
            elif cost >= 10_000:
                urgency_level = 'warm'
                urgency_score = 70
                confidence = 'high' if owner_name else 'medium'
            else:
                urgency_level = 'new'
                urgency_score = 50
                confidence = 'medium' if owner_name else 'low'

            # Build content
            content_parts = [
                f'DOB NOW PERMIT: {wt} at {location}',
                f'Filing: {filing_reason} | Status: {permit_status}',
                f'Estimated Cost: {cost_display}',
            ]
            if issued_dt:
                content_parts.append(
                    f'Issued: {issued_dt.strftime("%m/%d/%Y")} ({age_text})'
                )
            if job_desc:
                content_parts.append(f'Description: {job_desc[:300]}')
            content_parts.append('')
            content_parts.append('--- Contact Info ---')
            content_parts.append(f'Owner: {owner_display}')
            if owner_biz and owner_biz != owner_name:
                content_parts.append(f'Owner Business: {owner_biz}')
            if owner_addr:
                content_parts.append(f'Owner Address: {owner_addr}')
            content_parts.append('[No phone on file — flag for AI enrichment]')
            if applicant:
                content_parts.append(f'Applicant/Contractor: {applicant}')
                if app_biz and app_biz != applicant:
                    content_parts.append(f'Contractor Firm: {app_biz}')
            content_parts.append('')
            content_parts.append(
                f'Services likely needed: {", ".join(services[:7])}'
            )
            content = '\n'.join(content_parts)

            source_url = (
                f'https://data.cityofnewyork.us/resource/{DATASET_ID}.json'
                f'?job_filing_number={job_filing}'
            )

            raw_data = {
                'source_type': 'dob_permits_now',
                'job_filing_number': job_filing,
                'house_no': house_no,
                'street_name': street_name,
                'borough': boro,
                'zip_code': zipcode,
                'work_type': wt,
                'job_description': job_desc[:500] if job_desc else '',
                'estimated_job_costs': cost,
                'permit_status': permit_status,
                'filing_reason': filing_reason,
                'issued_date': issued_date,
                'owner_name': owner_name,
                'owner_business_name': owner_biz,
                'owner_address': owner_addr,
                'applicant_name': f'{app_first} {app_last}'.strip(),
                'applicant_business': app_biz,
                'latitude': lat,
                'longitude': lng,
                'services_mapped': services,
                'needs_enrichment': True,  # DOB NOW doesn't have phone
                'urgency': urgency_level,
                'confidence': confidence,
            }

            if dry_run:
                logger.info(
                    f'[DRY RUN] Permit: {location} | {wt} | '
                    f'Cost: {cost_display} | Owner: {owner_display} | '
                    f'Issued: {age_text} | Urgency: {urgency_level.upper()}'
                )
                stats['created'] += 1
                continue

            if remote and ingest_url:
                payload = {
                    'platform': 'public_records',
                    'source_url': source_url,
                    'source_content': content,
                    'author': owner_display,
                    'confidence': confidence,
                    'urgency': urgency_level,
                    'detected_category': 'DOB_PERMIT_NOW',
                    'raw_data': raw_data,
                }
                ok, sc, body = _post_lead_remote(ingest_url, ingest_key,
                                                 payload)
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
                author=owner_display,
                posted_at=issued_dt,
                raw_data=raw_data,
                state='NY',
                region=boro_display,
                source_group='public_records',
                source_type='permits_now',
                contact_name=owner_name,
                contact_business=owner_biz,
                contact_address=f'{owner_addr}' if owner_addr else location,
            )

            if lead and created:
                lead.confidence = confidence
                lead.urgency_level = urgency_level
                lead.urgency_score = urgency_score
                lead.detected_location = location
                if zipcode:
                    lead.detected_zip = zipcode
                if lat and lng:
                    try:
                        lead.latitude = float(lat)
                        lead.longitude = float(lng)
                    except (ValueError, TypeError):
                        pass
                lead.save(update_fields=[
                    'confidence', 'urgency_level', 'urgency_score',
                    'detected_location', 'detected_zip',
                    'latitude', 'longitude',
                ])
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[dob_permits_now] Processing error: {e}')
            stats['errors'] += 1

    logger.info(f'DOB NOW Permits monitor complete: {stats}')
    return stats
