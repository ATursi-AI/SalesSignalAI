"""
California county health inspection monitors for SalesSignal AI.

Covers multiple CA counties via their respective open data portals:

  - Sacramento County:  inspections.myhealthdepartment.com/sacramento
                        (also SACOG open data + EMD site) — DAILY updates
  - San Diego County:   data.sandiegocounty.gov — 95K+ facilities
  - Santa Clara County: data.sccgov.org dataset 2u2d-8jej — Socrata API
  - LA County:          data.lacounty.gov — has owner_name field (quarterly)

Each county function returns stats in the standard monitor format.
"""
import csv
import io
import logging
from datetime import datetime, timedelta

import requests
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Shared violation → service mapping
# ──────────────────────────────────────────────
VIOLATION_SERVICE_MAP = {
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'cockroach': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'mouse': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'fly': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber'],
    'ventilation': ['HVAC'],
    'exhaust': ['HVAC'],
    'hood': ['commercial kitchen cleaning', 'HVAC'],
    'temperature': ['HVAC', 'refrigeration repair'],
    'refriger': ['refrigeration repair', 'kitchen equipment repair'],
    'cooler': ['refrigeration repair'],
    'freezer': ['refrigeration repair'],
    'cold hold': ['refrigeration repair'],
    'hot hold': ['kitchen equipment repair'],
    'cleaning': ['commercial cleaning', 'deep cleaning'],
    'sanit': ['commercial cleaning', 'deep cleaning'],
    'floor': ['commercial cleaning', 'flooring'],
    'wall': ['painter', 'general contractor'],
    'ceiling': ['general contractor'],
    'mold': ['mold remediation'],
    'grease': ['grease trap cleaning', 'commercial kitchen cleaning'],
    'fire': ['fire safety', 'electrician'],
    'extinguisher': ['fire safety'],
    'electrical': ['electrician'],
    'lighting': ['electrician'],
    'trash': ['commercial cleaning', 'waste management'],
    'restroom': ['plumber', 'commercial cleaning'],
    'handwash': ['plumber', 'commercial cleaning'],
}

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'HVAC', 'plumber']


def _detect_services(text):
    if not text:
        return DEFAULT_SERVICES
    text_lower = text.lower()
    services = set()
    for key, svc_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


