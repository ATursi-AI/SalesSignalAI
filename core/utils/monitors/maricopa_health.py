"""
Phoenix / Maricopa County AZ health inspection monitor for SalesSignal AI.

Maricopa County Environmental Services publishes a weekly inspection report
at envapp.maricopa.gov/Report/WeeklyReport.  Pick any date → get all food
inspections from the prior week.  Reports available for 3 years.

Priority violations = direct risk of foodborne illness.
Priority Foundation = supports Priority items.
Core = general sanitation.

Also scrapes envapp.maricopa.gov/EnvironmentalHealth/FoodInspections/Weekly
for weekly violation listings.

Phoenix is the 5th largest US city.  Mountain time zone.
"""
import logging
import re
from datetime import datetime, timedelta

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

WEEKLY_REPORT_URL = 'https://envapp.maricopa.gov/Report/WeeklyReport'
WEEKLY_LISTING_URL = 'https://envapp.maricopa.gov/EnvironmentalHealth/FoodInspections/Weekly'

VIOLATION_SERVICE_MAP = {
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'cockroach': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'fly': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
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
    'electrical': ['electrician'],
    'trash': ['commercial cleaning', 'waste management'],
    'restroom': ['plumber', 'commercial cleaning'],
    'handwash': ['plumber', 'commercial cleaning'],
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
    for fmt in ['%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%Y', '%b %d, %Y']:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def monitor_maricopa_health(days=7, dry_run=False):
    """
    Monitor Maricopa County (Phoenix) food inspections.

    Scrapes the weekly listing page for recent inspections and violations.
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

    # Fetch weekly listing page
    try:
        resp = requests.get(WEEKLY_LISTING_URL, headers=headers, timeout=30, verify=False)
        if resp.status_code != 200:
            logger.error(f'[maricopa] Weekly listing returned {resp.status_code}')
            stats['errors'] += 1
            return stats
    except Exception as e:
        logger.error(f'[maricopa] Request failed: {e}')
        stats['errors'] += 1
        return stats

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Look for table rows with inspection data
    facilities = {}
    tables = soup.find_all('table')

    for table in tables:
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue

        header_cells = rows[0].find_all(['th', 'td'])
        headers_text = [c.get_text(strip=True).lower() for c in header_cells]

        # Try to find column indices
        name_idx = None
        addr_idx = None
        date_idx = None
        type_idx = None
        violations_idx = None

        for i, h in enumerate(headers_text):
            if any(k in h for k in ['establishment', 'facility', 'restaurant', 'name']):
                name_idx = i
            elif any(k in h for k in ['address', 'location']):
                addr_idx = i
            elif any(k in h for k in ['date', 'inspection']):
                date_idx = i
            elif any(k in h for k in ['type', 'category']):
                type_idx = i
            elif any(k in h for k in ['violation', 'finding', 'result']):
                violations_idx = i

        if name_idx is None:
            continue

        for row in rows[1:]:
            cells = row.find_all('td')
            if len(cells) <= (name_idx or 0):
                continue

            name = cells[name_idx].get_text(strip=True) if name_idx is not None and name_idx < len(cells) else ''
            address = cells[addr_idx].get_text(strip=True) if addr_idx is not None and addr_idx < len(cells) else ''
            date_str = cells[date_idx].get_text(strip=True) if date_idx is not None and date_idx < len(cells) else ''
            violations_text = cells[violations_idx].get_text(strip=True) if violations_idx is not None and violations_idx < len(cells) else ''

            if not name:
                continue

            insp_date = _parse_date(date_str)
            if insp_date and insp_date < cutoff:
                continue

            fac_key = f"{name}|{address}"
            if fac_key not in facilities:
                facilities[fac_key] = {
                    'name': name,
                    'address': address,
                    'inspection_date': insp_date,
                    'violations': [],
                }
            if violations_text:
                facilities[fac_key]['violations'].append(violations_text)

    # Also try parsing any card/div-based layout
    cards = soup.find_all('div', class_=re.compile(r'card|inspection|result|facility', re.I))
    for card in cards:
        name_el = card.find(class_=re.compile(r'name|title|facility', re.I))
        addr_el = card.find(class_=re.compile(r'address|location', re.I))
        date_el = card.find(class_=re.compile(r'date', re.I))
        viol_el = card.find(class_=re.compile(r'violation|finding|priority', re.I))

        name = name_el.get_text(strip=True) if name_el else ''
        address = addr_el.get_text(strip=True) if addr_el else ''
        date_str = date_el.get_text(strip=True) if date_el else ''
        violations_text = viol_el.get_text(strip=True) if viol_el else ''

        if not name:
            continue

        insp_date = _parse_date(date_str)
        if insp_date and insp_date < cutoff:
            continue

        fac_key = f"{name}|{address}"
        if fac_key not in facilities:
            facilities[fac_key] = {
                'name': name,
                'address': address,
                'inspection_date': insp_date,
                'violations': [],
            }
        if violations_text:
            facilities[fac_key]['violations'].append(violations_text)

    logger.info(f'[maricopa] Found {len(facilities)} facilities with violations')
    stats['items_scraped'] = len(facilities)

    printed = 0
    for fac_key, fac in facilities.items():
        name = fac['name']
        address = fac['address']
        insp_date = fac['inspection_date']
        all_violations = '\n'.join(fac['violations'])
        services = _detect_services(all_violations)
        full_address = f"{address}, AZ" if address else 'Phoenix, AZ'

        has_priority = any(
            term in all_violations.lower()
            for term in ['priority', 'critical', 'imminent', 'closure']
        )

        urgency = 'hot' if has_priority else 'warm'
        urgency_note = 'Priority violation — direct foodborne illness risk' if has_priority else 'Violations found during inspection'

        content_parts = [f'HEALTH VIOLATION: {name}']
        if full_address:
            content_parts.append(f'Address: {full_address}')
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
                print(f'\n  [Maricopa] {name}')
                print(f'    {full_address}')
                print(f'    Urgency: {urgency.upper()} — {urgency_note}')
                printed += 1
            stats['created'] += 1
            continue

        try:
            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=WEEKLY_LISTING_URL,
                content=content,
                author='',
                posted_at=insp_date,
                raw_data={
                    'data_source': 'maricopa_county',
                    'business_name': name,
                    'address': full_address,
                    'urgency': urgency,
                    'services_mapped': services,
                },
                state='AZ',
                region='Maricopa County',
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
            logger.error(f'[maricopa] Error processing {name}: {e}')
            stats['errors'] += 1

    logger.info(f'Maricopa health monitor complete: {stats}')
    return stats
