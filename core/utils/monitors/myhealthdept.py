"""
Multi-jurisdiction health inspection monitor via myhealthdepartment.com

Uses the platform's internal POST API at https://inspections.myhealthdepartment.com/
with task="searchInspections" and a jurisdiction-specific path.

Confirmed API pattern (March 2026):
  POST https://inspections.myhealthdepartment.com/
  Body: {"task": "searchInspections", "data": {"path": "<jurisdiction>", "programName": "", "filters": {"date": "YYYY-MM-DD to YYYY-MM-DD"}}}
  Response: array of {establishmentName, addressLine1, city, state, zip, score, scoreDisplay, inspectionDate, inspectionType, purpose, comments, ADCounty, permitType, ...}

Active jurisdictions:
  - Denver (Colorado):     path = "colorado"
  - Portland (Multnomah):  path = "multco-eh"
  - Colorado Springs:      path = "epcph"
  - Honolulu (Hawaii DOH): path = "soh"
"""
import json
import logging
from datetime import datetime, timedelta

import requests
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Jurisdiction configs
# ──────────────────────────────────────────────
JURISDICTIONS = {
    'denver': {
        'name': 'Denver / Colorado',
        'path': 'colorado',
        'state': 'CO',
        'region': 'Denver',
    },
    'portland': {
        'name': 'Portland / Multnomah County',
        'path': 'multco-eh',
        'state': 'OR',
        'region': 'Multnomah County',
    },
    'colorado_springs': {
        'name': 'Colorado Springs / El Paso County',
        'path': 'epcph',
        'state': 'CO',
        'region': 'Colorado Springs',
    },
    'honolulu': {
        'name': 'Honolulu / Hawaii DOH',
        'path': 'soh',
        'state': 'HI',
        'region': 'Honolulu',
    },
    'orange_county': {
        'name': 'Orange County / OC Health Care Agency',
        'path': 'orange-county',
        'state': 'CA',
        'region': 'Orange County',
    },
    'sacramento': {
        'name': 'Sacramento County EMD',
        'path': 'sacramento',
        'state': 'CA',
        'region': 'Sacramento County',
    },
    'san_francisco': {
        'name': 'San Francisco DPH',
        'path': 'san-francisco',
        'state': 'CA',
        'region': 'San Francisco',
    },
}

API_URL = 'https://inspections.myhealthdepartment.com/'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'Origin': 'https://inspections.myhealthdepartment.com',
    'Referer': 'https://inspections.myhealthdepartment.com/',
}

