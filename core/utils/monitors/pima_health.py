"""
Tucson / Pima County AZ health inspection monitor for SalesSignal AI.

Portal: healthinspect.pima.gov/portal/
Data confirmed current as of March 31, 2026.

Covers restaurants, food trucks, school cafeterias, hotels, convenience
stores in Tucson and greater Pima County.

Scrapes the web portal search results page for recent inspections.
"""
import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

PORTAL_URL = 'https://healthinspect.pima.gov/portal/'
SEARCH_URL = 'https://healthinspect.pima.gov/portal/Home/Search'
API_URL = 'https://healthinspect.pima.gov/portal/api/inspections'

VIOLATION_SERVICE_MAP = {
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'cockroach': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'mouse': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber'],
    'ventilation': ['HVAC'],
    'exhaust': ['HVAC'],
    'hood': ['commercial kitchen cleaning', 'HVAC'],
    'temperature': ['HVAC', 'refrigeration repair'],
    'cold hold': ['refrigeration repair'],
    'hot hold': ['kitchen equipment repair'],
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
    'glove': ['food safety training'],
    'cross.contamina': ['food safety training', 'commercial cleaning'],
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
        '%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d',
        '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f',
        '%b %d, %Y', '%B %d, %Y',
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _try_api(days):
    """Try JSON API endpoint if it exists."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    for url in [API_URL, f'{PORTAL_URL}api/Search', f'{PORTAL_URL}api/facilities']:
        try:
            resp = requests.get(url, params={
                'startDate': cutoff,
                'pageSize': 500,
            }, headers=headers, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data
                if isinstance(data, dict):
                    items = data.get('results', data.get('data', data.get('items', [])))
                    if items:
                        return items
        except Exception:
            continue
    return None


def monitor_pima_health(days=7, dry_run=False):
    """
    Monitor Pima County (Tucson) food facility inspections.
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
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }

    facilities = {}

    # Try API first
    api_data = _try_api(days)
    if api_data:
        logger.info(f'[pima] Got {len(api_data)} records from API')
        for item in api_data:
            if isinstance(item, dict):
                r = {k.lower(): v for k, v in item.items() if v}
                name = (
                    r.get('facilityname', '') or r.get('facility_name', '')
                    or r.get('name', '') or r.get('establishment', '')
                ).strip()
                if not name:
                    continue

                address = (r.get('address', '') or r.get('location', '')).strip()
                phone = (r.get('phone', '') or r.get('telephone', '')).strip()
                date_str = str(
                    r.get('inspectiondate', '') or r.get('inspection_date', '')
                    or r.get('date', '')
                )
                violations_text = (
                    r.get('violations', '') or r.get('violation_description', '')
                    or r.get('notes', '')
                )
                result = (r.get('result', '') or r.get('status', '') or r.get('disposition', '')).strip()

                insp_date = _parse_date(date_str)
                if insp_date and insp_date < cutoff:
                    continue

                fac_key = f"{name}|{address}"
                if fac_key not in facilities:
                    facilities[fac_key] = {
                        'name': name, 'address': address, 'phone': phone,
                        'inspection_date': insp_date, 'result': result,
                        'violations': [],
                    }
                if violations_text:
                    facilities[fac_key]['violations'].append(str(violations_text))
    else:
        # Fall back to HTML scrape
        logger.info('[pima] API not available, scraping portal HTML')
        try:
            resp = requests.get(PORTAL_URL, headers=headers, timeout=30)
            if resp.status_code != 200:
                stats['errors'] += 1
                return stats

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Parse tables
            for table in soup.find_all('table'):
                rows = table.find_all('tr')
                if len(rows) < 2:
                    continue

                hdr = [c.get_text(strip=True).lower() for c in rows[0].find_all(['th', 'td'])]
                name_idx = addr_idx = date_idx = viol_idx = result_idx = phone_idx = None

                for i, h in enumerate(hdr):
                    if any(k in h for k in ['facility', 'establishment', 'name', 'restaurant']):
                        name_idx = i
                    elif 'address' in h or 'location' in h:
                        addr_idx = i
                    elif 'date' in h:
                        date_idx = i
                    elif 'violation' in h or 'finding' in h:
                        viol_idx = i
                    elif 'result' in h or 'status' in h or 'disposition' in h:
                        result_idx = i
                    elif 'phone' in h:
                        phone_idx = i

                if name_idx is None:
                    continue

                for row in rows[1:]:
                    cells = row.find_all('td')
                    if len(cells) <= name_idx:
                        continue
                    name = cells[name_idx].get_text(strip=True)
                    address = cells[addr_idx].get_text(strip=True) if addr_idx and addr_idx < len(cells) else ''
                    date_str = cells[date_idx].get_text(strip=True) if date_idx and date_idx < len(cells) else ''
                    violations_text = cells[viol_idx].get_text(strip=True) if viol_idx and viol_idx < len(cells) else ''
                    result = cells[result_idx].get_text(strip=True) if result_idx and result_idx < len(cells) else ''
                    phone = cells[phone_idx].get_text(strip=True) if phone_idx and phone_idx < len(cells) else ''

                    if not name:
                        continue
                    insp_date = _parse_date(date_str)
                    if insp_date and insp_date < cutoff:
                        continue

                    fac_key = f"{name}|{address}"
                    if fac_key not in facilities:
                        facilities[fac_key] = {
                            'name': name, 'address': address, 'phone': phone,
                            'inspection_date': insp_date, 'result': result,
                            'violations': [],
                        }
                    if violations_text:
                        facilities[fac_key]['violations'].append(violations_text)

        except Exception as e:
            logger.error(f'[pima] Scrape error: {e}')
            stats['errors'] += 1

    logger.info(f'[pima] {len(facilities)} facilities found')
    stats['items_scraped'] = len(facilities)

    printed = 0
    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac['address']
        phone = fac.get('phone', '')
        insp_date = fac['inspection_date']
        result = fac.get('result', '')
        all_violations = '\n'.join(fac['violations'])
        services = _detect_services(all_violations)
        full_address = f"{address}, Tucson, AZ" if address else 'Tucson, AZ'

        is_failure = any(
            t in (result + all_violations).lower()
            for t in ['fail', 'provisional', 'critical', 'closure', 'imminent']
        )
        urgency = 'hot' if is_failure else 'warm'
        urgency_note = 'Failed/provisional — must fix before re-inspection' if is_failure else 'Violations found'

        content_parts = [f'HEALTH VIOLATION: {name}']
        if full_address:
            content_parts.append(f'Address: {full_address}')
        if phone:
            content_parts.append(f'Phone: {phone}')
        if result:
            content_parts.append(f'Result: {result}')
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
                print(f'\n  [Pima] {name}')
                print(f'    {full_address}')
                if phone:
                    print(f'    Phone: {phone}')
                print(f'    Result: {result}  Urgency: {urgency.upper()}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=PORTAL_URL,
                content=content,
                author='',
                posted_at=insp_date,
                raw_data={
                    'data_source': 'pima_county',
                    'business_name': name,
                    'address': full_address,
                    'phone': phone,
                    'result': result,
                    'urgency': urgency,
                    'services_mapped': services,
                },
                state='AZ',
                region='Pima County',
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
            logger.error(f'[pima] Error processing {name}: {e}')
            stats['errors'] += 1

    logger.info(f'Pima health monitor complete: {stats}')
    return stats
