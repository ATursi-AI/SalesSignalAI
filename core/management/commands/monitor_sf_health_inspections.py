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

SODA_URL = 'https://data.sfgov.org/resource/tvy3-wexg.json'

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

        # Focus on non-passing facilities
        params = {
            '$where': (
                f"inspection_date >= '{since}' AND "
                f"facility_rating_status != 'Pass'"
            ),
            '$select': (
                'inspection_date,dba,permit_number,permit_type,street_address,'
                'inspection_type,facility_rating_status,violation_count,'
                'violation_codes,analysis_neighborhood,latitude,longitude'
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
                dba = (rec.get('dba', '') or '').strip()
                permit_num = (rec.get('permit_number', '') or '').strip()
                street_address = (rec.get('street_address', '') or '').strip()
                neighborhood = (rec.get('analysis_neighborhood', '') or '').strip()
                permit_type = (rec.get('permit_type', '') or '').strip()
                inspection_type = (rec.get('inspection_type', '') or '').strip()
                facility_rating = (rec.get('facility_rating_status', '') or '').strip()
                violation_count = rec.get('violation_count', 0)
                violation_codes = (rec.get('violation_codes', '') or '').strip()
                inspection_date = rec.get('inspection_date', '')

                if not street_address:
                    continue

                full_addr = f"{street_address}, San Francisco, CA"
                display_name = dba or street_address

                # Determine urgency based on facility rating status
                rating_lower = facility_rating.lower()
                if rating_lower == 'closure':
                    urgency = 'hot'
                    urgency_note = 'CLOSURE ORDER — Facility must close'
                elif rating_lower == 'conditional pass' and violation_count and int(violation_count) >= 5:
                    urgency = 'hot'
                    urgency_note = f'Conditional Pass with {violation_count} violations'
                elif rating_lower == 'conditional pass':
                    urgency = 'warm'
                    urgency_note = 'Conditional Pass — remediation needed'
                else:
                    urgency = 'new'
                    urgency_note = f'{facility_rating} status'

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
                content_parts = [f'SF HEALTH INSPECTION: {display_name}']
                content_parts.append(f'Business: {display_name}')
                content_parts.append(f'Address: {full_addr}')
                if neighborhood:
                    content_parts.append(f'Neighborhood: {neighborhood}')
                if permit_num:
                    content_parts.append(f'Permit: {permit_num}')
                if permit_type:
                    content_parts.append(f'Type: {permit_type}')
                content_parts.append(f'Facility Status: {facility_rating}')
                if inspection_type:
                    content_parts.append(f'Inspection: {inspection_type}')
                if days_ago:
                    content_parts.append(f'Date: {days_ago}')

                if violation_count:
                    try:
                        v_count = int(violation_count)
                        content_parts.append(f'Violations: {v_count}')
                    except (ValueError, TypeError):
                        pass

                if violation_codes:
                    content_parts.append(f'\nViolation Codes:')
                    codes = violation_codes.split(',')[:10]
                    for code in codes:
                        code_str = code.strip()
                        if code_str:
                            content_parts.append(f'  - {code_str}')

                content_parts.append(f'\nUrgency: {urgency_note}')

                # Detect services from violation codes
                services = _detect_services(violation_codes)
                content_parts.append(f'Services needed: {", ".join(services[:6])}')

                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {street_address} — {facility_rating} — {urgency.upper()}")
                    if violation_count:
                        self.stdout.write(f"         - {violation_count} violations")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?permit_number={permit_num}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_health_inspections',
                            'business_name': display_name,
                            'permit_number': permit_num,
                            'address': full_addr,
                            'permit_type': permit_type,
                            'facility_rating': facility_rating,
                            'violation_count': violation_count,
                            'violation_codes': violation_codes,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='health',
                        source_type='health_inspections',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF health inspection error for {display_name}: {e}")
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