# ──────────────────────────────────────────────
# Violation → service mapping
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
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber'],
    'ventilation': ['HVAC'],
    'exhaust': ['HVAC'],
    'hood': ['commercial kitchen cleaning', 'HVAC'],
    'temperature': ['HVAC', 'refrigeration repair'],
    'refriger': ['refrigeration repair'],
    'cooler': ['refrigeration repair'],
    'freezer': ['refrigeration repair'],
    'cleaning': ['commercial cleaning', 'deep cleaning'],
    'sanit': ['commercial cleaning', 'deep cleaning'],
    'floor': ['commercial cleaning', 'flooring'],
    'wall': ['painter', 'general contractor'],
    'ceiling': ['general contractor'],
    'mold': ['mold remediation'],
    'grease': ['grease trap cleaning', 'commercial kitchen cleaning'],
    'fire': ['fire safety', 'electrician'],
    'electrical': ['electrician'],
    'trash': ['commercial cleaning', 'waste management'],
    'restroom': ['plumber', 'commercial cleaning'],
    'handwash': ['plumber', 'commercial cleaning'],
    'pool': ['pool service'],
    'spa': ['pool service'],
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
    """Parse ISO date from API (e.g., '2026-03-31T00:00:00.000Z')."""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in [
        '%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y',
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _fetch_inspections(path, days, dry_run=False, max_pages=20, page_size=100):
    """
    Fetch inspections via the myhealthdepartment.com POST API.

    The API caps results at 25 per request by default, but recently-observed
    Vue frontend calls include `page` and `rows` params (see OC closures page).
    Strategy:
      * Day-by-day requests (handles jurisdictions that don't strictly filter
        by date — each day gets its own top-N).
      * Within each day, paginate 1..max_pages until the response is empty
        or returns no new records (handles jurisdictions like Orange County
        that otherwise return only the latest 25).
    """
    all_records = []
    seen_ids = set()  # dedup by inspectionID or name+date

    if dry_run:
        print(f'  POST {API_URL}')
        print(f'  Strategy: day-by-day × pagination (rows={page_size}, max_pages={max_pages})')

    for day_offset in range(days):
        day = datetime.now() - timedelta(days=day_offset)
        day_str = day.strftime('%Y-%m-%d')

        day_total = 0
        day_new = 0

        for page in range(1, max_pages + 1):
            payload = {
                'task': 'searchInspections',
                'data': {
                    'path': path,
                    'programName': '',
                    'filters': {
                        'date': f'{day_str} to {day_str}',
                    },
                    # Pagination params — if API ignores them, we get the same
                    # 25 on page 2 which the dedup below will catch, and we'll
                    # break out on 0 new.
                    'page': page,
                    'rows': page_size,
                },
            }

            try:
                resp = requests.post(API_URL, json=payload, headers=HEADERS, timeout=60)
                if resp.status_code != 200:
                    if dry_run:
                        print(f'  {day_str} p{page}: HTTP {resp.status_code}')
                    logger.warning(f'[myhealthdept] HTTP {resp.status_code} for {path} on {day_str} p{page}')
                    break

                data = resp.json()
                records = []

                if isinstance(data, list):
                    records = data
                elif isinstance(data, dict):
                    if data.get('error'):
                        if dry_run:
                            print(f'  {day_str} p{page}: API error: {data.get("msg", "")}')
                        break
                    records = data.get('data', data.get('results', data.get('items', [])))
                    if not isinstance(records, list):
                        records = []

                if not records:
                    # No more pages for this day
                    break

                # Dedup across days/pages
                new_this_page = 0
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    rec_id = rec.get('inspectionID', '')
                    if not rec_id:
                        rec_id = f"{rec.get('establishmentName', '')}|{rec.get('inspectionDate', '')}"
                    if rec_id not in seen_ids:
                        seen_ids.add(rec_id)
                        all_records.append(rec)
                        new_this_page += 1

                day_total += len(records)
                day_new += new_this_page

                # If every record on this page is a dup, the API is ignoring
                # `page` and returning the same slice — no point hammering.
                if new_this_page == 0:
                    break

            except Exception as e:
                if dry_run:
                    print(f'  {day_str} p{page}: Request error: {e}')
                logger.error(f'[myhealthdept] Request error for {path} on {day_str} p{page}: {e}')
                break

        if dry_run:
            print(f'  {day_str}: {day_total} returned across pages, {day_new} new (total: {len(all_records)})')

    if dry_run:
        print(f'  Total unique records: {len(all_records)}')

    return all_records


def monitor_myhealthdept(jurisdiction='denver', days=7, dry_run=False):
    """
    Monitor a myhealthdepartment.com jurisdiction.

    Args:
        jurisdiction: key from JURISDICTIONS dict
        days: look back N days
        dry_run: log only, don't create leads
    """
    config = JURISDICTIONS.get(jurisdiction)
    if not config:
        logger.error(f'[myhealthdept] Unknown jurisdiction: {jurisdiction}')
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0}

    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    path = config['path']
    state = config['state']
    region = config['region']
    cutoff = timezone.now() - timedelta(days=days)

    logger.info(f'[myhealthdept] Monitoring {config["name"]}, days={days}')

    # ── Fetch via POST API ──
    raw_inspections = _fetch_inspections(path, days, dry_run)

    if dry_run:
        print(f'  Records returned: {len(raw_inspections)}')
        if raw_inspections and isinstance(raw_inspections[0], dict):
            print(f'  Sample fields: {list(raw_inspections[0].keys())}')

    if not raw_inspections:
        logger.warning(f'[myhealthdept] No data for {config["name"]}')
        return stats

    # ── Build facilities dict ──
    facilities = {}
    for rec in raw_inspections:
        if not isinstance(rec, dict):
            continue

        name = (rec.get('establishmentName', '') or '').strip()
        if not name:
            continue

        address1 = (rec.get('addressLine1', '') or '').strip()
        address2 = (rec.get('addressLine2', '') or '').strip()
        city = (rec.get('city', '') or '').strip()
        rec_state = (rec.get('state', '') or state).strip()
        zipcode = (rec.get('zip', '') or '').strip()
        county = (rec.get('ADCounty', '') or rec.get('county', '') or '').strip()

        score = rec.get('score', '')
        if score is None:
            score = ''
        # Different jurisdictions use different field names for result
        score_display = (
            rec.get('scoreDisplay', '') or rec.get('result', '') or ''
        ).strip()
        insp_type = (rec.get('inspectionType', '') or rec.get('InspectionType', '') or '').strip()
        purpose = (rec.get('purpose', '') or rec.get('PurposeofInspection', '') or '').strip()
        comments = (rec.get('comments', '') or '').strip()
        permit_type = (rec.get('permitType', '') or '').strip()
        inspection_id = (rec.get('inspectionID', '') or '').strip()

        insp_date = _parse_date(rec.get('inspectionDate', ''))
        if insp_date and insp_date < cutoff:
            continue

        # Build full address
        addr_parts = [address1]
        if address2:
            addr_parts.append(address2)
        addr_parts.append(f'{city}, {rec_state} {zipcode}'.strip())
        full_addr = ', '.join(p for p in addr_parts if p)

        fac_key = inspection_id or f'{name}|{full_addr}'

        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name,
                'address': full_addr,
                'phone': '',
                'inspection_date': insp_date,
                'score': score,
                'grade': score_display,
                'county': county,
                'inspection_type': f'{insp_type} | {purpose}' if purpose else insp_type,
                'permit_type': permit_type,
                'violations': [],
            }

        # Use comments as violation context
        if comments:
            facilities[fac_key]['violations'].append(comments)

    stats['items_scraped'] = len(facilities)
    if dry_run:
        print(f'  Unique facilities: {len(facilities)}')

    # ── Process leads ──
    source_url = f'https://inspections.myhealthdepartment.com/{path}'
    printed = 0

    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac['address']
        phone = fac.get('phone', '')
        insp_date = fac['inspection_date']
        score = fac.get('score', '')
        score_display = fac.get('grade', '')
        all_violations = '\n'.join(fac.get('violations', []))
        services = _detect_services(all_violations)

        # Urgency: "Re-Inspection Required" or high score = hot lead
        is_hot = False
        if score_display and 're-inspection' in score_display.lower():
            is_hot = True
        if score and isinstance(score, (int, float)) and score >= 50:
            is_hot = True
        if any(kw in all_violations.lower() for kw in
               ['critical', 'imminent', 'closure', 'fail', 'priority']):
            is_hot = True

        urgency = 'hot' if is_hot else 'warm'
        urgency_note = (
            'Re-inspection required — high risk score'
            if is_hot else 'Violations found during inspection'
        )

        content_parts = [f'HEALTH VIOLATION: {name}']
        if address:
            content_parts.append(f'Address: {address}')
        if fac.get('county'):
            content_parts.append(f'County: {fac["county"]}')
        if score is not None and score != '':
            content_parts.append(f'Risk Index Points: {score}')
        if score_display:
            content_parts.append(f'Result: {score_display}')
        if fac.get('inspection_type'):
            content_parts.append(f'Type: {fac["inspection_type"]}')
        if fac.get('permit_type'):
            content_parts.append(f'Permit: {fac["permit_type"]}')
        if insp_date:
            days_ago = (timezone.now() - insp_date).days
            content_parts.append(f'Inspected: {days_ago} days ago')
        if all_violations:
            content_parts.append(f'Notes: {all_violations[:500]}')
        content_parts.append(f'Jurisdiction: {config["name"]}')
        content_parts.append(f'Urgency: {urgency_note}')
        content_parts.append(f'Services needed: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 10:
                print(f'\n  [{config["name"]}] {name}')
                print(f'    {address}')
                if fac.get('county'):
                    print(f'    County: {fac["county"]}')
                print(f'    Score: {score}  Result: {score_display}  Urgency: {urgency.upper()}')
                if fac.get('inspection_type'):
                    print(f'    Type: {fac["inspection_type"]}')
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
                    'data_source': f'myhealthdept_{jurisdiction}',
                    'business_name': name,
                    'address': address,
                    'phone': phone,
                    'score': score,
                    'score_display': score_display,
                    'urgency': urgency,
                    'services_mapped': services,
                },
                state=state,
                region=region,
                source_group='public_records',
                source_type='health_inspections',
                contact_business=name,
                contact_phone=phone,
                contact_address=address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[myhealthdept] Error processing {name}: {e}')
            stats['errors'] += 1

    logger.info(f'myhealthdept {config["name"]} monitor complete: {stats}')
    return stats


