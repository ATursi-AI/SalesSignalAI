"""
NY State restaurant health inspection monitor for SalesSignal AI.

Uses the NY Health Data SODA API to query statewide food service inspections:

  Dataset: hjxk-iw5g (https://health.data.ny.gov/resource/hjxk-iw5g.json)

This dataset covers ALL of New York State EXCEPT:
  - New York City (covered by 43nn-pn8j in ny_health_violations.py)
  - Suffolk County
  - Erie County

Key advantage over the NYC dataset: has operator first/last name and
permitted corporation name — useful for contact enrichment.

No phone field, but has: operation_name, permitted_d_b_a, permitted_corp_name,
perm_operator_first_name, perm_operator_last_name, county, city, zip_code.

Filters:
  - date >= N days ago
  - total_critical_violations > 0 OR total_noncritical_violations > 0
"""
import logging
from datetime import datetime, timedelta

import requests
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NY Health Data SODA endpoint
# -------------------------------------------------------------------
SODA_URL = 'https://health.data.ny.gov/resource/hjxk-iw5g.json'

# Target counties (outside NYC, Suffolk, Erie — those have their own monitors)
DEFAULT_COUNTIES = [
    'Nassau', 'Westchester', 'Rockland', 'Orange', 'Dutchess',
    'Putnam', 'Sullivan', 'Ulster', 'Columbia', 'Albany',
    'Saratoga', 'Schenectady', 'Rensselaer', 'Onondaga',
    'Monroe', 'Oneida', 'Broome', 'Tompkins',
]

# County -> display region
COUNTY_REGIONS = {
    'Nassau': 'Nassau County',
    'Westchester': 'Westchester',
    'Rockland': 'Rockland County',
    'Orange': 'Orange County',
    'Dutchess': 'Dutchess County',
    'Putnam': 'Putnam County',
    'Sullivan': 'Sullivan County',
    'Ulster': 'Ulster County',
    'Columbia': 'Columbia County',
    'Albany': 'Albany',
    'Saratoga': 'Saratoga County',
    'Schenectady': 'Schenectady',
    'Rensselaer': 'Rensselaer County',
    'Onondaga': 'Syracuse',
    'Monroe': 'Rochester',
    'Oneida': 'Utica',
    'Broome': 'Binghamton',
    'Tompkins': 'Ithaca',
}

