"""
Las Vegas / Clark County NV health inspection monitor for SalesSignal AI.

Southern Nevada Health District publishes a nightly ZIP of ALL food
establishment inspections as CSV files.

Developer page:
  https://www.southernnevadahealthdistrict.org/permits-and-regulations/
  restaurant-inspections/developers/

ZIP contains CSV tables matching this schema (from restaurants.sql):
  - restaurant_establishments: permit_number, restaurant_name, address,
        city_name, zip_code, current_grade, current_demerits, date_current
  - restaurant_inspections: permit_number, inspection_date, inspection_demerits,
        inspection_grade, violations
  - restaurant_violations: violation_code, violation_demerits, violation_description

We download the ZIP, extract CSVs, join establishments + inspections,
and filter to recent inspection dates.
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

# Nightly ZIP dump from SNHD developers page
SNHD_ZIP_URL = 'https://www.southernnevadahealthdistrict.org/restaurants/download/restaurants.zip'

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
    'pool': ['pool service'],
    'spa': ['pool service'],
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
                # Peek at first line to detect delimiter
                first_line = text.readline()
                text.seek(0)
                delimiter = ';' if first_line.count(';') > first_line.count(',') else ','
                return list(csv.DictReader(text, delimiter=delimiter))
    return []


def monitor_vegas_health(days=7, dry_run=False):
    """
    Monitor Southern Nevada Health District restaurant inspections.

    Downloads the nightly ZIP dump, joins establishments with recent
    inspections, and creates leads for facilities with violations.

    Args:
        days: how many days back to include (default: 7)
        dry_run: if True, log matches without creating Leads

    Returns:
        dict with stats
    """
    stats = {
        'sources_checked': 1,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    cutoff = timezone.now() - timedelta(days=days)

    # ── Download the ZIP ──
    if dry_run:
        print(f'  Downloading SNHD ZIP from {SNHD_ZIP_URL}...')
    logger.info(f'[vegas_health] Downloading SNHD ZIP from {SNHD_ZIP_URL}')
    try:
        resp = requests.get(SNHD_ZIP_URL, timeout=120, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if resp.status_code != 200:
            if dry_run:
                print(f'  ERROR: ZIP download returned HTTP {resp.status_code}')
            stats['errors'] += 1
            return stats
        if len(resp.content) < 1000:
            if dry_run:
                print(f'  ERROR: ZIP too small ({len(resp.content)} bytes) — might be HTML error page')
                print(f'  First 500 chars: {resp.content[:500]}')
            stats['errors'] += 1
            return stats
    except Exception as e:
        if dry_run:
            print(f'  ERROR: ZIP download failed: {e}')
        stats['errors'] += 1
        return stats

    if dry_run:
        print(f'  Downloaded {len(resp.content):,} bytes')

    # ── Extract CSVs from ZIP ──
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile:
        if dry_run:
            print('  ERROR: Downloaded file is not a valid ZIP')
            print(f'  Content-Type: {resp.headers.get("Content-Type", "unknown")}')
            print(f'  First 200 bytes: {resp.content[:200]}')
        stats['errors'] += 1
        return stats

    if dry_run:
        print(f'  ZIP contents: {zf.namelist()}')

    # Read establishments (has restaurant name, address, city, grade, demerits)
    establishments_rows = _read_csv_from_zip(zf, [
        'restaurant_establishments.csv', 'establishments.csv', 'restaurants.csv',
    ])
    if dry_run:
        print(f'  Establishments loaded: {len(establishments_rows)}')
        if establishments_rows:
            print(f'  Establishment fields: {list(establishments_rows[0].keys())}')
            # Show a sample row
            sample = {k: v for k, v in establishments_rows[0].items() if k and v}
            print(f'  Sample establishment: {dict(list(sample.items())[:8])}')

    # Read inspections (has inspection_date, demerits, grade, violations)
    inspections_rows = _read_csv_from_zip(zf, [
        'restaurant_inspections.csv', 'inspections.csv',
    ])
    if dry_run:
        print(f'  Inspections loaded: {len(inspections_rows)}')
        if inspections_rows:
            print(f'  Inspection fields: {list(inspections_rows[0].keys())}')
            # Show most recent inspection dates
            dates = []
            for row in inspections_rows[:100]:
                d = row.get('inspection_date', '')
                if d:
                    dates.append(d)
            if dates:
                dates.sort(reverse=True)
                print(f'  Most recent inspection dates: {dates[:5]}')

    # Read violation definitions (optional — for better descriptions)
    violations_def = {}
    violations_rows = _read_csv_from_zip(zf, [
        'restaurant_violations.csv', 'violations.csv',
    ])
    if dry_run:
        print(f'  Violation definitions loaded: {len(violations_rows)}')
    for vr in violations_rows:
        vid = vr.get('violation_id', '') or vr.get('violation_code', '')
        desc = vr.get('violation_description', '')
        if vid and desc:
            violations_def[str(vid)] = desc

    zf.close()

    # ── Build establishment lookup by permit_number ──
    est_lookup = {}
    for row in establishments_rows:
        r = {k.lower().strip(): (v or '').strip() for k, v in row.items() if k}
        permit = r.get('permit_number', '')
        if permit:
            est_lookup[permit] = r

    # ── Process inspections — filter to recent dates ──
    facilities = {}

    if inspections_rows:
        # Join inspections with establishments
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

            # Inspection-level data
            insp_grade = r.get('inspection_grade', '') or r.get('inspection_grade_new', '')
            insp_demerits = 0
            try:
                insp_demerits = int(r.get('inspection_demerits', 0))
            except (ValueError, TypeError):
                pass

            violations_text = r.get('violations', '')
            # Expand violation IDs if we have definitions
            if violations_text and violations_def:
                expanded = []
                for vid in violations_text.split(','):
                    vid = vid.strip()
                    if vid in violations_def:
                        expanded.append(violations_def[vid])
                    elif vid:
                        expanded.append(vid)
                if expanded:
                    violations_text = '; '.join(expanded)

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
                }
            # Keep the most recent inspection date
            if insp_date and (not facilities[fac_key]['inspection_date']
                              or insp_date > facilities[fac_key]['inspection_date']):
                facilities[fac_key]['inspection_date'] = insp_date
                facilities[fac_key]['grade'] = insp_grade
                facilities[fac_key]['demerits'] = insp_demerits

            if violations_text:
                facilities[fac_key]['violations'].append(violations_text)

    elif establishments_rows:
        # No inspections CSV — use establishments with date_current field
        logger.info('[vegas_health] No inspections CSV, using establishments date_current')
        for row in establishments_rows:
            r = {k.lower().strip(): (v or '').strip() for k, v in row.items() if k}

            name = r.get('restaurant_name', '') or r.get('location_name', '')
            if not name:
                continue

            insp_date = _parse_date(r.get('date_current', ''))
            if not insp_date or insp_date < cutoff:
                continue

            address = r.get('address', '')
            city = r.get('city_name', '') or 'Las Vegas'
            zipcode = r.get('zip_code', '')
            grade = r.get('current_grade', '')
            demerits = 0
            try:
                demerits = int(r.get('current_demerits', 0))
            except (ValueError, TypeError):
                pass

            fac_key = f"{name}|{address}"
            if fac_key not in facilities:
                facilities[fac_key] = {
                    'name': name,
                    'address': address,
                    'city': city,
                    'zipcode': zipcode,
                    'inspection_date': insp_date,
                    'grade': grade,
                    'demerits': demerits,
                    'violations': [],
                }

    logger.info(f'[vegas_health] {len(facilities)} facilities with recent inspections')
    stats['items_scraped'] = len(facilities)

    # ── Create leads ──
    printed = 0
    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac['address']
        city = fac['city']
        grade = fac['grade']
        demerits = fac['demerits']
        insp_date = fac['inspection_date']
        all_violations = '\n'.join(fac['violations'])
        services = _detect_services(all_violations)

        full_address = f"{address}, {city}, NV" if address else f"{city}, NV"
        if fac['zipcode']:
            full_address += f" {fac['zipcode']}"

        # Urgency based on grade/demerits
        grade_upper = grade.upper() if grade else ''
        if grade_upper in ('C', 'D', 'F', 'X') or demerits >= 40:
            urgency = 'hot'
            urgency_note = f'Grade {grade} / {demerits} demerits — facility at risk of closure'
        elif grade_upper == 'B' or demerits >= 20:
            urgency = 'warm'
            urgency_note = f'Grade {grade} / {demerits} demerits — violations need attention'
        else:
            urgency = 'new'
            urgency_note = 'Violations found during inspection'

        content_parts = [f'HEALTH VIOLATION: {name}']
        if full_address:
            content_parts.append(f'Address: {full_address}')
        if grade:
            content_parts.append(f'Grade: {grade}')
        if demerits:
            content_parts.append(f'Demerits: {demerits}')
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
                print(f'\n  [{city}] {name}')
                print(f'    {full_address}')
                print(f'    Grade: {grade}  Demerits: {demerits}  Urgency: {urgency.upper()}')
                if all_violations:
                    print(f'    Violations: {all_violations[:120]}')
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