# ──────────────────────────────────────────────
# Restaurant closures — via /genericEndpoint
# (separate endpoint from /search used above;
#  returns shut-down facilities with reason codes)
# ──────────────────────────────────────────────
GENERIC_ENDPOINT_URL = 'https://inspections.myhealthdepartment.com/genericEndpoint'

CLOSURE_REASON_SERVICE_MAP = {
    'cockroach': ['pest control', 'exterminator', 'commercial cleaning', 'deep cleaning'],
    'rodent':    ['pest control', 'exterminator', 'commercial cleaning', 'deep cleaning'],
    'rat':       ['pest control', 'exterminator', 'commercial cleaning'],
    'mice':      ['pest control', 'exterminator', 'commercial cleaning'],
    'mouse':     ['pest control', 'exterminator', 'commercial cleaning'],
    'vermin':    ['pest control', 'exterminator', 'commercial cleaning'],
    'insect':    ['pest control', 'exterminator'],
    'pest':      ['pest control', 'exterminator'],
    'sewage':    ['plumber', 'commercial cleaning', 'sewer service'],
    'sewer':     ['plumber', 'sewer service'],
    'water':     ['plumber'],
    'restroom':  ['plumber', 'commercial cleaning'],
    'handwash':  ['plumber', 'commercial cleaning'],
    'fire':      ['fire safety', 'general contractor'],
    'power':     ['electrician'],
    'electrical': ['electrician'],
    'foodborne': ['commercial cleaning', 'deep cleaning'],
    'illness':   ['commercial cleaning', 'deep cleaning'],
}

