"""
San Francisco Fire Violations Monitor
API: https://data.sfgov.org/resource/4zuq-2cbe.json  (Socrata SODA)
Dataset: SF Fire Department violations with corrective action requirements

Rich fields:
  - violation_id, inspection_number
  - address, zipcode, neighborhood_district, battalion, station, bfp_district
  - violation_date, violation_number, violation_item, violation_item_description
  - status (open/closed), corrective_action, close_date
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.sfgov.org/resource/4zuq-2cbe.json'

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
}

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'general contractor']


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
    help = 'Monitor SF Fire Violations (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='sf_fire_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Get fire violations with any status
        params = {
            '$where': f"violation_date >= '{since}'",
            '$select': (
                'violation_id,address,violation_date,violation_number,violation_item,'
                'violation_item_description,status,close_date,corrective_action,'
                'neighborhood_district,zipcode,inspection_number'
            ),
            '$limit': limit,
            '$order': 'violation_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SF FIRE VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} fire violations from San Francisco")

            # Group violations by address to create one lead per address
            violations_by_address = {}
            for rec in records:
                address = (rec.get('address', '') or '').strip()
                if not address:
                    continue
                if address not in violations_by_address:
                    violations_by_address[address] = []
                violations_by_address[address].append(rec)

            for address, address_violations in violations_by_address.items():
                zipcode = (address_violations[0].get('zipcode', '') or '').strip()
                neighborhood = (address_violations[0].get('neighborhood_district', '') or '').strip()

                # Count open violations
                open_violations = [
                    v for v in address_violations
                    if (v.get('status', '') or '').strip().lower() != 'closed'
                ]
                closed_violations = [
                    v for v in address_violations
                    if (v.get('status', '') or '').strip().lower() == 'closed'
                ]

                # Determine urgency
                if len(open_violations) > 1:
                    urgency = 'hot'
                    urgency_note = f'Multiple open violations at this address'
                elif len(open_violations) == 1:
                    urgency = 'warm'
                    urgency_note = f'Single open violation'
                else:
                    urgency = 'new'
                    urgency_note = f'All violations closed'

                # Get most recent violation date
                most_recent = address_violations[0]
                violation_date = most_recent.get('violation_date', '')
                posted_at = None
                if violation_date:
                    try:
                        dt = datetime.fromisoformat(violation_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'SF FIRE VIOLATION: {address}']
                content_parts.append(f'Address: {address}, San Francisco, CA {zipcode}')
                if neighborhood:
                    content_parts.append(f'Neighborhood: {neighborhood}')

                content_parts.append(f'Total violations: {len(address_violations)} (Open: {len(open_violations)}, Closed: {len(closed_violations)})')

                # Add open violations with highest priority
                if open_violations:
                    content_parts.append('\nOPEN VIOLATIONS:')
                    for i, v in enumerate(open_violations[:5], 1):
                        v_num = (v.get('violation_number', '') or '').strip()
                        v_item = (v.get('violation_item', '') or '').strip()
                        v_desc = (v.get('violation_item_description', '') or '').strip()
                        corrective = (v.get('corrective_action', '') or '').strip()

                        parts = []
                        if v_num:
                            parts.append(f'#{v_num}')
                        if v_item:
                            parts.append(v_item[:100])
                        if v_desc:
                            parts.append(v_desc[:100])
                        if corrective:
                            parts.append(f'Action: {corrective[:100]}')

                        content_parts.append(f'  [{i}] ' + ' | '.join(parts))

                # Add closed violations for context
                if closed_violations and len(closed_violations) <= 3:
                    content_parts.append(f'\nRECENT CLOSED: {len(closed_violations)} violations')
                    for i, v in enumerate(closed_violations[:3], 1):
                        close_date = (v.get('close_date', '') or '').strip()
                        v_item = (v.get('violation_item', '') or '').strip()
                        content_parts.append(f'  [{i}] {v_item[:100]} (closed: {close_date})')

                if days_ago:
                    content_parts.append(f'\nMost Recent: {days_ago}')

                content_parts.append(f'\nUrgency: {urgency_note}')

                # Detect services from violation descriptions
                all_text = ' '.join(
                    f"{v.get('violation_item', '')} {v.get('violation_item_description', '')} {v.get('corrective_action', '')}"
                    for v in address_violations
                )
                services = _detect_services(all_text)
                content_parts.append(f'Services needed: {", ".join(services[:6])}')

                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {address} — {len(open_violations)} open — {urgency.upper()}")
                    for v in open_violations[:2]:
                        item = (v.get('violation_item', '') or '').strip()
                        self.stdout.write(f"         - {item[:80]}")
                    stats['created'] += 1
                    continue

                try:
                    inspection_num = (most_recent.get('inspection_number', '') or '').strip()
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?address={address}',
                        content=content,
                        author=address,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_fire_violations',
                            'address': address,
                            'inspection_number': inspection_num,
                            'open_violation_count': len(open_violations),
                            'total_violation_count': len(address_violations),
                            'neighborhood': neighborhood,
                            'urgency': urgency,
                            'services_mapped': services,
                            'violations': [
                                {
                                    'violation_id': v.get('violation_id', ''),
                                    'violation_number': v.get('violation_number', ''),
                                    'item': (v.get('violation_item', '') or '').strip(),
                                    'description': (v.get('violation_item_description', '') or '').strip(),
                                    'status': (v.get('status', '') or '').strip(),
                                    'corrective_action': (v.get('corrective_action', '') or '').strip(),
                                }
                                for v in address_violations[:10]
                            ],
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='public_records',
                        source_type='fire_violations',
                        contact_name=address,
                        contact_business=address,
                        contact_address=f"{address}, San Francisco, CA {zipcode}",
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF fire violation error for {address}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"SF fire violations error: {e}")
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
