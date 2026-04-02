"""
NYC restaurant health inspection monitor for SalesSignal AI.

Uses the NYC Open Data SODA API to query DOHMH restaurant inspection results:

  Dataset: 43nn-pn8j (https://data.cityofnewyork.us/resource/43nn-pn8j.json)

Filters:
  - inspection_date >= N days ago
  - violation_code IS NOT NULL
  - inspection_date != '1900-01-01T00:00:00.000' (bad data)

Urgency:
  - critical_flag='Critical' OR score >= 28 = HOT
  - score 14-27 = WARM
  - score < 14 = new

Health violations are high-urgency leads — restaurants risk closure
if they don't fix violations before follow-up inspection.
"""
import logging
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NYC Open Data SODA endpoint
# -------------------------------------------------------------------
SODA_URL = 'https://data.cityofnewyork.us/resource/43nn-pn8j.json'

# Borough mapping
BORO_MAP = {
    'MANHATTAN': 'Manhattan', 'BRONX': 'Bronx', 'BROOKLYN': 'Brooklyn',
    'QUEENS': 'Queens', 'STATEN ISLAND': 'Staten Island',
    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
    '4': 'Queens', '5': 'Staten Island',
}
BORO_FILTER = {
    'manhattan': 'Manhattan', 'bronx': 'Bronx', 'brooklyn': 'Brooklyn',
    'queens': 'Queens', 'staten_island': 'Staten Island',
    'staten island': 'Staten Island',
}

# Violation keywords -> services needed
VIOLATION_SERVICE_MAP = {
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'fly': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
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
    'mold': ['mold remediation'],
    'grease': ['grease trap cleaning', 'commercial kitchen cleaning'],
    'fire': ['fire safety', 'electrician'],
    'electrical': ['electrician'],
    'trash': ['commercial cleaning', 'waste management'],
    'restroom': ['plumber', 'commercial cleaning'],
}

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'HVAC', 'kitchen equipment repair']


def _detect_services(violation_text):
    if not violation_text:
        return DEFAULT_SERVICES
    text_lower = violation_text.lower()
    services = set()
    for key, service_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(service_list)
    return list(services) if services else DEFAULT_SERVICES


def _parse_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _headers():
    h = {}
    token = getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    if token:
        h['X-App-Token'] = token
    return h


