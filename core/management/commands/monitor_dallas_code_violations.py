"""
Dallas Code Violations Monitor
API: https://www.dallasopendata.com/resource/d7e7-envw.json  (Socrata SODA)
Dataset: Code Compliance service requests from Dallas

Rich fields:
  - service_request_number, address, city_council_district
  - department, service_request_type, status
  - created_date, update_date, closed_date
  - outcome, priority, method_received_description
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

SODA_URL = 'https://www.dallasopendata.com/resource/d7e7-envw.json'

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
    'weed': ['landscaping', 'lawn care'],
    'vegetation': ['landscaping', 'lawn care'],
    'grass': ['landscaping', 'lawn care'],
    'overgrown': ['landscaping', 'lawn care'],
    'junk': ['junk removal', 'hauling'],
    'vehicle': ['towing', 'auto removal'],
    'towing': ['towing', 'auto removal'],
    'building': ['contractor', 'general contractor'],
    'structure': ['contractor', 'general contractor'],
    'roof': ['roofing'],
    'gutter': ['gutter cleaning', 'roofing'],
    'sidewalk': ['concrete', 'general contractor'],
    'driveway': ['concrete', 'paving'],
    'concrete': ['concrete', 'paving'],
}

DEFAULT_SERVICES = ['general contractor', 'code compliance specialist', 'property maintenance']


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
    help = 'Monitor Dallas Code Violations (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='dallas_code_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on code compliance requests
        params = {
            '$where': (
                f"created_date >= '{since}' AND department = 'Code Compliance'"
            ),
            '$select': (
                'service_request_number,address,city_council_district,'
                'department,service_request_type,status,'
                'created_date,update_date,closed_date,outcome,priority'
            ),
            '$limit': limit,
            '$order': 'created_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  DALLAS CODE VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} code violations from Dallas")

            for rec in records:
                service_request_number = (rec.get('service_request_number', '') or '').strip()
                address = (rec.get('address', '') or '').strip()
                city_council_district = (rec.get('city_council_district', '') or '').strip()
                service_request_type = (rec.get('service_request_type', '') or '').strip()
                status = (rec.get('status', '') or '').strip()
                priority = (rec.get('priority', '') or '').strip()
                created_date = rec.get('created_date', '')
                update_date = rec.get('update_date', '')
                closed_date = rec.get('closed_date', '')
                outcome = (rec.get('outcome', '') or '').strip()

                if not address:
                    continue

                full_addr = f"{address}, Dallas, TX".strip()
                display_name = address

                # Combine request type and outcome for service detection
                combined_text = f"{service_request_type} {outcome}"
                services = _detect_services(combined_text)

                # Urgency logic based on priority and status
                is_urgent = priority in ('Urgent', 'Dispatch')
                is_in_progress = status == 'In Progress'

                # Calculate days open if in progress
                days_open = 0
                if is_in_progress and created_date:
                    try:
                        created_dt = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
                        days_open = (datetime.now(timezone.utc) - created_dt).days
                    except Exception:
                        pass

                if is_urgent:
                    urgency = 'hot'
                    urgency_note = f'URGENT priority — {priority}'
                elif is_in_progress and days_open > 14:
                    urgency = 'warm'
                    urgency_note = f'In Progress {days_open} days — urgent attention needed'
                else:
                    urgency = 'new'
                    urgency_note = f'{status} — new code violation'

                # Parse created date
                posted_at = None
                if created_date:
                    try:
                        dt = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'DALLAS CODE VIOLATION: {display_name}']
                content_parts.append(f'Address: {full_addr}')
                if service_request_number:
                    content_parts.append(f'Request #: {service_request_number}')
                if service_request_type:
                    content_parts.append(f'Type: {service_request_type}')
                if city_council_district:
                    content_parts.append(f'District: {city_council_district}')
                if priority:
                    content_parts.append(f'Priority: {priority}')
                content_parts.append(f'Status: {status}')
                if outcome:
                    content_parts.append(f'Outcome: {outcome}')
                if days_ago:
                    content_parts.append(f'Created: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} — {status} — {urgency.upper()}")
                    if service_request_type:
                        self.stdout.write(f"         Type: {service_request_type[:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?service_request_number={service_request_number}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'dallas_code_violations',
                            'service_request_number': service_request_number,
                            'address': full_addr,
                            'request_type': service_request_type,
                            'status': status,
                            'priority': priority,
                            'outcome': outcome,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='TX',
                        region='Dallas',
                        source_group='public_records',
                        source_type='code_complaints',
                        contact_name=address,
                        contact_business=address,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Dallas code violation error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Dallas code violations error: {e}")
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
