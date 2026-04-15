"""
Montgomery County Housing Violations Monitor
API: https://data.montgomerycountymd.gov/resource/k9nj-z35d.json  (Socrata SODA)
Dataset: Housing code violations from Montgomery County, MD

Rich fields:
  - case_number, violation_id, date_filed, date_assigned, date_closed
  - disposition, street_address, unit_number, city, zip_code
  - inspection_date, location_description, action, code_reference
  - condition, item, latitude, longitude
"""
import logging
import re
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.montgomerycountymd.gov/resource/k9nj-z35d.json'

VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber', 'sewer service'],
    'hot water': ['plumber'],
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'rat': ['pest control', 'exterminator'],
    'ventilation': ['HVAC'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'refriger': ['commercial refrigeration'],
    'cooler': ['commercial refrigeration'],
    'freezer': ['commercial refrigeration'],
    'hood': ['hood cleaning', 'HVAC'],
    'grease': ['hood cleaning', 'commercial cleaning'],
    'fire': ['fire safety'],
    'extinguisher': ['fire safety'],
    'suppression': ['fire safety'],
    'floor': ['general contractor', 'commercial cleaning'],
    'wall': ['general contractor'],
    'ceiling': ['general contractor'],
    'door': ['general contractor'],
    'window': ['general contractor'],
    'mold': ['mold remediation'],
    'clean': ['commercial cleaning', 'deep cleaning'],
    'sanitiz': ['commercial cleaning'],
    'trash': ['waste management'],
    'garbage': ['waste management'],
    'dumpster': ['waste management'],
    'paint': ['painter'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'light': ['electrician'],
    'roof': ['roofing'],
    'gutter': ['gutter cleaning', 'roofing'],
    'foundation': ['foundation repair'],
    'crack': ['foundation repair', 'concrete'],
    'basement': ['basement waterproofing', 'general contractor'],
    'moisture': ['mold remediation', 'basement waterproofing'],
    'damp': ['mold remediation', 'basement waterproofing'],
    'bath': ['plumber', 'general contractor'],
    'kitchen': ['plumber', 'general contractor'],
    'appliance': ['appliance repair'],
    'wood': ['carpenter', 'general contractor'],
    'structural': ['general contractor', 'structural engineer'],
    'unsafe': ['general contractor', 'building inspector'],
}

DEFAULT_SERVICES = ['general contractor', 'housing code specialist', 'property maintenance']


def _detect_services(text):
    if not text:
        return DEFAULT_SERVICES
    text_lower = text.lower()
    services = set()
    for key, svc_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


class Command(BaseCommand):
    help = 'Monitor Montgomery County Housing Violations (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='mc_housing_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on open violations (not closed)
        params = {
            '$where': (
                f"date_filed >= '{since}' AND date_closed IS NULL"
            ),
            '$select': (
                'case_number,date_filed,date_assigned,date_closed,disposition,'
                'street_address,unit_number,city,zip_code,'
                'violation_id,inspection_date,location_description,action,'
                'code_reference,condition,item,latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'date_filed DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  MONTGOMERY COUNTY HOUSING VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} housing violations from Montgomery County")

            # Group violations by street address (multiple violations at same address = one lead)
            violations_by_address = {}
            for rec in records:
                street_address = (rec.get('street_address', '') or '').strip()
                if not street_address:
                    continue
                if street_address not in violations_by_address:
                    violations_by_address[street_address] = []
                violations_by_address[street_address].append(rec)

            # Process grouped violations
            for street_address, address_violations in violations_by_address.items():
                city = (address_violations[0].get('city', '') or '').strip()
                zip_code = (address_violations[0].get('zip_code', '') or '').strip()
                unit_number = (address_violations[0].get('unit_number', '') or '').strip()

                full_addr = f"{street_address}, {city}, MD {zip_code}".strip()
                display_name = street_address
                if unit_number:
                    display_name = f"{street_address} Unit {unit_number}"

                # Collect all violation details for service detection
                violation_texts = []
                action_texts = []
                code_refs = []
                conditions = []

                for v in address_violations:
                    action = (v.get('action', '') or '').strip()
                    code_ref = (v.get('code_reference', '') or '').strip()
                    condition = (v.get('condition', '') or '').strip()
                    item = (v.get('item', '') or '').strip()
                    location_desc = (v.get('location_description', '') or '').strip()

                    if action:
                        action_texts.append(action)
                    if code_ref:
                        code_refs.append(code_ref)
                    if condition:
                        conditions.append(condition)
                    if item:
                        violation_texts.append(item)
                    if location_desc:
                        violation_texts.append(location_desc)

                # Combine all text for service detection
                combined_text = ' '.join(
                    violation_texts + action_texts + conditions
                )
                services = _detect_services(combined_text)

                # Urgency logic
                num_violations = len(address_violations)
                has_immediate_action = any(
                    'immediate' in (a.lower()) or 'emergency' in (a.lower())
                    for a in action_texts
                )

                if num_violations > 1:
                    urgency = 'hot'
                    urgency_note = f'{num_violations} OPEN violations at address'
                elif has_immediate_action:
                    urgency = 'hot'
                    urgency_note = 'IMMEDIATE or EMERGENCY action required'
                else:
                    urgency = 'warm'
                    urgency_note = f'{num_violations} violation(s) — needs remediation'

                # Parse date filed (use earliest/first record)
                posted_at = None
                date_filed = address_violations[0].get('date_filed', '')
                if date_filed:
                    try:
                        dt = datetime.fromisoformat(date_filed.replace('Z', '+00:00'))
                        posted_at = dt if dt.tzinfo else timezone.make_aware(dt)
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'MONTGOMERY COUNTY HOUSING VIOLATION: {display_name}']
                content_parts.append(f'Address: {full_addr}')
                if num_violations > 1:
                    content_parts.append(f'Total Violations: {num_violations}')

                for i, v in enumerate(address_violations[:8]):
                    case_num = (v.get('case_number', '') or '').strip()
                    violation_id = (v.get('violation_id', '') or '').strip()
                    action = (v.get('action', '') or '').strip()
                    condition = (v.get('condition', '') or '').strip()
                    item = (v.get('item', '') or '').strip()

                    v_parts = []
                    if case_num:
                        v_parts.append(f'Case: {case_num}')
                    if violation_id:
                        v_parts.append(f'Violation: {violation_id}')
                    if item:
                        v_parts.append(f'Item: {item}')
                    if condition:
                        v_parts.append(f'Condition: {condition}')
                    if action:
                        v_parts.append(f'Action: {action}')

                    if v_parts:
                        content_parts.append(f"  [{i+1}] {' | '.join(v_parts[:3])}")

                if days_ago:
                    content_parts.append(f'Filed: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} — {num_violations} violations — {urgency.upper()}")
                    for v in address_violations[:2]:
                        item = (v.get('item', '') or '').strip()
                        if item:
                            self.stdout.write(f"         - {item[:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?street_address={street_address}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'mc_housing_violations',
                            'street_address': street_address,
                            'unit_number': unit_number,
                            'full_address': full_addr,
                            'violation_count': num_violations,
                            'violation_ids': [v.get('violation_id', '') for v in address_violations],
                            'case_numbers': [v.get('case_number', '') for v in address_violations],
                            'conditions': conditions,
                            'actions': action_texts,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='MD',
                        region='Montgomery County',
                        source_group='public_records',
                        source_type='housing_violations',
                        contact_name=street_address,
                        contact_business=street_address,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"MC housing violation error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"MC housing violations error: {e}")
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['items_scraped']
        run.finish(status='success' if not stats['errors'] else 'partial')

        self.stdout.write(
            f"\nResults: {stats['created']} created, "
            f"{stats['duplicates']} dupes, {stats['errors']} errors"
        )