def _parse_date(date_str):
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in [
        '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d',
        '%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y', '%b %d, %Y',
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _process_facilities(facilities, source_name, source_url, state, region, dry_run, stats):
    """Shared processing for all CA counties."""
    printed = 0
    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac.get('address', '')
        phone = fac.get('phone', '')
        owner = fac.get('owner_name', '')
        insp_date = fac.get('inspection_date')
        score = fac.get('score')
        grade = fac.get('grade', '')
        all_violations = '\n'.join(fac.get('violations', []))
        services = _detect_services(all_violations)

        # Urgency
        is_critical = any(
            t in all_violations.lower()
            for t in ['critical', 'major', 'imminent', 'closure', 'fail']
        )
        if score is not None:
            try:
                score_num = float(score)
                if score_num < 70:
                    is_critical = True
            except (ValueError, TypeError):
                pass

        if grade and grade.upper() in ('C', 'D', 'F'):
            is_critical = True

        urgency = 'hot' if is_critical else 'warm'
        urgency_note = 'Critical/major violation — facility at risk' if is_critical else 'Violations found during inspection'

        content_parts = [f'HEALTH VIOLATION: {name}']
        if owner:
            content_parts.append(f'Owner: {owner}')
        if address:
            content_parts.append(f'Address: {address}')
        if phone:
            content_parts.append(f'Phone: {phone}')
        if score is not None:
            content_parts.append(f'Score: {score}')
        if grade:
            content_parts.append(f'Grade: {grade}')
        if insp_date:
            days_ago = (timezone.now() - insp_date).days
            content_parts.append(f'Inspected: {days_ago} days ago')
        if all_violations:
            content_parts.append(f'Violations: {all_violations[:500]}')
        content_parts.append(f'Urgency: {urgency_note}')
        content_parts.append(f'Services needed: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 10:
                print(f'\n  [{region}] {name}')
                if owner:
                    print(f'    Owner: {owner}')
                print(f'    {address}')
                if phone:
                    print(f'    Phone: {phone}')
                print(f'    Score: {score}  Grade: {grade}  Urgency: {urgency.upper()}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author='',
                posted_at=insp_date,
                raw_data={
                    'data_source': source_name,
                    'business_name': name,
                    'owner_name': owner,
                    'address': address,
                    'phone': phone,
                    'score': score,
                    'grade': grade,
                    'urgency': urgency,
                    'services_mapped': services,
                },
                state=state,
                region=region,
                source_group='public_records',
                source_type='health_inspections',
                contact_business=name,
                contact_name=owner,
                contact_phone=phone,
                contact_address=address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[{source_name}] Error processing {name}: {e}')
            stats['errors'] += 1


# ──────────────────────────────────────────────
# SANTA CLARA COUNTY — Socrata API
# ──────────────────────────────────────────────
SCC_SODA_URL = 'https://data.sccgov.org/resource/2u2d-8jej.json'


def monitor_santa_clara_health(days=7, dry_run=False):
    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    params = {
        '$where': f"inspection_date >= '{since}'",
        '$limit': 5000,
        '$order': 'inspection_date DESC',
    }

    logger.info(f'[santa_clara] Querying Socrata API, days={days}')
    try:
        resp = requests.get(SCC_SODA_URL, params=params, timeout=60,
                            headers={'User-Agent': 'SalesSignalAI/1.0'})
        if resp.status_code != 200:
            logger.error(f'[santa_clara] SODA returned {resp.status_code}')
            stats['errors'] += 1
            return stats
        data = resp.json()
    except Exception as e:
        logger.error(f'[santa_clara] API error: {e}')
        stats['errors'] += 1
        return stats

    if not isinstance(data, list):
        stats['errors'] += 1
        return stats

    logger.info(f'[santa_clara] Fetched {len(data)} records')

    facilities = {}
    cutoff = timezone.now() - timedelta(days=days)
    for rec in data:
        name = (rec.get('facility_name', '') or rec.get('name', '')).strip()
        if not name:
            continue

        address = (rec.get('address', '') or rec.get('facility_address', '')).strip()
        city = (rec.get('city', '') or 'San Jose').strip()
        phone = (rec.get('phone', '') or rec.get('telephone', '')).strip()
        insp_date = _parse_date(rec.get('inspection_date', ''))
        score = rec.get('score', rec.get('compliance_score', ''))
        grade = rec.get('grade', '')
        violations = rec.get('violation_description', '') or rec.get('violations', '')

        if insp_date and insp_date < cutoff:
            continue

        full_addr = f"{address}, {city}, CA" if address else f"{city}, CA"
        fac_key = f"{name}|{full_addr}"
        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name, 'address': full_addr, 'phone': phone,
                'inspection_date': insp_date, 'score': score, 'grade': grade,
                'violations': [],
            }
        if violations:
            facilities[fac_key]['violations'].append(str(violations))

    stats['items_scraped'] = len(facilities)
    _process_facilities(facilities, 'santa_clara_county', SCC_SODA_URL, 'CA',
                        'Santa Clara County', dry_run, stats)
    logger.info(f'Santa Clara health monitor complete: {stats}')
    return stats


# ──────────────────────────────────────────────
# SAN DIEGO COUNTY — data portal
# ──────────────────────────────────────────────
SD_API_URL = 'https://data.sandiegocounty.gov/resource/nd4s-9r7d.json'
SD_FALLBACK = 'https://data.sandiegocounty.gov/resource/5hzn-fenw.json'


def monitor_san_diego_health(days=7, dry_run=False):
    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

    data = []
    for url in [SD_API_URL, SD_FALLBACK]:
        try:
            params = {
                '$where': f"inspection_date >= '{since}'",
                '$limit': 5000,
                '$order': 'inspection_date DESC',
            }
            resp = requests.get(url, params=params, timeout=60,
                                headers={'User-Agent': 'SalesSignalAI/1.0'})
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    logger.info(f'[san_diego] Got {len(data)} from {url}')
                    break
        except Exception:
            continue

    if not data:
        # Try without date filter to verify API works
        for url in [SD_API_URL, SD_FALLBACK]:
            try:
                resp = requests.get(url, params={'$limit': 10}, timeout=30,
                                    headers={'User-Agent': 'SalesSignalAI/1.0'})
                if resp.status_code == 200:
                    test_data = resp.json()
                    if isinstance(test_data, list) and test_data:
                        logger.info(f'[san_diego] API works at {url}, using date filter')
                        data = test_data
                        break
            except Exception:
                continue

    if not data:
        logger.warning('[san_diego] No data from any endpoint')
        return stats

    facilities = {}
    cutoff = timezone.now() - timedelta(days=days)
    for rec in data:
        if not isinstance(rec, dict):
            continue
        r = {k.lower(): v for k, v in rec.items() if v}
        name = (
            r.get('facility_name', '') or r.get('name', '')
            or r.get('dba', '') or r.get('business_name', '')
        ).strip()
        if not name:
            continue

        address = (r.get('address', '') or r.get('facility_address', '')).strip()
        city = (r.get('city', '') or 'San Diego').strip()
        phone = (r.get('phone', '') or r.get('telephone', '')).strip()
        insp_date = _parse_date(
            r.get('inspection_date', '') or r.get('date', '')
        )
        score = r.get('score', r.get('grade_score', ''))
        grade = r.get('grade', '')
        violations = r.get('violation_description', '') or r.get('violations', '')

        if insp_date and insp_date < cutoff:
            continue

        full_addr = f"{address}, {city}, CA" if address else f"{city}, CA"
        fac_key = f"{name}|{full_addr}"
        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name, 'address': full_addr, 'phone': phone,
                'inspection_date': insp_date, 'score': score, 'grade': grade,
                'violations': [],
            }
        if violations:
            facilities[fac_key]['violations'].append(str(violations))

    stats['items_scraped'] = len(facilities)
    _process_facilities(facilities, 'san_diego_county', SD_API_URL, 'CA',
                        'San Diego County', dry_run, stats)
    logger.info(f'San Diego health monitor complete: {stats}')
    return stats


# ──────────────────────────────────────────────
# LA COUNTY — Socrata API (has owner_name!)
# ──────────────────────────────────────────────
LA_INSPECTIONS_URL = 'https://data.lacounty.gov/resource/6ni6-h5kp.json'
LA_VIOLATIONS_URL = 'https://data.lacounty.gov/resource/8jyd-4pv9.json'


def monitor_la_county_health(days=30, dry_run=False):
    """LA County — quarterly update but has owner_name field."""
    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    params = {
        '$where': f"activity_date >= '{since}'",
        '$select': (
            'facility_id,facility_name,owner_id,owner_name,'
            'facility_address,facility_city,facility_zip,'
            'activity_date,score,grade'
        ),
        '$limit': 5000,
        '$order': 'activity_date DESC',
    }

    logger.info(f'[la_county] Querying Socrata API, days={days}')
    try:
        resp = requests.get(LA_INSPECTIONS_URL, params=params, timeout=60,
                            headers={'User-Agent': 'SalesSignalAI/1.0'})
        if resp.status_code != 200:
            logger.error(f'[la_county] SODA returned {resp.status_code}')
            stats['errors'] += 1
            return stats
        data = resp.json()
    except Exception as e:
        logger.error(f'[la_county] API error: {e}')
        stats['errors'] += 1
        return stats

    if not isinstance(data, list):
        return stats

    logger.info(f'[la_county] Fetched {len(data)} inspection records')

    facilities = {}
    cutoff = timezone.now() - timedelta(days=days)
    for rec in data:
        name = (rec.get('facility_name', '') or '').strip()
        if not name:
            continue

        fac_id = rec.get('facility_id', '')
        owner_name = (rec.get('owner_name', '') or '').strip()
        address = (rec.get('facility_address', '') or '').strip()
        city = (rec.get('facility_city', '') or '').strip()
        zipcode = (rec.get('facility_zip', '') or '').strip()
        insp_date = _parse_date(rec.get('activity_date', ''))
        score = rec.get('score', '')
        grade = rec.get('grade', '')

        if insp_date and insp_date < cutoff:
            continue

        full_addr = f"{address}, {city}, CA {zipcode}".strip()
        fac_key = fac_id or f"{name}|{full_addr}"

        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name, 'owner_name': owner_name, 'address': full_addr,
                'inspection_date': insp_date, 'score': score, 'grade': grade,
                'violations': [],
            }

    # Now fetch violations for these facilities
    if facilities and not dry_run:
        try:
            viol_params = {
                '$where': f"activity_date >= '{since}'",
                '$limit': 10000,
                '$select': 'facility_id,violation_code,violation_description,points',
            }
            viol_resp = requests.get(LA_VIOLATIONS_URL, params=viol_params, timeout=60,
                                     headers={'User-Agent': 'SalesSignalAI/1.0'})
            if viol_resp.status_code == 200:
                viols = viol_resp.json()
                for v in viols:
                    fid = v.get('facility_id', '')
                    desc = v.get('violation_description', '')
                    if fid in facilities and desc:
                        facilities[fid]['violations'].append(desc)
        except Exception as e:
            logger.warning(f'[la_county] Violations fetch error: {e}')

    stats['items_scraped'] = len(facilities)
    _process_facilities(facilities, 'la_county', LA_INSPECTIONS_URL, 'CA',
                        'Los Angeles County', dry_run, stats)
    logger.info(f'LA County health monitor complete: {stats}')
    return stats