# Violation description keywords -> services needed
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
    'refriger': ['refrigeration repair'],
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
    date_str = date_str.strip()
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def monitor_nys_health_inspections(county=None, days=30, dry_run=False):
    """
    Monitor NY State food service inspections via SODA API.

    Covers restaurants outside NYC that have violations.

    Args:
        county: single county name, comma-separated list, or None for defaults
        days: lookback period in days (default: 30)
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

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # Determine counties
    if county and county.lower() != 'all':
        counties = [c.strip().title() for c in county.split(',')]
    else:
        counties = DEFAULT_COUNTIES

    counties_str = ','.join(f"'{c.upper()}'" for c in counties)

    where = (
        f"date >= '{since}' "
        f"AND (total_critical_violations > 0 OR total_noncritical_violations > 0) "
        f"AND upper(county) in({counties_str})"
    )

    params = {
        '$where': where,
        '$select': (
            'nys_health_operation_id,operation_name,permitted_d_b_a,'
            'permitted_corp_name,perm_operator_first_name,'
            'perm_operator_last_name,facility_address,city,county,'
            'zip_code,date,violations,total_critical_violations,'
            'total_crit_not_corrected,total_noncritical_violations,'
            'inspection_type,inspection_comments,description'
        ),
        '$limit': 2000,
        '$order': 'date DESC',
    }

    logger.info(f'[nys_health] Querying: counties={len(counties)}, days={days}')

    try:
        resp = requests.get(SODA_URL, params=params, timeout=60)
        if resp.status_code != 200:
            logger.error(f'[nys_health] SODA API returned {resp.status_code}')
            stats['errors'] += 1
            return stats
        data = resp.json()
    except Exception as e:
        logger.error(f'[nys_health] SODA API error: {e}')
        stats['errors'] += 1
        return stats

    if not isinstance(data, list):
        logger.error('[nys_health] Unexpected API response format')
        stats['errors'] += 1
        return stats

    logger.info(f'[nys_health] Fetched {len(data)} inspection records')
    stats['items_scraped'] = len(data)

    # Group by operation ID to consolidate multiple violations per restaurant
    restaurants = {}
    for rec in data:
        op_id = rec.get('nys_health_operation_id', '')
        if not op_id:
            continue

        if op_id not in restaurants:
            dba = (rec.get('permitted_d_b_a') or rec.get('operation_name') or '').strip()
            corp_name = (rec.get('permitted_corp_name') or '').strip()
            op_first = (rec.get('perm_operator_first_name') or '').strip()
            op_last = (rec.get('perm_operator_last_name') or '').strip()
            operator_name = f'{op_first} {op_last}'.strip()

            facility_addr = (rec.get('facility_address') or '').strip()
            city = (rec.get('city') or '').strip()
            county_name = (rec.get('county') or '').strip().title()
            zipcode = (rec.get('zip_code') or '').strip()
            description = (rec.get('description') or '').strip()

            addr_parts = [facility_addr] if facility_addr else []
            if city:
                addr_parts.append(city)
            addr_parts.append('NY')
            if zipcode:
                addr_parts.append(zipcode)

            restaurants[op_id] = {
                'dba': dba,
                'corp_name': corp_name,
                'operator_name': operator_name,
                'address': ', '.join(addr_parts),
                'city': city,
                'county': county_name,
                'zipcode': zipcode,
                'description': description,
                'inspection_date': rec.get('date', ''),
                'total_critical': 0,
                'total_noncritical': 0,
                'total_not_corrected': 0,
                'violations_text': [],
            }

        # Accumulate violation counts
        try:
            restaurants[op_id]['total_critical'] += int(rec.get('total_critical_violations') or 0)
        except (ValueError, TypeError):
            pass
        try:
            restaurants[op_id]['total_noncritical'] += int(rec.get('total_noncritical_violations') or 0)
        except (ValueError, TypeError):
            pass
        try:
            restaurants[op_id]['total_not_corrected'] += int(rec.get('total_crit_not_corrected') or 0)
        except (ValueError, TypeError):
            pass

        violations = (rec.get('violations') or '').strip()
        if violations and violations != 'No violations found.':
            restaurants[op_id]['violations_text'].append(violations)

        comments = (rec.get('inspection_comments') or '').strip()
        if comments:
            restaurants[op_id]['violations_text'].append(comments)

    logger.info(f'[nys_health] Grouped into {len(restaurants)} restaurants')

    printed = 0
    for op_id, rest in restaurants.items():
        dba = rest['dba']
        if not dba:
            continue

        address = rest['address']
        county_name = rest['county']
        operator_name = rest['operator_name']
        corp_name = rest['corp_name']
        inspection_date = _parse_date(rest['inspection_date'])
        critical = rest['total_critical']
        noncritical = rest['total_noncritical']
        not_corrected = rest['total_not_corrected']
        total = critical + noncritical
        all_violations = '\n'.join(rest['violations_text'][:5])

        if total == 0:
            continue

        services = _detect_services(all_violations)
        region = COUNTY_REGIONS.get(county_name, county_name)

        # Urgency
        if critical > 0 or not_corrected > 0:
            urgency = 'hot'
            urgency_note = f'{critical} critical violation(s) — restaurant risks closure'
        elif noncritical >= 3:
            urgency = 'warm'
            urgency_note = f'{noncritical} non-critical violations — must fix before re-inspection'
        else:
            urgency = 'new'
            urgency_note = 'Minor violations found'

        # Build content
        content_parts = [
            f'HEALTH VIOLATION: {dba}',
        ]
        if rest['description']:
            content_parts.append(f'Type: {rest["description"]}')
        content_parts.append(f'Address: {address}')
        content_parts.append(f'County: {county_name}')
        if operator_name:
            content_parts.append(f'Operator: {operator_name}')
        if corp_name and corp_name != dba:
            content_parts.append(f'Corporation: {corp_name}')
        if inspection_date:
            days_ago = (timezone.now() - inspection_date).days
            content_parts.append(f'Inspected: {days_ago} days ago')
        content_parts.append(f'Violations: {total} total ({critical} critical, {not_corrected} not corrected)')
        if all_violations:
            for v in rest['violations_text'][:3]:
                content_parts.append(f'  - {v[:200]}')
        content_parts.append(f'Urgency: {urgency_note}')
        content_parts.append(f'Services needed: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 5:
                print(f'\n  [{county_name}] {dba}')
                print(f'    {address}')
                if operator_name:
                    print(f'    Operator: {operator_name}')
                print(f'    Violations: {total} ({critical} critical)')
                print(f'    Urgency: {urgency.upper()}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            contact_name = operator_name or ''
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=SODA_URL,
                content=content,
                author='',
                posted_at=inspection_date,
                raw_data={
                    'data_source': 'nys_health_inspections',
                    'operation_id': op_id,
                    'business_name': dba,
                    'corp_name': corp_name,
                    'operator_name': operator_name,
                    'address': address,
                    'county': county_name,
                    'critical_violations': critical,
                    'noncritical_violations': noncritical,
                    'not_corrected': not_corrected,
                    'urgency': urgency,
                    'services_mapped': services,
                },
                state='NY',
                region=region,
                source_group='public_records',
                source_type='health_inspections',
                contact_name=contact_name,
                contact_business=dba,
                contact_address=address,
            )
            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1
        except Exception as e:
            logger.error(f'[nys_health] Error processing {dba}: {e}')
            stats['errors'] += 1

    logger.info(f'NYS Health Inspections monitor complete: {stats}')
    return stats
