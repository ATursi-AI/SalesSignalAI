"""
Multi-jurisdiction health inspection monitor via myhealthdepartment.com

This single scraper covers ALL jurisdictions that use the myhealthdepartment.com
platform.  Each jurisdiction has its own subdomain/path.

Confirmed active jurisdictions (as of March 2026):
  - Denver (Colorado):     inspections.myhealthdepartment.com/colorado
  - Portland (Multnomah):  inspections.myhealthdepartment.com/multco-eh
  - Colorado Springs:      inspections.myhealthdepartment.com/epcph
  - Honolulu (Hawaii DOH): inspections.myhealthdepartment.com/soh
  - Sacramento County:     inspections.myhealthdepartment.com/sacramento

The platform uses a JS-rendered frontend but inspection data can often be
fetched via their internal API or by scraping search results with date filters.
"""
import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Jurisdiction configs: (name, url_slug, state, region, timezone_offset)
JURISDICTIONS = {
    'denver': {
        'name': 'Denver / Colorado',
        'base_url': 'https://inspections.myhealthdepartment.com/colorado',
        'state': 'CO',
        'region': 'Denver',
    },
    'portland': {
        'name': 'Portland / Multnomah County',
        'base_url': 'https://inspections.myhealthdepartment.com/multco-eh',
        'state': 'OR',
        'region': 'Multnomah County',
    },
    'colorado_springs': {
        'name': 'Colorado Springs / El Paso County',
        'base_url': 'https://inspections.myhealthdepartment.com/epcph',
        'state': 'CO',
        'region': 'Colorado Springs',
    },
    'honolulu': {
        'name': 'Honolulu / Hawaii DOH',
        'base_url': 'https://inspections.myhealthdepartment.com/soh',
        'state': 'HI',
        'region': 'Honolulu',
    },
    'sacramento': {
        'name': 'Sacramento County',
        'base_url': 'https://inspections.myhealthdepartment.com/sacramento',
        'state': 'CA',
        'region': 'Sacramento',
    },
}

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
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in [
        '%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f', '%b %d, %Y', '%B %d, %Y',
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _try_api(base_url, days):
    """Try common API patterns used by myhealthdepartment.com."""
    cutoff_str = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }

    api_patterns = [
        f'{base_url}/api/inspections',
        f'{base_url}/api/search',
        f'{base_url}/api/facilities',
        f'{base_url}/api/v1/inspections',
    ]

    for api_url in api_patterns:
        try:
            resp = requests.get(api_url, params={
                'startDate': cutoff_str,
                'endDate': datetime.now().strftime('%Y-%m-%d'),
                'pageSize': 500,
                'page': 1,
            }, headers=headers, timeout=20)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get('results', data.get('data', data.get('items', [])))
                    if items and len(items) > 0:
                        return items
                except Exception:
                    pass
        except Exception:
            continue

    return None