# ──────────────────────────────────────────────
# SACRAMENTO COUNTY — myhealthdepartment.com + SACOG
# ──────────────────────────────────────────────
SACRAMENTO_SACOG_URL = 'https://data.sacog.org/resource/h3pu-mmdq.json'
SACRAMENTO_EMD_URL = 'https://emdinspections.saccounty.net/api/inspections'


def monitor_sacramento_health(days=7, dry_run=False):
    """Sacramento County — daily updates via multiple endpoints."""
    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    cutoff = timezone.now() - timedelta(days=days)

    data = []

    # Try SACOG Socrata endpoint
    try:
        params = {
            '$where': f"inspection_date >= '{since}'",
            '$limit': 5000,
            '$order': 'inspection_date DESC',
        }
        resp = requests.get(SACRAMENTO_SACOG_URL, params=params, timeout=60,
                            headers={'User-Agent': 'SalesSignalAI/1.0'})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                logger.info(f'[sacramento] Got {len(data)} from SACOG')
    except Exception as e:
        logger.warning(f'[sacramento] SACOG error: {e}')

    # Try EMD API if SACOG didn't work
    if not data:
        try:
            resp = requests.get(SACRAMENTO_EMD_URL, params={
                'startDate': (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d'),
                'pageSize': 500,
            }, timeout=30, headers={
                'User-Agent': 'SalesSignalAI/1.0',
                'Accept': 'application/json',
            })
            if resp.status_code == 200:
                raw = resp.json()
                data = raw if isinstance(raw, list) else raw.get('results', raw.get('data', []))
                logger.info(f'[sacramento] Got {len(data)} from EMD API')
        except Exception as e:
            logger.warning(f'[sacramento] EMD error: {e}')

    if not data:
        logger.warning('[sacramento] No data from any endpoint')
        return stats

    facilities = {}
    for rec in data:
        if not isinstance(rec, dict):
            continue
        r = {k.lower(): v for k, v in rec.items() if v}
        name = (
            r.get('facility_name', '') or r.get('name', '')
            or r.get('establishment', '') or r.get('dba', '')
        ).strip()
        if not name:
            continue

        address = (r.get('address', '') or r.get('facility_address', '')).strip()
        city = (r.get('city', '') or 'Sacramento').strip()
        phone = (r.get('phone', '') or r.get('telephone', '')).strip()
        insp_date = _parse_date(
            r.get('inspection_date', '') or r.get('date', '')
        )
        score = r.get('score', r.get('total_score', ''))
        violations = r.get('violation_description', '') or r.get('violations', '')
        result = r.get('result', '') or r.get('status', '')

        if insp_date and insp_date < cutoff:
            continue

        full_addr = f"{address}, {city}, CA" if address else f"{city}, CA"
        fac_key = f"{name}|{full_addr}"
        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name, 'address': full_addr, 'phone': phone,
                'inspection_date': insp_date, 'score': score, 'grade': '',
                'violations': [],
            }
        if violations:
            facilities[fac_key]['violations'].append(str(violations))

    stats['items_scraped'] = len(facilities)
    _process_facilities(facilities, 'sacramento_county', SACRAMENTO_SACOG_URL, 'CA',
                        'Sacramento County', dry_run, stats)
    logger.info(f'Sacramento health monitor complete: {stats}')
    return stats
