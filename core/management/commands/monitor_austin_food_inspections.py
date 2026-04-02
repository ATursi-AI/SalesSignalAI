"""
Austin Food Inspections Monitor
API: https://data.austintexas.gov/resource/ecmv-9xxi.json  (Socrata SODA)
Dataset: Food inspection results for Austin restaurants and facilities

Rich fields:
  - restaurant_name, address, zip_code, facility_id
  - inspection_date, score, process_description
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

SODA_URL = 'https://data.austintexas.gov/resource/ecmv-9xxi.json'

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
    help = 'Monitor Austin Food Inspections (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='austin_food_inspections',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on lower-scoring inspections (problems)
        params = {
            '$where': f"inspection_date >= '{since}' AND score < 90",
            '$select': (
                'restaurant_name,address,zip_code,facility_id,'
                'inspection_date,score,process_description'
            ),
            '$limit': limit,
            '$order': 'inspection_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  AUSTIN FOOD INSPECTIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} failing/low-score inspections from Austin")

            for rec in records:
                facility_id = (rec.get('facility_id', '') or '').strip()
                restaurant_name = (rec.get('restaurant_name', '') or '').strip()
                address = (rec.get('address', '') or '').strip()
                zipcode = (rec.get('zip_code', '') or '').strip()
                inspection_date = rec.get('inspection_date', '')
                process_description = (rec.get('process_description', '') or '').strip()

                # Score
                try:
                    score = int(rec.get('score', 0) or 0)
                except (ValueError, TypeError):
                    score = 0

                if not address or not restaurant_name:
                    continue

                full_addr = f"{address}, Austin, TX {zipcode}".strip()

                # Detect services from process description
                services = _detect_services(process_description)

                # Urgency based on score
                if score < 70:
                    urgency = 'hot'
                    urgency_note = f'Critical — Score {score}/100'
                elif score < 80:
                    urgency = 'warm'
                    urgency_note = f'Needs improvement — Score {score}/100'
                else:
                    urgency = 'new'
                    urgency_note = f'Minor issues — Score {score}/100'

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
                content_parts = [f'AUSTIN FOOD INSPECTION: {restaurant_name}']
                content_parts.append(f'Business: {restaurant_name}')
                content_parts.append(f'Address: {full_addr}')
                content_parts.append(f'Facility ID: {facility_id}')
                content_parts.append(f'Score: {score}/100')

                if process_description:
                    content_parts.append(f'Issues: {process_description[:300]}')

                if days_ago:
                    content_parts.append(f'Inspected: {days_ago}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')

                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(
                        f"  [DRY] {restaurant_name} @ {address} — "
                        f"Score {score} — {urgency.upper()}"
                    )
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?facility_id={facility_id}',
                        content=content,
                        author=restaurant_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'austin_food_inspections',
                            'facility_id': facility_id,
                            'restaurant_name': restaurant_name,
                            'address': full_addr,
                            'score': score,
                            'process_description': process_description,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='TX',
                        region='Austin',
                        source_group='health',
                        source_type='food_inspections',
                        contact_name=restaurant_name,
                        contact_business=restaurant_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Austin food inspection error for {restaurant_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Austin food inspections error: {e}")
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
