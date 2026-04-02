"""
Chicago Building Violations Monitor
API: https://data.cityofchicago.org/resource/22u3-xenr.json  (Socrata SODA)
Dataset: 2M rows, updated daily

Rich fields:
  - violation_description, violation_inspector_comments, violation_code
  - violation_status (Complied/Open/No Entry), violation_ordinance
  - inspection_category (COMPLAINT/PERIODIC/PERMIT/REGISTRATION)
  - address, latitude, longitude, property_group, ssa
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.cityofchicago.org/resource/22u3-xenr.json'

VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'water': ['plumber'],
    'sewer': ['plumber', 'sewer service'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'light': ['electrician'],
    'fire escape': ['general contractor', 'fire safety'],
    'fire': ['fire safety', 'electrician'],
    'smoke detector': ['fire safety'],
    'sprinkler': ['fire safety'],
    'elevator': ['elevator repair'],
    'roof': ['roofer', 'general contractor'],
    'stair': ['general contractor'],
    'porch': ['general contractor'],
    'deck': ['general contractor'],
    'handrail': ['general contractor'],
    'foundation': ['general contractor', 'structural engineer'],
    'structural': ['general contractor', 'structural engineer'],
    'masonry': ['masonry contractor'],
    'brick': ['masonry contractor'],
    'tuckpoint': ['masonry contractor'],
    'window': ['general contractor', 'window repair'],
    'door': ['general contractor'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'boiler': ['HVAC', 'plumber'],
    'furnace': ['HVAC'],
    'ventilation': ['HVAC'],
    'mold': ['mold remediation'],
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'rat': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'paint': ['painter'],
    'lead': ['lead abatement', 'painter'],
    'asbestos': ['asbestos abatement'],
    'demolition': ['demolition contractor'],
    'fence': ['fencing contractor'],
    'sidewalk': ['concrete contractor'],
    'concrete': ['concrete contractor'],
    'trash': ['waste management', 'commercial cleaning'],
    'garbage': ['waste management', 'commercial cleaning'],
    'debris': ['commercial cleaning', 'demolition contractor'],
    'vacant': ['general contractor', 'security'],
    'abandon': ['general contractor', 'security'],
    'permit': ['general contractor'],
}

DEFAULT_SERVICES = ['general contractor', 'plumber', 'electrician']


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
    help = 'Monitor Chicago Building Violations (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=3000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='chicago_building_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        params = {
            '$where': f"violation_date >= '{since}' AND violation_status = 'OPEN'",
            '$select': (
                'id,violation_date,violation_code,violation_status,'
                'violation_description,violation_inspector_comments,'
                'violation_ordinance,violation_location,'
                'inspection_number,inspection_status,inspection_category,'
                'department_bureau,address,property_group,ssa,latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'violation_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CHICAGO BUILDING VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} open violations from Chicago")

            # Group by address + property_group for consolidated leads
            properties = {}
            for rec in records:
                address = (rec.get('address', '') or '').strip()
                if not address:
                    continue

                prop_group = rec.get('property_group', '') or address
                key = str(prop_group)

                if key not in properties:
                    properties[key] = {
                        'address': address,
                        'latitude': rec.get('latitude', ''),
                        'longitude': rec.get('longitude', ''),
                        'ssa': rec.get('ssa', ''),
                        'violations': [],
                    }

                violation = {
                    'id': rec.get('id', ''),
                    'date': rec.get('violation_date', ''),
                    'code': rec.get('violation_code', ''),
                    'description': (rec.get('violation_description', '') or '').strip(),
                    'comments': (rec.get('violation_inspector_comments', '') or '').strip(),
                    'ordinance': (rec.get('violation_ordinance', '') or '').strip(),
                    'location': (rec.get('violation_location', '') or '').strip(),
                    'category': (rec.get('inspection_category', '') or '').strip(),
                    'bureau': (rec.get('department_bureau', '') or '').strip(),
                }
                properties[key]['violations'].append(violation)

            self.stdout.write(f"Grouped into {len(properties)} properties")

            for prop_key, prop in properties.items():
                address = prop['address']
                violations = prop['violations']
                if not violations:
                    continue

                # Build all violation text for service detection
                all_text = ' '.join(
                    f"{v['description']} {v['comments']}" for v in violations
                )
                services = _detect_services(all_text)

                # Urgency: complaints and multiple violations = hot
                has_complaint = any(v['category'].upper() == 'COMPLAINT' for v in violations)
                if has_complaint or len(violations) >= 3:
                    urgency = 'hot'
                    urgency_note = f'{len(violations)} open violations — complaint-driven inspection'
                elif len(violations) >= 2:
                    urgency = 'warm'
                    urgency_note = f'{len(violations)} open violations — needs remediation'
                else:
                    urgency = 'new'
                    urgency_note = 'Building code violation filed'

                # Most recent violation date
                dates = []
                for v in violations:
                    if v['date']:
                        try:
                            dt = datetime.fromisoformat(v['date'].replace('Z', '+00:00'))
                            dates.append(timezone.make_aware(dt.replace(tzinfo=None)))
                        except Exception:
                            pass
                posted_at = max(dates) if dates else None

                # Build rich content
                content_parts = [f'CHICAGO BUILDING VIOLATION: {address}']
                content_parts.append(f'Address: {address}, Chicago, IL')
                if violations[0].get('bureau'):
                    content_parts.append(f'Bureau: {violations[0]["bureau"]}')
                if posted_at:
                    days_ago = (timezone.now() - posted_at).days
                    content_parts.append(f'Most recent: {days_ago} days ago')

                content_parts.append(f'Open Violations: {len(violations)}')

                for i, v in enumerate(violations[:6]):
                    parts = []
                    if v['description']:
                        parts.append(v['description'][:150])
                    if v['comments']:
                        parts.append(f'Inspector: {v["comments"][:150]}')
                    if v['ordinance']:
                        parts.append(f'Ordinance: {v["ordinance"][:80]}')
                    if v['location']:
                        parts.append(f'Location: {v["location"]}')
                    if v['category']:
                        parts.append(f'({v["category"]})')
                    content_parts.append(f'  [{i+1}] ' + ' | '.join(parts))

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {address} — {len(violations)} violations — {urgency.upper()}")
                    for v in violations[:2]:
                        desc = v['description'][:80] or v['comments'][:80]
                        self.stdout.write(f"         - {desc}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?address={address}',
                        content=content,
                        author='',
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'chicago_building_violations',
                            'address': f'{address}, Chicago, IL',
                            'violation_count': len(violations),
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='IL',
                        region='Chicago',
                        source_group='public_records',
                        source_type='building_violations',
                        contact_name=address,
                        contact_business=address,
                        contact_address=f'{address}, Chicago, IL',
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Chicago building violation error for {address}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Chicago building violations error: {e}")
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
