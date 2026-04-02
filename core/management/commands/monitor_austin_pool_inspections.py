"""
Austin Pool Inspections Monitor
API: https://data.austintexas.gov/resource/peux-uuwu.json  (Socrata SODA)
Dataset: Pool and spa inspection records

Rich fields:
  - facility_id, facility_name, street_address
  - city_desc, state_desc, zip_code
  - latitude, longitude
  - inspection_date, inspection_type, inspection_result
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.austintexas.gov/resource/peux-uuwu.json'

VIOLATION_SERVICE_MAP = {
    'fail': ['pool maintenance', 'pool repair'],
    'closure': ['pool maintenance', 'pool repair'],
    'chemical': ['chemical supply', 'pool maintenance'],
    'sanitiz': ['chemical supply', 'pool maintenance'],
    'chlorine': ['chemical supply'],
    'bacteria': ['water treatment', 'chemical supply'],
    'algae': ['water treatment', 'chemical supply'],
    'filter': ['pool equipment', 'pool maintenance'],
    'pump': ['pool equipment', 'pool repair'],
    'circul': ['pool equipment', 'pool maintenance'],
    'drain': ['pool repair', 'pool equipment'],
    'leak': ['pool repair'],
    'crack': ['pool repair'],
    'equipment': ['pool equipment', 'pool repair'],
    'deck': ['pool repair', 'general contractor'],
    'gate': ['pool repair', 'general contractor'],
    'safety': ['pool repair', 'pool equipment'],
}

DEFAULT_SERVICES = ['pool maintenance', 'pool repair', 'water treatment']


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
    help = 'Monitor Austin Pool Inspections (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='austin_pool_inspections',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        # Fetch all inspections from the past N days
        params = {
            '$where': f"inspection_date >= '{since}'",
            '$select': (
                'facility_id,facility_name,street_address,'
                'city_desc,state_desc,zip_code,latitude,longitude,'
                'inspection_date,inspection_type,inspection_result'
            ),
            '$limit': limit,
            '$order': 'inspection_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  AUSTIN POOL INSPECTIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} pool inspections from Austin")

            for rec in records:
                facility_id = rec.get('facility_id', '')
                facility_name = (rec.get('facility_name', '') or '').strip()
                street_address = (rec.get('street_address', '') or '').strip()
                city_desc = rec.get('city_desc', 'Austin')
                state_desc = rec.get('state_desc', 'TX')
                zip_code = rec.get('zip_code', '')
                latitude = rec.get('latitude')
                longitude = rec.get('longitude')
                inspection_date = rec.get('inspection_date', '')
                inspection_type = (rec.get('inspection_type', '') or '').strip()
                inspection_result = (rec.get('inspection_result', '') or '').strip()

                if not facility_name or not street_address:
                    continue

                full_addr = f"{street_address}, {city_desc}, {state_desc} {zip_code}".strip()
                display_name = facility_name

                # Detect services from inspection result
                services = _detect_services(inspection_result)

                # Urgency based on inspection result
                result_lower = inspection_result.lower()
                if 'fail' in result_lower or 'closure' in result_lower:
                    urgency = 'hot'
                    urgency_note = 'FAILED inspection — immediate action required'
                elif 'warning' in result_lower or 'correction' in result_lower:
                    urgency = 'warm'
                    urgency_note = 'Warning issued — correction needed'
                else:
                    urgency = 'new'
                    urgency_note = 'Routine inspection completed'

                # Parse inspection date
                posted_at = None
                if inspection_date:
                    try:
                        dt = datetime.fromisoformat(inspection_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'AUSTIN POOL INSPECTION: {display_name}']
                content_parts.append(f'Facility: {display_name}')
                content_parts.append(f'Address: {full_addr}')
                if latitude and longitude:
                    content_parts.append(f'Location: {latitude}, {longitude}')
                if inspection_type:
                    content_parts.append(f'Type: {inspection_type}')
                content_parts.append(f'Result: {inspection_result}')
                if days_ago:
                    content_parts.append(f'Date: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {street_address} — {inspection_result} — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?facility_id={facility_id}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'austin_pool_inspections',
                            'facility_id': facility_id,
                            'facility_name': display_name,
                            'address': full_addr,
                            'inspection_type': inspection_type,
                            'result': inspection_result,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='TX',
                        region='Austin',
                        source_group='health',
                        source_type='pool_inspections',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Austin pool inspection error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Austin pool inspections error: {e}")
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