def monitor_ny_health_violations(days=30, borough=None, dry_run=False):
    """
    Monitor NYC restaurant health inspections via SODA API.

    Args:
        days: how many days back to search (default: 30)
        borough: filter by borough name (manhattan/bronx/brooklyn/queens)
        dry_run: if True, log matches without creating Lead records

    Returns:
        dict with counts
    """
    stats = {
        'sources_checked': 1,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

    where_parts = [
        f"inspection_date >= '{since}'",
        "violation_code IS NOT NULL",
        "inspection_date != '1900-01-01T00:00:00.000'",
    ]
    if borough:
        boro_val = BORO_FILTER.get(borough.lower(), borough)
        where_parts.append(f"boro = '{boro_val}'")

    params = {
        '$where': ' AND '.join(where_parts),
        '$select': (
            'camis,dba,boro,building,street,zipcode,phone,'
            'cuisine_description,inspection_date,violation_code,'
            'violation_description,critical_flag,score,grade'
        ),
        '$limit': 5000,
        '$order': 'inspection_date DESC',
    }

    logger.info(f'[ny_health] Querying: days={days}, borough={borough or "all"}')

    try:
        resp = requests.get(SODA_URL, params=params, headers=_headers(), timeout=60)
        if resp.status_code != 200:
            logger.error(f'[ny_health] SODA API returned {resp.status_code}')
            stats['errors'] += 1
            return stats
        data = resp.json()
    except Exception as e:
        logger.error(f'[ny_health] SODA API error: {e}')
        stats['errors'] += 1
        return stats

    if not isinstance(data, list):
        logger.error('[ny_health] Unexpected API response format')
        stats['errors'] += 1
        return stats

    logger.info(f'[ny_health] Fetched {len(data)} violation records')
    stats['items_scraped'] = len(data)

    # Group violations by restaurant (CAMIS = unique restaurant ID)
    restaurants = {}
    for record in data:
        camis = record.get('camis', '')
        if not camis:
            continue

        if camis not in restaurants:
            boro_raw = str(record.get('boro', '')).strip().upper()
            boro_name = BORO_MAP.get(boro_raw, boro_raw)
            building = record.get('building', '').strip()
            street = record.get('street', '').strip()
            zipcode = record.get('zipcode', '').strip()
            addr_parts = []
            if building and street:
                addr_parts.append(f'{building} {street}')
            elif street:
                addr_parts.append(street)
            if boro_name:
                addr_parts.append(boro_name)
            addr_parts.append('NY')
            if zipcode:
                addr_parts.append(zipcode)

            restaurants[camis] = {
                'camis': camis,
                'dba': record.get('dba', '').strip(),
                'address': ', '.join(addr_parts),
                'boro': boro_name,
                'zipcode': zipcode,
                'phone': record.get('phone', '').strip(),
                'cuisine': record.get('cuisine_description', '').strip(),
                'inspection_date': record.get('inspection_date', ''),
                'score': record.get('score', ''),
                'grade': record.get('grade', ''),
                'violations': [],
                'has_critical': False,
            }

        violation_desc = record.get('violation_description', '').strip()
        violation_code = record.get('violation_code', '').strip()
        critical_flag = record.get('critical_flag', '').strip()

        if violation_desc or violation_code:
            is_critical = critical_flag.lower() == 'critical'
            restaurants[camis]['violations'].append({
                'code': violation_code,
                'description': violation_desc,
                'critical': is_critical,
            })
            if is_critical:
                restaurants[camis]['has_critical'] = True

    logger.info(f'[ny_health] Grouped into {len(restaurants)} restaurants')

    # Process each restaurant
    printed = 0
    for camis, rest in restaurants.items():
        dba = rest['dba']
        if not dba or not rest['violations']:
            continue

        address = rest['address']
        inspection_date = _parse_date(rest['inspection_date'])
        score_str = rest['score']
        grade = rest['grade']
        cuisine = rest['cuisine']
        phone = rest['phone']
        has_critical = rest['has_critical']
        violations = rest['violations']

        # Parse score for urgency
        try:
            score = int(score_str)
        except (ValueError, TypeError):
            score = 0

        # Urgency scoring
        if has_critical or score >= 28:
            urgency = 'hot'
            urgency_note = 'CRITICAL violation or score >= 28 — restaurant risks closure'
        elif score >= 14:
            urgency = 'warm'
            urgency_note = f'Score {score} — must fix before follow-up inspection'
        else:
            urgency = 'new'
            urgency_note = 'Minor violations'

        critical_count = sum(1 for v in violations if v.get('critical'))
        total_violations = len(violations)

        all_violation_text = ' '.join(v.get('description', '') for v in violations)
        services = _detect_services(all_violation_text)

        content_parts = [
            f'HEALTH VIOLATION: {dba}',
        ]
        if cuisine:
            content_parts.append(f'Cuisine: {cuisine}')
        if address:
            content_parts.append(f'Address: {address}')
        if phone:
            content_parts.append(f'Phone: {phone}')
        if score_str:
            content_parts.append(f'Score: {score_str}')
        if grade:
            content_parts.append(f'Grade: {grade}')
        if inspection_date:
            days_ago = (timezone.now() - inspection_date).days
            content_parts.append(f'Inspected: {days_ago} days ago')
        content_parts.append(f'Violations: {total_violations} total ({critical_count} critical)')

        for v in violations[:5]:
            desc = v.get('description', '')
            if desc:
                prefix = '[CRITICAL] ' if v.get('critical') else ''
                content_parts.append(f'  - {prefix}{desc[:200]}')

        content_parts.append(f'Urgency: {urgency_note}')
        content_parts.append(f'Services needed: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 5:
                boro = rest['boro']
                print(f'\n  [{boro}] {dba}')
                print(f'    Address: {address}')
                print(f'    Score: {score_str}  Grade: {grade}  Violations: {total_violations} ({critical_count} critical)')
                print(f'    Urgency: {urgency.upper()} — {urgency_note}')
                if phone:
                    print(f'    Phone: {phone}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=SODA_URL,
                content=content,
                author='',
                posted_at=inspection_date,
                raw_data={
                    'data_source': 'nyc_dohmh',
                    'camis': camis,
                    'business_name': dba,
                    'address': address,
                    'cuisine': cuisine,
                    'score': score_str,
                    'grade': grade,
                    'critical_count': critical_count,
                    'total_violations': total_violations,
                    'has_critical': has_critical,
                    'urgency': urgency,
                    'phone': phone,
                    'services_mapped': services,
                },
                state='NY',
                region=rest['boro'],
                source_group='public_records',
                source_type='health_inspections',
                contact_business=dba,
                contact_phone=phone,
                contact_address=address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[ny_health] Error processing {dba}: {e}')
            stats['errors'] += 1

    logger.info(f'NY health violations monitor complete: {stats}')
    return stats
