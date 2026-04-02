"""
Las Vegas / Clark County NV health inspection monitor for SalesSignal AI.

TWO data sources (tries ZIP first, falls back to ArcGIS):

1. SNHD Nightly ZIP dump (CSV tables):
   https://www.southernnevadahealthdistrict.org/restaurants/download/restaurants.zip
   Tables: restaurant_establishments, restaurant_inspections, restaurant_violations
   Joined via permit_number / violation_code.

2. City of Las Vegas ArcGIS Open Data (fallback):
   Inspections: https://opendataportal-lasvegas.opendata.arcgis.com/.../c630117f44774db8814ab88c8ec97853_0
   Violations:  https://opendataportal-lasvegas.opendata.arcgis.com/.../9abf2b74783e49f4949afc06839860a7_0

   Inspections have a 'Violations' field with pipe-delimited violation IDs (e.g. "29|30").
   Violations lookup table has Violation_ID -> Violation_Description.
   Join on Violation_ID to get full text descriptions.
"""
import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta

import requests
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# SNHD nightly ZIP dump
SNHD_ZIP_URL = 'https://www.southernnevadahealthdistrict.org/restaurants/download/restaurants.zip'

# ArcGIS fallback endpoints (City of Las Vegas open data portal)
ARCGIS_INSPECTIONS_URL = (
    'https://services2.arcgis.com/JeWWOMbPkAbJRUKC/arcgis/rest/services/'
    'SNHD_Restaurant_Inspections/FeatureServer/0/query'
)
ARCGIS_VIOLATIONS_URL = (
    'https://services2.arcgis.com/JeWWOMbPkAbJRUKC/arcgis/rest/services/'
    'SNHD_Restaurant_Violations/FeatureServer/0/query'
)

HEADERS = {'User-Agent': 'SalesSignalAI/1.0'}

VIOLATION_SERVICE_MAP = {
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'cockroach': ['pest control', 'exterminator'],
    'fly': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber', 'sewer service'],
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
    'dumpster': ['commercial cleaning', 'waste management'],
    'restroom': ['plumber', 'commercial cleaning'],
    'handwash': ['plumber', 'commercial cleaning'],
    'pool': ['pool service'],
    'spa': ['pool service'],
    'window': ['general contractor', 'window cleaning'],
    'door': ['general contractor'],
    'roof': ['roofer'],
    'pavement': ['concrete contractor'],
}

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'HVAC', 'plumber']


def _detect_services(violation_text):
    if not violation_text:
        return DEFAULT_SERVICES
    text_lower = violation_text.lower()
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
        '%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y', '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


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


def _read_csv_from_zip(zf, possible_names):
    """Find and read a CSV file from the ZIP by trying multiple filenames.
    SNHD uses semicolons as delimiters — auto-detect from first line."""
    zip_names = zf.namelist()

    for name in possible_names:
        matched = None
        if name in zip_names:
            matched = name
        else:
            for zn in zip_names:
                if name.lower() in zn.lower():
                    matched = zn
                    break
        if matched:
            with zf.open(matched) as f:
                text = io.TextIOWrapper(f, encoding='utf-8', errors='replace')
                first_line = text.readline()
                text.seek(0)
                delimiter = ';' if first_line.count(';') > first_line.count(',') else ','
                return list(csv.DictReader(text, delimiter=delimiter))
    return []


def _arcgis_fetch(url, params, dry_run=False, max_records=10000):
    """Fetch features from ArcGIS FeatureServer REST API with pagination."""
    all_features = []
    offset = 0
    page_size = 1000

    fetch_params = {**params, 'resultRecordCount': page_size}

    while len(all_features) < max_records:
        p = {**fetch_params, 'resultOffset': offset}
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
            exceeded = data.get('exceededTransferLimit', None)
            if exceeded is False:
                break
            if len(features) < page_size:
                break
            offset += page_size
        except Exception as e:
            if dry_run:
                print(f'  ArcGIS fetch error: {e}')
            break

    return all_features


