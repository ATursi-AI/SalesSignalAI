"""
Austin Repeat Offender Property Monitor
API: https://data.austintexas.gov/resource/cdze-ufp8.json (Socrata SODA)
Dataset: Registered repeat offender properties with violations and compliance records

Rich fields:
  - rop_registration_number, registered_address, registration_status
  - city, state, zip_code, registeredunits
  - latitude, longitude
  - violation_case_number, violation_case_date, violation_case_link
  - State: TX, Region: Austin
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.austintexas.gov/resource/cdze-ufp8.json'

VIOLATION_SERVICE_MAP = {
    'violation': ['property management', 'compliance attorney'],
    'offender': ['property management', 'compliance attorney'],
    'repeat': ['property management', 'compliance attorney'],
    'registration': ['property attorney', 'compliance attorney'],
    'compliance': ['compliance consultant', 'property management'],
    'code': ['code compliance specialist'],
    'enforce': ['compliance attorney'],
    'property': ['property management', 'real estate manager'],
}

DEFAULT_SERVICES = ['property management', 'compliance attorney']


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
    help = 'Monitor Austin Repeat Offender Properties (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='austin_repeat_offender',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        params = {
            '$where': f"violation_case_date >= '{since}'",
            '$select': (
                'rop_registration_number,registered_address,registration_status,'
                'city,state,zip_code,registeredunits,latitude,longitude,'
                'violation_case_number,violation_case_date,violation_case_link'
            ),
            '$limit': limit,
            '$order': 'violation_case_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  AUSTIN REPEAT OFFENDER MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} repeat offender violations from Austin")

            for rec in records:
                rop_reg_num = (rec.get('rop_registration_number', '') or '').strip()
                registered_addr = (rec.get('registered_address', '') or '').strip()
                registration_status = (rec.get('registration_status', '') or '').strip()
                city = (rec.get('city', '') or '').strip()
                state = (rec.get('state', '') or '').strip()
                zip_code = (rec.get('zip_code', '') or '').strip()
                registered_units = rec.get('registeredunits', 0)
                violation_date = rec.get('violation_case_date', '')
                violation_case_num = (rec.get('violation_case_number', '') or '').strip()
                violation_link = (rec.get('violation_case_link', '') or '').strip()

                if not registered_addr:
                    continue

                full_addr = f"{registered_addr}, {city}, {state} {zip_code}".strip()
                display_name = registered_addr

                # Determine urgency
                try:
                    units = int(registered_units) if registered_units else 0
                except (ValueError, TypeError):
                    units = 0

                is_large_property = units > 20
                is_active = registration_status.lower() == 'active'

                if is_large_property:
                    urgency = 'hot'
                    urgency_note = f'Large property ({units} units registered)'
                elif is_active:
                    urgency = 'warm'
                    urgency_note = f'Active repeat offender registration'
                else:
                    urgency = 'new'
                    urgency_note = f'Repeat offender property violation'

                # Parse violation date
                posted_at = None
                if violation_date:
                    try:
                        dt = datetime.fromisoformat(violation_date.replace('Z', '+00:00'))
                        posted_at = dt if dt.tzinfo else timezone.make_aware(dt)
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                services = _detect_services(registration_status + ' repeat offender')

                # Build rich content
                content_parts = [f'AUSTIN REPEAT OFFENDER: {display_name}']
                content_parts.append(f'Property: {display_name}')
                content_parts.append(f'Address: {full_addr}')
                content_parts.append(f'Registration #: {rop_reg_num}')
                content_parts.append(f'Registered Units: {units}')
                content_parts.append(f'Status: {registration_status}')
                if violation_case_num:
                    content_parts.append(f'Violation Case #: {violation_case_num}')
                if days_ago:
                    content_parts.append(f'Date: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:4])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} ({units} units) — {registration_status} — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=violation_link or f'{SODA_URL}?rop_reg={rop_reg_num}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'austin_repeat_offender',
                            'property_address': full_addr,
                            'registration_number': rop_reg_num,
                            'registered_units': units,
                            'registration_status': registration_status,
                            'violation_case_number': violation_case_num,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='TX',
                        region='Austin',
                        source_group='public_records',
                        source_type='repeat_offender_violations',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Austin repeat offender error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Austin repeat offender error: {e}")
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
