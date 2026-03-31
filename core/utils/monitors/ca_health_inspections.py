"""
California county health inspection monitors for SalesSignal AI.

Covers multiple CA counties via their respective open data portals:

  - Santa Clara County: data.sccgov.org Socrata API (3 joined datasets) — WORKING
  - Sacramento County:  ArcGIS FeatureServer (services1.arcgis.com) — ~6K records, daily updates
  - San Diego County:   data.sandiegocounty.gov — endpoints unverified
  - LA County:          ArcGIS FeatureServer (services.arcgis.com) — 106K+ records, quarterly, has owner_name

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


def _arcgis_fetch(url, params, dry_run=False):
    """Fetch features from ArcGIS FeatureServer REST API with pagination."""
    all_features = []
    offset = 0
    batch_size = params.get('resultRecordCount', 2000)

    while True:
        p = {**params, 'resultOffset': offset}
        try:
            resp = requests.get(url, params=p, timeout=120, headers=HEADERS)
            if resp.status_code != 200:
                if dry_run:
                    print(f'  ArcGIS returned HTTP {resp.status_code}')
                break
            data = resp.json()
            if 'error' in data:
                if dry_run:
                    print(f'  ArcGIS error: {data["error"]}')
                break
            features = data.get('features', [])
            if not features:
                break
            all_features.extend(features)
            if dry_run:
                print(f'  Fetched batch: {len(features)} (total: {len(all_features)})')
            # ArcGIS returns exceededTransferLimit if there are more pages
            if not data.get('exceededTransferLimit', False):
                break
            offset += batch_size
        except Exception as e:
            if dry_run:
                print(f'  ArcGIS fetch error: {e}')
            break

    return all_features


def _parse_epoch_ms(val):
    """Parse ArcGIS epoch-millisecond date fields."""
    if not val:
        return None
    try:
        ts = int(val) / 1000
        dt = datetime.utcfromtimestamp(ts)
        return timezone.make_aware(dt)
    except (ValueError, TypeError, OSError):
        return None


# ──────────────────────────────────────────────
# SANTA CLARA COUNTY — Socrata API (3 datasets)
#   Inspections: 2u2d-8jej  (business_id, date, score, result)
#   Business:    vuw7-jmjk  (business_id, name, address, city, phone_number)
#   Violations:  wkaa-4ccv  (inspection_id, description, critical, violation_comment)
# ──────────────────────────────────────────────
SCC_INSPECTIONS_URL = 'https://data.sccgov.org/resource/2u2d-8jej.json'
SCC_BUSINESS_URL = 'https://data.sccgov.org/resource/vuw7-jmjk.json'
SCC_VIOLATIONS_URL = 'https://data.sccgov.org/resource/wkaa-4ccv.json'

HEADERS = {'User-Agent': 'SalesSignalAI/1.0'}


def _scc_fetch_json(url, params):
    """Fetch JSON from Santa Clara Socrata API with error handling."""
    try:
        resp = requests.get(url, params=params, timeout=60, headers=HEADERS)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.warning(f'[santa_clara] Fetch error for {url}: {e}')
    return []


def monitor_santa_clara_health(days=7, dry_run=False):
    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    # Date field in inspections is YYYYMMDD format (e.g., "20250609")
    since_yyyymmdd = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    cutoff = timezone.now() - timedelta(days=days)

    # ── Step 1: Fetch recent inspections ──
    if dry_run:
        print(f'  Fetching inspections since {since_yyyymmdd}...')
    inspections = _scc_fetch_json(SCC_INSPECTIONS_URL, {
        '$where': f"date >= '{since_yyyymmdd}'",
        '$limit': 5000,
        '$order': 'date DESC',
    })
    if dry_run:
        print(f'  Inspections fetched: {len(inspections)}')
        if inspections:
            print(f'  Sample inspection: {inspections[0]}')

    if not inspections:
        logger.warning('[santa_clara] No recent inspections found')
        return stats

    # Collect unique business_ids and inspection_ids
    business_ids = set()
    inspection_ids = set()
    for insp in inspections:
        bid = insp.get('business_id', '')
        iid = insp.get('inpsection_id', '') or insp.get('inspection_id', '')  # note typo in API
        if bid:
            business_ids.add(bid)
        if iid:
            inspection_ids.add(iid)

    if dry_run:
        print(f'  Unique businesses: {len(business_ids)}')
        print(f'  Unique inspections: {len(inspection_ids)}')

    # ── Step 2: Fetch business details for those business_ids ──
    biz_lookup = {}
    # Fetch in batches (Socrata $where IN clause)
    bid_list = list(business_ids)
    batch_size = 200
    for i in range(0, len(bid_list), batch_size):
        batch = bid_list[i:i + batch_size]
        in_clause = ','.join(f"'{b}'" for b in batch)
        biz_data = _scc_fetch_json(SCC_BUSINESS_URL, {
            '$where': f"business_id in({in_clause})",
            '$limit': 5000,
        })
        for biz in biz_data:
            bid = biz.get('business_id', '')
            if bid:
                biz_lookup[bid] = biz

    if dry_run:
        print(f'  Business records fetched: {len(biz_lookup)}')
        if biz_lookup:
            sample_biz = list(biz_lookup.values())[0]
            print(f'  Sample business: {sample_biz.get("name", "")} — {sample_biz.get("phone_number", "no phone")}')

    # ── Step 3: Fetch violations for those inspection_ids ──
    violations_lookup = {}  # inspection_id -> list of violation texts
    iid_list = list(inspection_ids)
    for i in range(0, len(iid_list), batch_size):
        batch = iid_list[i:i + batch_size]
        in_clause = ','.join(f"'{v}'" for v in batch)
        viol_data = _scc_fetch_json(SCC_VIOLATIONS_URL, {
            '$where': f"inspection_id in({in_clause})",
            '$limit': 10000,
        })
        for v in viol_data:
            iid = v.get('inspection_id', '')
            desc = v.get('description', '')
            comment = v.get('violation_comment', '')
            is_critical = v.get('critical', False)
            viol_text = desc
            if comment:
                viol_text += f' — {comment[:200]}'
            if is_critical:
                viol_text = f'[CRITICAL] {viol_text}'
            if iid and viol_text:
                violations_lookup.setdefault(iid, []).append(viol_text)

    if dry_run:
        print(f'  Violation records fetched: {sum(len(v) for v in violations_lookup.values())}')

    # ── Step 4: Join everything and build facilities dict ──
    facilities = {}
    for insp in inspections:
        bid = insp.get('business_id', '')
        iid = insp.get('inpsection_id', '') or insp.get('inspection_id', '')
        biz = biz_lookup.get(bid, {})

        name = (biz.get('name', '') or '').strip()
        if not name:
            continue

        address = (biz.get('address', '') or '').strip()
        city = (biz.get('city', '') or 'San Jose').strip()
        phone = (biz.get('phone_number', '') or '').strip()
        postal = (biz.get('postal_code', '') or '').strip()

        # Parse YYYYMMDD date
        date_str = insp.get('date', '')
        insp_date = _parse_date(date_str)
        if not insp_date and date_str and len(date_str) == 8:
            try:
                insp_date = _parse_date(f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}')
            except Exception:
                pass

        if insp_date and insp_date < cutoff:
            continue

        score = insp.get('score', '')
        result = insp.get('result', '')

        full_addr = f"{address}, {city}, CA {postal}".strip() if address else f"{city}, CA"
        fac_key = bid or f"{name}|{full_addr}"

        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name, 'address': full_addr, 'phone': phone,
                'inspection_date': insp_date, 'score': score, 'grade': result,
                'violations': [],
            }

        # Keep most recent inspection
        if insp_date and facilities[fac_key]['inspection_date']:
            if insp_date > facilities[fac_key]['inspection_date']:
                facilities[fac_key]['inspection_date'] = insp_date
                facilities[fac_key]['score'] = score
                facilities[fac_key]['grade'] = result

        # Add violations for this inspection
        if iid and iid in violations_lookup:
            facilities[fac_key]['violations'].extend(violations_lookup[iid])

    stats['items_scraped'] = len(facilities)
    if dry_run:
        print(f'  Facilities with recent inspections: {len(facilities)}')

    _process_facilities(facilities, 'santa_clara_county', SCC_INSPECTIONS_URL, 'CA',
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
# LA COUNTY — ArcGIS FeatureServer (has owner_name!)
#   Inspections: 106K+ records, quarterly updates (last: Jan 7 2026)
#   Fields: ACTIVITY_DATE, OWNER_NAME, FACILITY_ID, FACILITY_NAME,
#           FACILITY_ADDRESS, FACILITY_CITY, FACILITY_ZIP, SCORE, GRADE,
#           SERIAL_NUMBER (joins to violations), SERVICE_DESCRIPTION
#   Violations: SERIAL_NUMBER, VIOLATION_CODE, VIOLATION_DESCRIPTION, POINTS
# ──────────────────────────────────────────────
LA_INSPECTIONS_URL = (
    'https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/'
    'Environmental_Health_Restaurant_and_Market_Inspections_01012023_to_123120025/'
    'FeatureServer/0/query'
)
LA_VIOLATIONS_URL = (
    'https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/'
    'Environmental_Health_Restaurant_and_Market_Violations_01012023_to_123120025/'
    'FeatureServer/0/query'
)


def monitor_la_county_health(days=30, dry_run=False):
    """LA County — ArcGIS FeatureServer (quarterly update, has owner_name, 106K+ records)."""
    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    cutoff = timezone.now() - timedelta(days=days)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    if dry_run:
        print(f'  Querying LA County ArcGIS FeatureServer (last {days} days)...')
        print(f'  URL: {LA_INSPECTIONS_URL}')

    # ── Fetch recent inspections ──
    params = {
        'where': f'ACTIVITY_DATE >= {cutoff_ms}',
        'outFields': ('ACTIVITY_DATE,OWNER_NAME,FACILITY_ID,FACILITY_NAME,'
                      'FACILITY_ADDRESS,FACILITY_CITY,FACILITY_ZIP,'
                      'SCORE,GRADE,SERIAL_NUMBER,SERVICE_DESCRIPTION'),
        'f': 'json',
        'resultRecordCount': 2000,
    }
    features = _arcgis_fetch(LA_INSPECTIONS_URL, params, dry_run)

    if dry_run:
        print(f'  Inspection records fetched: {len(features)}')
        if features:
            sample = features[0].get('attributes', {})
            print(f'  Sample fields: {list(sample.keys())}')
            print(f'  Sample: {sample.get("FACILITY_NAME", "")} — '
                  f'Score: {sample.get("SCORE", "")} Grade: {sample.get("GRADE", "")}')

    if not features:
        logger.warning('[la_county] No data from ArcGIS FeatureServer')
        if dry_run:
            print('  No data returned — check if ACTIVITY_DATE field uses epoch ms')
        return stats

    # ── Build facilities dict + collect serial numbers for violation lookup ──
    facilities = {}
    serial_to_facility = {}  # SERIAL_NUMBER -> fac_key

    for feat in features:
        attrs = feat.get('attributes', {})
        if not attrs:
            continue

        name = (str(attrs.get('FACILITY_NAME', '') or '')).strip()
        if not name:
            continue

        fac_id = str(attrs.get('FACILITY_ID', '') or '').strip()
        owner_name = (str(attrs.get('OWNER_NAME', '') or '')).strip()
        address = (str(attrs.get('FACILITY_ADDRESS', '') or '')).strip()
        city = (str(attrs.get('FACILITY_CITY', '') or '')).strip()
        zipcode = (str(attrs.get('FACILITY_ZIP', '') or '')).strip()
        score = attrs.get('SCORE', '')
        grade = (str(attrs.get('GRADE', '') or '')).strip()
        serial = str(attrs.get('SERIAL_NUMBER', '') or '').strip()

        insp_date = _parse_epoch_ms(attrs.get('ACTIVITY_DATE'))
        if insp_date and insp_date < cutoff:
            continue

        full_addr = f"{address}, {city}, CA {zipcode}".strip() if address else f"{city}, CA"
        fac_key = fac_id or f"{name}|{full_addr}"

        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name,
                'owner_name': owner_name,
                'address': full_addr,
                'inspection_date': insp_date,
                'score': score,
                'grade': grade,
                'violations': [],
            }

        # Keep most recent inspection
        if insp_date and facilities[fac_key]['inspection_date']:
            if insp_date > facilities[fac_key]['inspection_date']:
                facilities[fac_key]['inspection_date'] = insp_date
                facilities[fac_key]['score'] = score
                facilities[fac_key]['grade'] = grade

        if serial:
            serial_to_facility[serial] = fac_key

    if dry_run:
        print(f'  Unique facilities: {len(facilities)}')
        print(f'  Serial numbers for violation lookup: {len(serial_to_facility)}')

    # ── Fetch violations by SERIAL_NUMBER in batches ──
    serial_list = list(serial_to_facility.keys())
    batch_size = 200
    violations_fetched = 0

    for i in range(0, len(serial_list), batch_size):
        batch = serial_list[i:i + batch_size]
        in_clause = ','.join(f"'{s}'" for s in batch)
        viol_params = {
            'where': f"SERIAL_NUMBER in({in_clause})",
            'outFields': 'SERIAL_NUMBER,VIOLATION_DESCRIPTION,VIOLATION_CODE,POINTS',
            'f': 'json',
            'resultRecordCount': 2000,
        }
        try:
            resp = requests.get(LA_VIOLATIONS_URL, params=viol_params,
                                timeout=120, headers=HEADERS)
            if resp.status_code == 200:
                data = resp.json()
                for feat in data.get('features', []):
                    va = feat.get('attributes', {})
                    serial = str(va.get('SERIAL_NUMBER', '') or '').strip()
                    desc = (str(va.get('VIOLATION_DESCRIPTION', '') or '')).strip()
                    points = va.get('POINTS', '')
                    fac_key = serial_to_facility.get(serial)
                    if fac_key and desc and fac_key in facilities:
                        viol_text = desc
                        if points:
                            viol_text += f' ({points} pts)'
                        facilities[fac_key]['violations'].append(viol_text)
                        violations_fetched += 1
        except Exception as e:
            logger.warning(f'[la_county] Violation batch fetch error: {e}')
            if dry_run:
                print(f'  Violation batch error: {e}')

    if dry_run:
        print(f'  Violations fetched: {violations_fetched}')

    stats['items_scraped'] = len(facilities)
    _process_facilities(
        facilities, 'la_county',
        'https://data.lacounty.gov/datasets/19b6607ac82c4512b10811870975dbdc',
        'CA', 'Los Angeles County', dry_run, stats,
    )
    logger.info(f'LA County health monitor complete: {stats}')
    return stats


# ──────────────────────────────────────────────
# SACRAMENTO COUNTY — ArcGIS FeatureServer
#   Sacramento County Environmental Management Dept
#   Layer 0: Facilities (points with lat/lon)
#   Layer 1: Inspection & Violation History (table)
#   Fields: Facility_ID, Facility_Name, Facility_Address, Description,
#           Inspection_Service, Inspection_Type, Inspection_Result,
#           Inspection_Date (epoch ms), Inspection_Report, Violation_Description
#   ~6,200 records, updated daily
# ──────────────────────────────────────────────
SACRAMENTO_FEATURESERVER = (
    'https://services1.arcgis.com/5NARefyPVtAeuJPU/arcgis/rest/services/'
    'Food_Inspections/FeatureServer'
)
SACRAMENTO_FACILITIES_URL = f'{SACRAMENTO_FEATURESERVER}/0/query'
SACRAMENTO_HISTORY_URL = f'{SACRAMENTO_FEATURESERVER}/1/query'


def monitor_sacramento_health(days=7, dry_run=False):
    """Sacramento County — ArcGIS FeatureServer (daily updates, ~6K records)."""
    stats = {'sources_checked': 1, 'items_scraped': 0, 'created': 0,
             'duplicates': 0, 'assigned': 0, 'errors': 0}

    cutoff = timezone.now() - timedelta(days=days)
    # ArcGIS date filter uses epoch ms
    cutoff_ms = int(cutoff.timestamp() * 1000)

    if dry_run:
        print(f'  Querying Sacramento ArcGIS FeatureServer (last {days} days)...')
        print(f'  URL: {SACRAMENTO_FACILITIES_URL}')

    # ── Fetch from Layer 0 (Facilities — has geometry) ──
    params = {
        'where': f'Inspection_Date >= {cutoff_ms}',
        'outFields': '*',
        'f': 'json',
        'resultRecordCount': 2000,
    }
    features = _arcgis_fetch(SACRAMENTO_FACILITIES_URL, params, dry_run)

    if dry_run:
        print(f'  Layer 0 features: {len(features)}')
        if features:
            sample = features[0].get('attributes', {})
            print(f'  Sample fields: {list(sample.keys())}')
            print(f'  Sample: {sample.get("Facility_Name", "")} — '
                  f'{sample.get("Facility_Address", "")}')

    # If Layer 0 returned nothing, try Layer 1 (History table)
    if not features:
        if dry_run:
            print(f'  Trying Layer 1 (History)...')
            print(f'  URL: {SACRAMENTO_HISTORY_URL}')
        features = _arcgis_fetch(SACRAMENTO_HISTORY_URL, params, dry_run)
        if dry_run:
            print(f'  Layer 1 features: {len(features)}')
            if features:
                sample = features[0].get('attributes', {})
                print(f'  Sample fields: {list(sample.keys())}')

    if not features:
        if dry_run:
            print('  No data from Sacramento ArcGIS')
        logger.warning('[sacramento] No data from ArcGIS FeatureServer')
        return stats

    # ── Build facilities dict ──
    facilities = {}
    for feat in features:
        attrs = feat.get('attributes', {})
        if not attrs:
            continue

        name = (attrs.get('Facility_Name', '') or '').strip()
        if not name:
            continue

        fac_id = attrs.get('Facility_ID', '') or ''
        address = (attrs.get('Facility_Address', '') or '').strip()
        description = (attrs.get('Description', '') or '').strip()
        service = (attrs.get('Inspection_Service', '') or '').strip()
        insp_type = (attrs.get('Inspection_Type', '') or '').strip()
        result = (attrs.get('Inspection_Result', '') or '').strip()
        violations = (attrs.get('Violation_Description', '') or '').strip()
        report_url = (attrs.get('Inspection_Report', '') or '').strip()

        # Parse epoch-ms date
        insp_date = _parse_epoch_ms(attrs.get('Inspection_Date'))
        if insp_date and insp_date < cutoff:
            continue

        # Build full address — Sacramento County doesn't include city in data,
        # most facilities are in Sacramento metro area
        full_addr = f"{address}, Sacramento, CA" if address else "Sacramento, CA"

        fac_key = str(fac_id) if fac_id else f"{name}|{full_addr}"

        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name,
                'address': full_addr,
                'phone': '',
                'inspection_date': insp_date,
                'score': '',
                'grade': result,
                'violations': [],
            }

        # Keep most recent inspection
        if insp_date and facilities[fac_key]['inspection_date']:
            if insp_date > facilities[fac_key]['inspection_date']:
                facilities[fac_key]['inspection_date'] = insp_date
                facilities[fac_key]['grade'] = result

        # Add violations
        if violations:
            facilities[fac_key]['violations'].append(violations)
        # Also include description/service/type as context if they contain
        # violation-relevant keywords
        if description and any(kw in description.lower() for kw in
                               ['violation', 'fail', 'critical', 'major']):
            facilities[fac_key]['violations'].append(description)

    stats['items_scraped'] = len(facilities)
    if dry_run:
        print(f'  Facilities with recent inspections: {len(facilities)}')

    _process_facilities(
        facilities, 'sacramento_county',
        'https://services1.arcgis.com/5NARefyPVtAeuJPU/arcgis/rest/services/Food_Inspections/FeatureServer',
        'CA', 'Sacramento County', dry_run, stats,
    )
    logger.info(f'Sacramento health monitor complete: {stats}')
    return stats
