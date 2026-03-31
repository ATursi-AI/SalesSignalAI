"""
Las Vegas / Clark County NV health inspection monitor for SalesSignal AI.

Southern Nevada Health District publishes a nightly CSV of ALL food
establishment inspections.  Data is posted ~5 business days after
the inspection.

Developer page:
  https://www.southernnevadahealthdistrict.org/permits-and-regulations/
  restaurant-inspections/developers/

Covers: restaurants, bars, hotels, buffets, food trucks, convenience
stores across Las Vegas, Henderson, North Las Vegas, Boulder City.

Violation → service mapping follows the same pattern as NYC.
"""
import csv
import io
import logging
from datetime import datetime, timedelta

import requests
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# PRIMARY: SNHD Socrata open data portal
SNHD_SOCRATA_URL = 'https://data.snhd.org/resource/96em-8cmh.json'
# Fallback: ArcGIS Las Vegas open data portal
ARCGIS_URL = 'https://services.arcgis.com/YSN1DaSBQGSIVjMd/arcgis/rest/services/SNHD_Restaurant_Inspections/FeatureServer/0/query'
# CSV fallback (nightly dump from SNHD developers page)
SNHD_CSV_URL = 'https://www.southernnevadahealthdistrict.org/permits-and-regulations/restaurant-inspections/developers/download/'

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
    date_str = date_str.strip()
    for fmt in [
        '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d',
        '%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y',
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _fetch_socrata(days):
    """PRIMARY: Query SNHD Socrata open data portal."""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
    params = {
        '$where': f"inspection_date >= '{since}'",
        '$limit': 5000,
        '$order': 'inspection_date DESC',
    }
    try:
        resp = requests.get(SNHD_SOCRATA_URL, params=params, timeout=60,
                            headers={'User-Agent': 'SalesSignalAI/1.0'})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                logger.info(f'[vegas_health] Socrata returned {len(data)} records')
                return data
            # If date filter returns empty, try without filter to check field names
            logger.info('[vegas_health] Socrata date filter empty, trying $limit only')
            resp2 = requests.get(SNHD_SOCRATA_URL, params={'$limit': 5}, timeout=30,
                                 headers={'User-Agent': 'SalesSignalAI/1.0'})
            if resp2.status_code == 200:
                sample = resp2.json()
                if isinstance(sample, list) and sample:
                    # Log field names so we can fix the date filter
                    logger.info(f'[vegas_health] Sample fields: {list(sample[0].keys())}')
                    # Try common date field names
                    for date_field in ['inspection_date', 'inspectiondate', 'inspection_time',
                                       'date', 'activity_date', 'insp_date']:
                        if date_field in sample[0]:
                            logger.info(f'[vegas_health] Found date field: {date_field}, retrying')
                            retry_params = {
                                '$where': f"{date_field} >= '{since}'",
                                '$limit': 5000,
                                '$order': f'{date_field} DESC',
                            }
                            resp3 = requests.get(SNHD_SOCRATA_URL, params=retry_params,
                                                 timeout=60, headers={'User-Agent': 'SalesSignalAI/1.0'})
                            if resp3.status_code == 200:
                                data3 = resp3.json()
                                if isinstance(data3, list) and data3:
                                    return data3
                            break
                    # Return the sample so we at least get some data and can debug
                    return sample
    except Exception as e:
        logger.warning(f'[vegas_health] Socrata error: {e}')
    return []


def _fetch_arcgis(days):
    """Fallback: query ArcGIS open data endpoint."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    params = {
        'where': f"Inspection_Date >= '{cutoff}'",
        'outFields': '*',
        'f': 'json',
        'resultRecordCount': 5000,
        'orderByFields': 'Inspection_Date DESC',
    }
    try:
        resp = requests.get(ARCGIS_URL, params=params, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            features = data.get('features', [])
            if features:
                logger.info(f'[vegas_health] ArcGIS returned {len(features)} features')
                return [f.get('attributes', f) for f in features]
    except Exception as e:
        logger.warning(f'[vegas_health] ArcGIS fallback failed: {e}')
    return []


def _fetch_csv(days):
    """Last resort: SNHD developer CSV download."""
    try:
        resp = requests.get(SNHD_CSV_URL, timeout=60, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception as e:
        logger.warning(f'[vegas_health] CSV download failed: {e}')
    return None


def monitor_vegas_health(days=7, dry_run=False):
    """
    Monitor Southern Nevada Health District restaurant inspections.

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
    records = []

    # Try Socrata API first (most reliable)
    socrata_data = _fetch_socrata(days)
    if socrata_data:
        logger.info(f'[vegas_health] Using Socrata data ({len(socrata_data)} records)')
        records = socrata_data
    else:
        # Try ArcGIS
        logger.info('[vegas_health] Trying ArcGIS fallback')
        arcgis_data = _fetch_arcgis(days)
        if arcgis_data:
            records = arcgis_data
        else:
            # Last resort: CSV download
            logger.info('[vegas_health] Trying CSV fallback')
            csv_text = _fetch_csv(days)
            if csv_text:
                reader = csv.DictReader(io.StringIO(csv_text))
                for row in reader:
                    records.append(row)

    logger.info(f'[vegas_health] Fetched {len(records)} total records')

    # Normalize field names (SNHD CSV uses various casing)
    facilities = {}
    for row in records:
        # Normalize keys to lowercase
        r = {k.lower().strip(): v for k, v in row.items() if v}

        name = (
            r.get('restaurant_name', '') or r.get('facility_name', '')
            or r.get('name', '') or r.get('establishment_name', '')
            or r.get('dba', '')
        ).strip()
        if not name:
            continue

        address = (
            r.get('address', '') or r.get('location_address', '')
            or r.get('site_address', '')
        ).strip()
        city = (r.get('city', '') or r.get('city_name', '') or 'Las Vegas').strip()
        zipcode = (r.get('zip', '') or r.get('zipcode', '') or r.get('zip_code', '')).strip()
        phone = (r.get('phone', '') or r.get('telephone', '') or r.get('phone_number', '')).strip()

        insp_date_str = (
            r.get('inspection_date', '') or r.get('inspectiondate', '')
            or r.get('inspection_time', '') or r.get('date', '')
        )
        insp_date = _parse_date(str(insp_date_str))
        if insp_date and insp_date < cutoff:
            continue

        grade = (r.get('grade', '') or r.get('current_grade', '') or r.get('inspection_grade', '')).strip()
        violations_text = (
            r.get('violations', '') or r.get('violation_description', '')
            or r.get('violation', '') or r.get('demerits', '')
        )

        demerits = 0
        for k, v in r.items():
            if 'demerit' in k.lower():
                try:
                    demerits = int(v)
                except (ValueError, TypeError):
                    pass

        fac_key = f"{name}|{address}"
        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name,
                'address': address,
                'city': city,
                'zipcode': zipcode,
                'phone': phone,
                'inspection_date': insp_date,
                'grade': grade,
                'demerits': demerits,
                'violations': [],
            }

        if violations_text:
            facilities[fac_key]['violations'].append(str(violations_text))

    logger.info(f'[vegas_health] {len(facilities)} facilities after grouping')
    stats['items_scraped'] = len(facilities)

    printed = 0
    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac['address']
        city = fac['city']
        phone = fac['phone']
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
        if phone:
            content_parts.append(f'Phone: {phone}')
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
                if phone:
                    print(f'    Phone: {phone}')
                print(f'    Grade: {grade}  Demerits: {demerits}  Urgency: {urgency.upper()}')
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
                    'phone': phone,
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
                contact_phone=phone,
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