DEFAULT_CLOSURE_SERVICES = ['commercial cleaning', 'pest control', 'deep cleaning']


def _detect_closure_services(reason):
    """Map a closure reason string to service categories."""
    if not reason:
        return DEFAULT_CLOSURE_SERVICES
    text = str(reason).lower()
    services = set()
    for key, svc_list in CLOSURE_REASON_SERVICE_MAP.items():
        if key in text:
            services.update(svc_list)
    return list(services) if services else DEFAULT_CLOSURE_SERVICES


def _fetch_closures(jurisdiction_path, days_back=60, dry_run=False,
                    max_pages=50, page_size=100):
    """
    Fetch restaurant closures via POST /genericEndpoint with
    requestType="inspclosures". The payload mirrors the exact shape
    the OC Vue frontend sends — confirmed reverse-engineered in April 2026.

    Returns a list of closure records (dicts).
    """
    all_records = []
    seen_ids = set()

    inspection_purposes = [
        "Inspection (Non-Routine)",
        "Notice of Violation Reinspection",
        "Reinspection",
        "Routine Inspection",
    ]

    filter_by_val = json.dumps([
        ["CLOSED", "CLOSED-OPERATOR INITIATED"],
        [str(days_back), "0"],
        ["Retail Food Facility Inspection"],
        inspection_purposes,
    ])

    if dry_run:
        print(f'  POST {GENERIC_ENDPOINT_URL}')
        print(f'  jurisdictionPath={jurisdiction_path} days_back={days_back} page_size={page_size}')

    for page in range(1, max_pages + 1):
        payload = {
            "jurisdictionPath": jurisdiction_path,
            "requestType": "inspclosures",
            "rows": page_size,
            "page": page,
            "searchTerm": "",
            "filterBySrc": json.dumps(["This_Form", "This_Form", "Inspection_Type", "This_Form"]),
            "filterByAct": json.dumps(["EQUAL", "BETWEEN", "EQUAL", "EQUAL"]),
            "filterByCol": json.dumps(["result", "inspectionDate", "type", "InspectionTypeMRS"]),
            "filterByVal": filter_by_val,
            "sort": "This_Form.inspectionDate|DESC",
        }

        try:
            resp = requests.post(GENERIC_ENDPOINT_URL, json=payload,
                                 headers=HEADERS, timeout=60)
            if resp.status_code != 200:
                if dry_run:
                    print(f'  p{page}: HTTP {resp.status_code}')
                logger.warning(f'[closures] HTTP {resp.status_code} for {jurisdiction_path} p{page}')
                break

            data = resp.json()
            body = data.get('data') if isinstance(data, dict) else None
            records = (body or {}).get('DATA') or []
            available = (body or {}).get('availableRows', 0) or 0

            if not isinstance(records, list) or not records:
                if dry_run:
                    print(f'  p{page}: empty, stopping (availableRows={available})')
                break

            new_count = 0
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                rec_id = (
                    rec.get('permitID')
                    or rec.get('inspectionID')
                    or f"{rec.get('FacilityName') or rec.get('name', '')}|{rec.get('inspectionDate', '')}"
                )
                if rec_id in seen_ids:
                    continue
                seen_ids.add(rec_id)
                all_records.append(rec)
                new_count += 1

            if dry_run:
                print(f'  p{page}: {len(records)} returned ({new_count} new), '
                      f'availableRows={available}, total={len(all_records)}')

            # Done if we've pulled everything the API reports
            if available and len(all_records) >= available:
                break

            # API ignoring pagination → no new rows appearing
            if new_count == 0:
                break

        except Exception as e:
            logger.error(f'[closures] Request error for {jurisdiction_path} p{page}: {e}')
            if dry_run:
                print(f'  p{page}: error {e}')
            break

    if dry_run:
        print(f'  Total unique closure records: {len(all_records)}')

    return all_records