# ──────────────────────────────────────────────
# Strategy 1: SNHD ZIP dump (original method)
# ──────────────────────────────────────────────
def _fetch_via_zip(days, dry_run=False):
    """Try the SNHD nightly ZIP dump. Returns (facilities dict, violations_def dict) or (None, None) on failure."""
    cutoff = timezone.now() - timedelta(days=days)

    if dry_run:
        print(f'  Downloading SNHD ZIP from {SNHD_ZIP_URL}...')

    try:
        resp = requests.get(SNHD_ZIP_URL, timeout=120, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if resp.status_code != 200:
            if dry_run:
                print(f'  ZIP download returned HTTP {resp.status_code}')
            return None, None
        if len(resp.content) < 1000:
            if dry_run:
                print(f'  ZIP too small ({len(resp.content)} bytes)')
            return None, None
    except Exception as e:
        if dry_run:
            print(f'  ZIP download failed: {e}')
        return None, None

    if dry_run:
        print(f'  Downloaded {len(resp.content):,} bytes')

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile:
        if dry_run:
            print('  Not a valid ZIP')
        return None, None

    if dry_run:
        print(f'  ZIP contents: {zf.namelist()}')

    establishments_rows = _read_csv_from_zip(zf, [
        'restaurant_establishments.csv', 'establishments.csv', 'restaurants.csv',
    ])
    inspections_rows = _read_csv_from_zip(zf, [
        'restaurant_inspections.csv', 'inspections.csv',
    ])

    # Violation definitions lookup
    violations_def = {}
    violations_rows = _read_csv_from_zip(zf, [
        'restaurant_violations.csv', 'violations.csv',
    ])
    for vr in violations_rows:
        vid = vr.get('violation_id', '') or vr.get('violation_code', '')
        desc = vr.get('violation_description', '')
        demerits = vr.get('violation_demerits', '')
        if vid and desc:
            violations_def[str(vid).strip()] = {
                'description': desc.strip(),
                'demerits': demerits.strip() if demerits else '0',
            }

    if dry_run:
        print(f'  Establishments: {len(establishments_rows)}')
        print(f'  Inspections: {len(inspections_rows)}')
        print(f'  Violation defs: {len(violations_def)}')

    zf.close()

    if not establishments_rows:
        return None, None

    # Build establishment lookup
    est_lookup = {}
    for row in establishments_rows:
        r = {k.lower().strip(): (v or '').strip() for k, v in row.items() if k}
        permit = r.get('permit_number', '')
        if permit:
            est_lookup[permit] = r

    # Process inspections
    facilities = {}

    if inspections_rows:
        for row in inspections_rows:
            r = {k.lower().strip(): (v or '').strip() for k, v in row.items() if k}
            insp_date = _parse_date(r.get('inspection_date', ''))
            if not insp_date or insp_date < cutoff:
                continue

            permit = r.get('permit_number', '')
            est = est_lookup.get(permit, {})
            name = est.get('restaurant_name', '') or est.get('location_name', '')
            if not name:
                continue

            address = est.get('address', '')
            city = est.get('city_name', '') or 'Las Vegas'
            zipcode = est.get('zip_code', '')
            insp_grade = r.get('inspection_grade', '') or r.get('inspection_grade_new', '')

            insp_demerits = 0
            try:
                insp_demerits = int(r.get('inspection_demerits', 0))
            except (ValueError, TypeError):
                pass

            # Parse pipe-delimited violation IDs and expand
            violations_raw = r.get('violations', '')
            violation_details = []
            if violations_raw and violations_def:
                for vid in violations_raw.replace('|', ',').split(','):
                    vid = vid.strip()
                    if vid in violations_def:
                        vdef = violations_def[vid]
                        demerit_str = f' ({vdef["demerits"]} demerits)' if vdef['demerits'] and vdef['demerits'] != '0' else ''
                        violation_details.append(f'{vdef["description"]}{demerit_str}')
                    elif vid:
                        violation_details.append(f'Violation #{vid}')
            elif violations_raw:
                violation_details.append(violations_raw)

            fac_key = f"{name}|{address}"
            if fac_key not in facilities:
                facilities[fac_key] = {
                    'name': name,
                    'address': address,
                    'city': city,
                    'zipcode': zipcode,
                    'inspection_date': insp_date,
                    'grade': insp_grade,
                    'demerits': insp_demerits,
                    'violations': [],
                    'violation_details': [],
                }

            if insp_date and (not facilities[fac_key]['inspection_date']
                              or insp_date > facilities[fac_key]['inspection_date']):
                facilities[fac_key]['inspection_date'] = insp_date
                facilities[fac_key]['grade'] = insp_grade
                facilities[fac_key]['demerits'] = insp_demerits

            facilities[fac_key]['violation_details'].extend(violation_details)

    elif establishments_rows:
        for row in establishments_rows:
            r = {k.lower().strip(): (v or '').strip() for k, v in row.items() if k}
            name = r.get('restaurant_name', '') or r.get('location_name', '')
            if not name:
                continue
            insp_date = _parse_date(r.get('date_current', ''))
            if not insp_date or insp_date < cutoff:
                continue

            fac_key = f"{name}|{r.get('address', '')}"
            if fac_key not in facilities:
                demerits = 0
                try:
                    demerits = int(r.get('current_demerits', 0))
                except (ValueError, TypeError):
                    pass

                facilities[fac_key] = {
                    'name': name,
                    'address': r.get('address', ''),
                    'city': r.get('city_name', '') or 'Las Vegas',
                    'zipcode': r.get('zip_code', ''),
                    'inspection_date': insp_date,
                    'grade': r.get('current_grade', ''),
                    'demerits': demerits,
                    'violations': [],
                    'violation_details': [],
                }

    return facilities, violations_def


# ──────────────────────────────────────────────
# Strategy 2: ArcGIS Open Data (fallback)
# ──────────────────────────────────────────────
def _fetch_via_arcgis(days, dry_run=False):
    """Fetch from City of Las Vegas ArcGIS open data portal. Returns (facilities dict, violations_def dict)."""
    cutoff = timezone.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')

    if dry_run:
        print(f'  Fetching from ArcGIS (fallback)...')

    # ── Step 1: Fetch violations lookup table ──
    if dry_run:
        print(f'  Loading violations lookup table...')

    violations_def = {}
    viol_params = {
        'where': '1=1',
        'outFields': 'Violation_ID,Violation_Code,Violation_Demerits,Violation_Description',
        'f': 'json',
        'resultRecordCount': 1000,
    }
    viol_features = _arcgis_fetch(ARCGIS_VIOLATIONS_URL, viol_params, dry_run, max_records=5000)

    for feat in viol_features:
        attrs = feat.get('attributes', {})
        vid = str(attrs.get('Violation_ID', '') or attrs.get('Violation_Code', '') or '').strip()
        desc = (str(attrs.get('Violation_Description', '') or '')).strip()
        demerits = str(attrs.get('Violation_Demerits', 0) or '0')
        if vid and desc:
            violations_def[vid] = {
                'description': desc,
                'demerits': demerits,
            }

    if dry_run:
        print(f'  Violation definitions loaded: {len(violations_def)}')
        if violations_def:
            sample_id = list(violations_def.keys())[0]
            print(f'  Sample: #{sample_id} -> {violations_def[sample_id]["description"][:80]}')

    # ── Step 2: Fetch recent inspections ──
    insp_params = {
        'where': f"Inspection_Date >= timestamp '{cutoff_str}'",
        'outFields': (
            'Serial_Number,Permit_Number,Inspection_Date,Inspection_Time,'
            'Inspection_Demerits,Inspection_Grade,Inspection_Result,Violations,Record_Updated'
        ),
        'f': 'json',
        'resultRecordCount': 2000,
    }
    insp_features = _arcgis_fetch(ARCGIS_INSPECTIONS_URL, insp_params, dry_run, max_records=10000)

    if dry_run:
        print(f'  Inspection records fetched: {len(insp_features)}')
        if insp_features:
            sample = insp_features[0].get('attributes', {})
            print(f'  Sample fields: {list(sample.keys())}')
            print(f'  Sample Violations field: {sample.get("Violations", "")}')

    if not insp_features:
        return {}, violations_def

    # ── Step 3: We need establishment names — try a separate establishments layer ──
    # The inspections layer may not have restaurant names. Check if there's
    # a permit table we can join, or try the establishments endpoint.
    # For now, group by Permit_Number and try to get names from inspection data.

    # Group inspections by Permit_Number, expand violations
    facilities = {}
    for feat in insp_features:
        attrs = feat.get('attributes', {})
        if not attrs:
            continue

        permit = str(attrs.get('Permit_Number', '') or '').strip()
        if not permit:
            continue

        insp_date = _parse_epoch_ms(attrs.get('Inspection_Date'))
        if not insp_date or insp_date < cutoff:
            continue

        grade = str(attrs.get('Inspection_Grade', '') or '').strip()
        result = str(attrs.get('Inspection_Result', '') or '').strip()
        demerits = 0
        try:
            demerits = int(attrs.get('Inspection_Demerits', 0) or 0)
        except (ValueError, TypeError):
            pass

        # Parse pipe-delimited violation IDs
        violations_raw = str(attrs.get('Violations', '') or '').strip()
        violation_details = []
        if violations_raw and violations_def:
            for vid in violations_raw.split('|'):
                vid = vid.strip()
                if vid in violations_def:
                    vdef = violations_def[vid]
                    demerit_str = f' ({vdef["demerits"]} demerits)' if vdef['demerits'] and vdef['demerits'] != '0' else ''
                    violation_details.append(f'{vdef["description"]}{demerit_str}')
                elif vid:
                    violation_details.append(f'Violation #{vid}')

        fac_key = permit
        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': f'Permit #{permit}',  # Will try to enrich with establishment name
                'address': '',
                'city': 'Las Vegas',
                'zipcode': '',
                'inspection_date': insp_date,
                'grade': grade,
                'result': result,
                'demerits': demerits,
                'violations': [],
                'violation_details': [],
                'permit': permit,
            }

        if insp_date and (not facilities[fac_key]['inspection_date']
                          or insp_date > facilities[fac_key]['inspection_date']):
            facilities[fac_key]['inspection_date'] = insp_date
            facilities[fac_key]['grade'] = grade
            facilities[fac_key]['result'] = result
            facilities[fac_key]['demerits'] = demerits

        facilities[fac_key]['violation_details'].extend(violation_details)

    # ── Step 4: Try to fetch establishment names/addresses ──
    # Check if there's an establishments feature layer at index 1 or 2
    for layer_idx in [1, 2]:
        est_url = ARCGIS_INSPECTIONS_URL.replace('/0/query', f'/{layer_idx}/query')
        try:
            permit_list = list(facilities.keys())[:200]
            if not permit_list:
                break
            in_clause = ','.join(f"'{p}'" for p in permit_list)
            est_params = {
                'where': f"Permit_Number in({in_clause})",
                'outFields': '*',
                'f': 'json',
                'resultRecordCount': 1000,
            }
            est_features = _arcgis_fetch(est_url, est_params, dry_run, max_records=2000)
            if est_features:
                if dry_run:
                    sample_attrs = est_features[0].get('attributes', {})
                    print(f'  Establishment layer {layer_idx} fields: {list(sample_attrs.keys())}')
                for feat in est_features:
                    ea = feat.get('attributes', {})
                    perm = str(ea.get('Permit_Number', '') or '').strip()
                    name = (
                        str(ea.get('Restaurant_Name', '') or ea.get('Location_Name', '')
                            or ea.get('Facility_Name', '') or '').strip()
                    )
                    addr = str(ea.get('Address', '') or '').strip()
                    city = str(ea.get('City_Name', '') or ea.get('City', '') or '').strip()
                    zipcode = str(ea.get('Zip_Code', '') or ea.get('Zip', '') or '').strip()

                    if perm in facilities:
                        if name:
                            facilities[perm]['name'] = name
                        if addr:
                            facilities[perm]['address'] = addr
                        if city:
                            facilities[perm]['city'] = city
                        if zipcode:
                            facilities[perm]['zipcode'] = zipcode
                break  # Found the right layer
        except Exception:
            continue

    return facilities, violations_def


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────
def monitor_vegas_health(days=7, dry_run=False):
    """
    Monitor Southern Nevada Health District restaurant inspections.
    Tries SNHD ZIP dump first, falls back to ArcGIS open data.

    Returns dict with stats.
    """
    stats = {
        'sources_checked': 1,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    # ── Try ZIP first, fall back to ArcGIS ──
    if dry_run:
        print(f'\n  Strategy 1: SNHD ZIP dump')
    facilities, violations_def = _fetch_via_zip(days, dry_run)

    if not facilities:
        if dry_run:
            print(f'\n  Strategy 2: ArcGIS Open Data (fallback)')
        facilities, violations_def = _fetch_via_arcgis(days, dry_run)

    if not facilities:
        logger.warning('[vegas_health] No data from either source')
        if dry_run:
            print('  No data from either source')
        return stats

    logger.info(f'[vegas_health] {len(facilities)} facilities with recent inspections')
    stats['items_scraped'] = len(facilities)

    # ── Create leads ──
    printed = 0
    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac['address']
        city = fac.get('city', 'Las Vegas')
        grade = fac['grade']
        demerits = fac['demerits']
        insp_date = fac['inspection_date']
        result = fac.get('result', '')
        violation_details = fac.get('violation_details', [])
        all_violation_text = '\n'.join(violation_details)
        services = _detect_services(all_violation_text)

        full_address = f"{address}, {city}, NV" if address else f"{city}, NV"
        if fac['zipcode']:
            full_address += f" {fac['zipcode']}"

        # Count demerits from individual violations if we have details
        total_violation_demerits = 0
        critical_violations = []
        for vd in violation_details:
            if 'demerits)' in vd:
                try:
                    d = int(vd.split('(')[1].split(' ')[0])
                    total_violation_demerits += d
                    if d >= 5:
                        critical_violations.append(vd)
                except (IndexError, ValueError):
                    pass

        # Urgency based on grade/demerits
        grade_upper = grade.upper() if grade else ''
        if grade_upper in ('C', 'D', 'F', 'X') or demerits >= 40 or critical_violations:
            urgency = 'hot'
            urgency_note = f'Grade {grade} / {demerits} demerits — facility at risk of closure'
        elif grade_upper == 'B' or demerits >= 20:
            urgency = 'warm'
            urgency_note = f'Grade {grade} / {demerits} demerits — violations need attention'
        else:
            urgency = 'new'
            urgency_note = 'Violations found during inspection'

        # Build rich content like NY
        content_parts = [f'HEALTH VIOLATION: {name}']
        if full_address:
            content_parts.append(f'Address: {full_address}')
        if grade:
            content_parts.append(f'Grade: {grade}')
        if result and result != grade:
            content_parts.append(f'Result: {result}')
        if demerits:
            content_parts.append(f'Demerits: {demerits}')
        if insp_date:
            days_ago = (timezone.now() - insp_date).days
            content_parts.append(f'Inspected: {days_ago} days ago')

        if violation_details:
            content_parts.append(f'Violations: {len(violation_details)} total'
                                 f'{f" ({len(critical_violations)} critical)" if critical_violations else ""}')
            for i, vd in enumerate(violation_details[:8]):
                is_crit = vd in critical_violations
                prefix = '  - [CRITICAL] ' if is_crit else '  - '
                content_parts.append(f'{prefix}{vd[:200]}')

        content_parts.append(f'Urgency: {urgency_note}')
        content_parts.append(f'Services needed: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 10:
                print(f'\n  [{city}] {name}')
                print(f'    {full_address}')
                print(f'    Grade: {grade}  Demerits: {demerits}  Urgency: {urgency.upper()}')
                for vd in violation_details[:3]:
                    print(f'    - {vd[:100]}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url='https://www.southernnevadahealthdistrict.org/permits-and-regulations/restaurant-inspections/',
                content=content,
                author='',
                posted_at=insp_date,
                raw_data={
                    'data_source': 'snhd_vegas',
                    'business_name': name,
                    'address': full_address,
                    'grade': grade,
                    'demerits': demerits,
                    'violation_count': len(violation_details),
                    'critical_count': len(critical_violations),
                    'urgency': urgency,
                    'services_mapped': services,
                },
                state='NV',
                region=city,
                source_group='public_records',
                source_type='health_inspections',
                contact_business=name,
                contact_address=full_address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[vegas_health] Error processing {name}: {e}')
            stats['errors'] += 1

    logger.info(f'Vegas health monitor complete: {stats}')
    return stats
