"""
San Francisco Health Inspections Monitor
API: https://data.sfgov.org/resource/tvy3-wexg.json  (Socrata SODA)
Dataset: SF Department of Public Health food facility inspections

Rich fields:
  - permit_number, dba (business name), permit_type
  - inspection_date, inspection_type, facility_rating_status
  - violation_count, violation_codes
  - street_address, analysis_neighborhood, supervisor_district
  - latitude, longitude
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.sfgov.org/resource/pyih-qa8i.json'

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
    help = 'Monitor SF Health Inspections (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='sf_health_inspections',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on low-scoring facilities (score < 90)
        params = {
            '$where': (
                f"inspection_date >= '{since}' AND "
                f"inspection_score < 90 AND inspection_score > 0"
            ),
            '$select': (
                'business_id,business_name,business_address,business_city,'
                'business_state,business_postal_code,business_phone_number,'
                'inspection_id,inspection_date,inspection_score,inspection_type,'
                'business_latitude,business_longitude'
            ),
            '$limit': limit,
            '$order': 'inspection_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SF HEALTH INSPECTIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} non-passing inspections from SF Health")

            for rec in records:
                biz_name = (rec.get('business_name', '') or '').strip()
                biz_id = (rec.get('business_id', '') or '').strip()
                biz_address = (rec.get('business_address', '') or '').strip()
                biz_city = (rec.get('business_city', '') or 'San Francisco').strip()
                biz_state = (rec.get('business_state', '') or 'CA').strip()
                biz_zip = (rec.get('business_postal_code', '') or '').strip()
                biz_phone = (rec.get('business_phone_number', '') or '').strip()
                inspection_type = (rec.get('inspection_type', '') or '').strip()
                inspection_date = rec.get('inspection_date', '')

                # Score
                try:
                    score = int(float(rec.get('inspection_score', 0) or 0))
                except (ValueError, TypeError):
                    score = 0

                if not biz_address or not biz_name:
                    continue

                full_addr = f"{biz_address}, {biz_city}, {biz_state} {biz_zip}".strip()

                # Urgency based on score
                if score < 70:
                    urgency = 'hot'
                    urgency_note = f'Critical — Score {score}/100, facility risks closure'
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
                        if dt.tzinfo:
                            posted_at = dt
                        else:
                            posted_at = timezone.make_aware(dt)
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                services = _detect_services(inspection_type)

                # Build rich content
                content_parts = [f'SF HEALTH INSPECTION: {biz_name}']
                content_parts.append(f'Business: {biz_name}')
                content_parts.append(f'Address: {full_addr}')
                if biz_phone:
                    content_parts.append(f'Phone: {biz_phone}')
                content_parts.append(f'Score: {score}/100')
                if inspection_type:
                    content_parts.append(f'Inspection Type: {inspection_type}')
                if days_ago:
                    content_parts.append(f'Inspected: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')

                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {biz_name} @ {biz_address} — Score {score} — {urgency.upper()}")
                    if biz_phone:
                        self.stdout.write(f"         Phone: {biz_phone}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?business_id={biz_id}',
                        content=content,
                        author='',
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_health_inspections',
                            'business_id': biz_id,
                            'business_name': biz_name,
                            'address': full_addr,
                            'phone': biz_phone,
                            'score': score,
                            'inspection_type': inspection_type,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='health',
                        source_type='health_inspections',
                        contact_business=biz_name,
                        contact_phone=biz_phone,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF health inspection error for {biz_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"SF health inspections error: {e}")
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