def _scrape_html(base_url, days):
    """Scrape the HTML search results page."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }

    facilities = []

    try:
        resp = requests.get(base_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Look for facility cards/rows
        for el in soup.find_all(['tr', 'div', 'li'], class_=re.compile(
            r'facility|inspection|result|establishment|search-result', re.I
        )):
            name_el = el.find(class_=re.compile(r'name|title|facility', re.I))
            addr_el = el.find(class_=re.compile(r'address|location', re.I))
            date_el = el.find(class_=re.compile(r'date', re.I))
            score_el = el.find(class_=re.compile(r'score|grade|rating', re.I))
            viol_el = el.find(class_=re.compile(r'violation|finding|item', re.I))

            name = name_el.get_text(strip=True) if name_el else ''
            if not name:
                continue

            facilities.append({
                'name': name,
                'address': addr_el.get_text(strip=True) if addr_el else '',
                'date': date_el.get_text(strip=True) if date_el else '',
                'score': score_el.get_text(strip=True) if score_el else '',
                'violations': viol_el.get_text(strip=True) if viol_el else '',
            })

        # Also check tables
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue
            hdr = [c.get_text(strip=True).lower() for c in rows[0].find_all(['th', 'td'])]
            name_idx = next((i for i, h in enumerate(hdr)
                             if any(k in h for k in ['name', 'facility', 'establishment'])), None)
            if name_idx is None:
                continue

            addr_idx = next((i for i, h in enumerate(hdr) if 'address' in h or 'location' in h), None)
            date_idx = next((i for i, h in enumerate(hdr) if 'date' in h), None)
            score_idx = next((i for i, h in enumerate(hdr) if 'score' in h or 'grade' in h or 'rating' in h), None)
            viol_idx = next((i for i, h in enumerate(hdr) if 'violation' in h or 'finding' in h), None)

            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) <= name_idx:
                    continue
                name = cells[name_idx].get_text(strip=True)
                if not name:
                    continue
                facilities.append({
                    'name': name,
                    'address': cells[addr_idx].get_text(strip=True) if addr_idx and addr_idx < len(cells) else '',
                    'date': cells[date_idx].get_text(strip=True) if date_idx and date_idx < len(cells) else '',
                    'score': cells[score_idx].get_text(strip=True) if score_idx and score_idx < len(cells) else '',
                    'violations': cells[viol_idx].get_text(strip=True) if viol_idx and viol_idx < len(cells) else '',
                })

    except Exception as e:
        logger.warning(f'HTML scrape error for {base_url}: {e}')

    return facilities


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

    base_url = config['base_url']
    state = config['state']
    region = config['region']
    cutoff = timezone.now() - timedelta(days=days)

    logger.info(f'[myhealthdept] Monitoring {config["name"]}, days={days}')

    facilities = {}

    # Try API first
    api_data = _try_api(base_url, days)
    if api_data:
        logger.info(f'[myhealthdept] Got {len(api_data)} from API')
        for item in api_data:
            if not isinstance(item, dict):
                continue
            r = {k.lower(): v for k, v in item.items() if v}
            name = (
                r.get('facilityname', '') or r.get('facility_name', '')
                or r.get('name', '') or r.get('establishment', '')
            ).strip()
            if not name:
                continue

            address = (r.get('address', '') or r.get('location', '')).strip()
            phone = (r.get('phone', '') or r.get('telephone', '')).strip()
            insp_date = _parse_date(
                r.get('inspectiondate', '') or r.get('inspection_date', '')
                or r.get('date', '')
            )
            score = r.get('score', r.get('totalscore', ''))
            violations = (
                r.get('violations', '') or r.get('violation_description', '')
                or r.get('items', '')
            )
            result = r.get('result', '') or r.get('status', '')

            if insp_date and insp_date < cutoff:
                continue

            full_addr = f"{address}, {state}" if address else f"{region}, {state}"
            fac_key = f"{name}|{full_addr}"
            if fac_key not in facilities:
                facilities[fac_key] = {
                    'name': name, 'address': full_addr, 'phone': phone,
                    'inspection_date': insp_date, 'score': score, 'grade': '',
                    'result': result, 'violations': [],
                }
            if violations:
                facilities[fac_key]['violations'].append(str(violations))
    else:
        # Fallback to HTML scraping
        logger.info(f'[myhealthdept] API unavailable, scraping HTML')
        raw = _scrape_html(base_url, days)
        for item in raw:
            name = item.get('name', '').strip()
            if not name:
                continue
            insp_date = _parse_date(item.get('date', ''))
            if insp_date and insp_date < cutoff:
                continue

            address = item.get('address', '')
            full_addr = f"{address}, {state}" if address else f"{region}, {state}"
            fac_key = f"{name}|{full_addr}"
            if fac_key not in facilities:
                facilities[fac_key] = {
                    'name': name, 'address': full_addr, 'phone': '',
                    'inspection_date': insp_date, 'score': item.get('score', ''),
                    'grade': '', 'result': '', 'violations': [],
                }
            if item.get('violations'):
                facilities[fac_key]['violations'].append(item['violations'])

    logger.info(f'[myhealthdept] {len(facilities)} facilities for {config["name"]}')
    stats['items_scraped'] = len(facilities)

    printed = 0
    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac['address']
        phone = fac.get('phone', '')
        insp_date = fac['inspection_date']
        score = fac.get('score', '')
        all_violations = '\n'.join(fac.get('violations', []))
        result = fac.get('result', '')
        services = _detect_services(all_violations)

        is_failure = any(
            t in (str(result) + all_violations).lower()
            for t in ['critical', 'priority', 'fail', 'closure', 'imminent', 'unsatisfactory']
        )
        urgency = 'hot' if is_failure else 'warm'
        urgency_note = 'Critical/priority violation' if is_failure else 'Violations found during inspection'

        content_parts = [f'HEALTH VIOLATION: {name}']
        if address:
            content_parts.append(f'Address: {address}')
        if phone:
            content_parts.append(f'Phone: {phone}')
        if score:
            content_parts.append(f'Score: {score}')
        if result:
            content_parts.append(f'Result: {result}')
        if insp_date:
            days_ago = (timezone.now() - insp_date).days
            content_parts.append(f'Inspected: {days_ago} days ago')
        if all_violations:
            content_parts.append(f'Violations: {all_violations[:500]}')
        content_parts.append(f'Jurisdiction: {config["name"]}')
        content_parts.append(f'Urgency: {urgency_note}')
        content_parts.append(f'Services needed: {", ".join(services[:6])}')
        content = '\n'.join(content_parts)

        if dry_run:
            if printed < 10:
                print(f'\n  [{config["name"]}] {name}')
                print(f'    {address}')
                if phone:
                    print(f'    Phone: {phone}')
                print(f'    Score: {score}  Urgency: {urgency.upper()}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=base_url,
                content=content,
                author='',
                posted_at=insp_date,
                raw_data={
                    'data_source': f'myhealthdept_{jurisdiction}',
                    'business_name': name,
                    'address': address,
                    'phone': phone,
                    'score': score,
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