def monitor_myhealthdept_closures(jurisdiction='orange_county', days=60, dry_run=False):
    """
    Pull restaurant closures for a jurisdiction. Every closure is a HOT lead —
    by definition they're shut down, usually with cockroach/rodent/sewage
    reasons attached. Reopened facilities drop to WARM (prevention pitch).
    """
    config = JURISDICTIONS.get(jurisdiction)
    if not config:
        logger.error(f'[closures] Unknown jurisdiction: {jurisdiction}')
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0}

    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    path = config['path']
    state = config['state']
    region = config['region']

    logger.info(f'[closures] Monitoring {config["name"]} closures, days={days}')

    records = _fetch_closures(path, days_back=days, dry_run=dry_run)

    if dry_run:
        print(f'  Records returned: {len(records)}')
        if records and isinstance(records[0], dict):
            print(f'  Sample fields: {list(records[0].keys())}')

    if not records:
        logger.warning(f'[closures] No closures for {config["name"]}')
        return stats

    # CLIENT-SIDE DATE FILTER: the platform's `[days, 0]` date filter is
    # unreliable when we paginate deep — it silently returns records far
    # outside the window (we observed 2018 closures coming back on a 60-day
    # ask). Hard-drop anything older than the cutoff ourselves so those
    # historical records with no ReasonforClosure never pollute the DB.
    cutoff = timezone.now() - timedelta(days=days)
    filtered = []
    dropped_old = 0
    for rec in records:
        rec_date = _parse_date(str(rec.get('inspectionDate') or ''))
        if rec_date and rec_date < cutoff:
            dropped_old += 1
            continue
        filtered.append(rec)
    if dry_run and dropped_old:
        print(f'  Dropped {dropped_old} records older than {days} days '
              f'(cutoff {cutoff.date()})')
    records = filtered

    if not records:
        logger.warning(f'[closures] All {len(filtered) + dropped_old} records '
                       f'were outside the {days}-day window for {config["name"]}')
        return stats

    stats['items_scraped'] = len(records)
    source_url = f'https://inspections.myhealthdepartment.com/{path}/restaurant-closures'
    printed = 0

    for rec in records:
        name = (rec.get('FacilityName') or rec.get('name') or '').strip()
        if not name:
            continue

        permit_name = (rec.get('permitName') or '').strip()
        address1 = (rec.get('addressLine1') or '').strip()
        address2 = (rec.get('addressLine2') or '').strip()
        city = (rec.get('city') or '').strip()
        zipcode = (rec.get('zip') or '').strip()
        result = (rec.get('result') or '').strip()
        reason = (rec.get('ReasonforClosure') or '').strip()
        reopened_raw = rec.get('reopenedDate') or ''
        reopened = str(reopened_raw).strip() if reopened_raw else ''
        closure_date_raw = rec.get('inspectionDate') or ''

        insp_date = _parse_date(str(closure_date_raw))

        addr_parts = [address1]
        if address2:
            addr_parts.append(address2)
        addr_parts.append(f'{city}, {state} {zipcode}'.strip())
        full_addr = ', '.join(p for p in addr_parts if p)

        services = _detect_closure_services(reason)

        is_reopened = bool(reopened and reopened.lower() not in ('', 'null', 'none'))
        if is_reopened:
            urgency = 'warm'
            urgency_note = (
                f'Previously closed for {reason}; reopened on {reopened[:10]} — '
                f'offer prevention services'
            ) if reason else f'Previously closed; reopened {reopened[:10]}'
            score = 70
        else:
            urgency = 'hot'
            urgency_note = f'CLOSED — {reason}' if reason else 'CLOSED (reason not specified)'
            score = 95

        # Headline includes reason when available so it's visible in the
        # Command Center list view without expanding the lead.
        headline_parts = [f'RESTAURANT CLOSURE: {name}']
        if reason:
            headline_parts.append(reason)
        headline = ' — '.join(headline_parts)

        content_parts = [headline]
        if permit_name and permit_name != name:
            content_parts.append(f'Permit Name: {permit_name}')
        content_parts.append(f'Address: {full_addr}')
        if closure_date_raw:
            content_parts.append(f'Closed: {str(closure_date_raw)[:10]}')
        if reason:
            content_parts.append(f'Reason for Closure: {reason}')
        else:
            content_parts.append('Reason for Closure: (not specified)')
        content_parts.append(f'Status: {result}')
        if is_reopened:
            content_parts.append(f'Reopened: {reopened[:10]}')
        content_parts.append(f'Jurisdiction: {config["name"]}')
        content_parts.append(f'Urgency: {urgency_note}')
        content_parts.append(f'Services needed: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 10:
                print(f'\n  [{config["name"]}] {name}')
                print(f'    {full_addr}')
                print(f'    Closed: {str(closure_date_raw)[:10]}  Reason: {reason}')
                print(f'    Status: {result}  Reopened: {reopened[:10] if reopened else "(not reopened)"}')
                print(f'    Urgency: {urgency.upper()}')
                printed += 1
            # Dry-run: count as "would-create" so the total reflects what a
            # real run would do, but don't claim it was actually saved.
            stats.setdefault('would_create', 0)
            stats['would_create'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author='',
                posted_at=insp_date,
                raw_data={
                    'data_source': f'myhealthdept_closures_{jurisdiction}',
                    'business_name': name,
                    'permit_name': permit_name,
                    'address': full_addr,
                    'closure_date': str(closure_date_raw),
                    'reopened_date': reopened,
                    'reason': reason,
                    'result': result,
                    'urgency': urgency,
                    'urgency_score': score,
                    'services_mapped': services,
                    'jurisdiction_path': path,
                    'raw_record': rec,
                },
                state=state,
                region=region,
                source_group='public_records',
                source_type='health_closures',
                contact_business=name,
                contact_address=full_addr,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[closures] Error processing {name}: {e}')
            stats['errors'] += 1

    logger.info(f'[closures] {config["name"]} complete: {stats}')
    return stats
