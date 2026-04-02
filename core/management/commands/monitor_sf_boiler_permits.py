"""
San Francisco Boiler Permits Monitor
API: https://data.sfgov.org/resource/5dp4-gtxk.json (Socrata SODA)
Dataset: Contains boiler permit applications and details for SF properties

Fields:
  - permit_number, application_date, expiration_date
  - street_number, street_name, street_suffix, block, lot, zip_code
  - description, status, boiler_type, model, boiler_serial_number
  - neighborhoods_analysis_boundaries, supervisor_district
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.sfgov.org/resource/5dp4-gtxk.json'


class Command(BaseCommand):
    help = 'Monitor San Francisco Boiler Permits (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='sf_boiler_permits',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        params = {
            '$select': (
                'permit_number,application_date,street_number,street_name,'
                'street_suffix,description,status,expiration_date,'
                'neighborhoods_analysis_boundaries,boiler_serial_number,'
                'boiler_type,model,zip_code'
            ),
            '$where': f"application_date >= '{since}T00:00:00'",
            '$order': 'application_date DESC',
            '$limit': limit,
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SF BOILER PERMITS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} boiler permits from San Francisco")

            for rec in records:
                permit_number = rec.get('permit_number', '').strip()
                application_date = rec.get('application_date', '')
                expiration_date = rec.get('expiration_date', '')
                street_number = (rec.get('street_number', '') or '').strip()
                street_name = (rec.get('street_name', '') or '').strip()
                street_suffix = (rec.get('street_suffix', '') or '').strip()
                description = (rec.get('description', '') or '').strip()
                status = (rec.get('status', '') or '').strip()
                boiler_type = (rec.get('boiler_type', '') or '').strip()
                model = (rec.get('model', '') or '').strip()
                boiler_serial_number = (rec.get('boiler_serial_number', '') or '').strip()
                zip_code = (rec.get('zip_code', '') or '').strip()
                neighborhood = (rec.get('neighborhoods_analysis_boundaries', '') or '').strip()

                if not street_name or not permit_number:
                    continue

                # Build address
                addr_parts = [street_number, street_name]
                if street_suffix:
                    addr_parts.append(street_suffix)
                full_addr = ' '.join(filter(None, addr_parts))
                full_addr_with_location = f"{full_addr}, San Francisco, CA {zip_code}".strip()

                # Determine urgency
                is_high_pressure = 'high pressure' in boiler_type.lower()
                expired = False
                expiring_soon = False

                if expiration_date:
                    try:
                        exp_date = datetime.fromisoformat(expiration_date.replace('Z', '+00:00'))
                        exp_date = exp_date.replace(tzinfo=None)
                        now = datetime.now()
                        days_until = (exp_date - now).days
                        if days_until < 0:
                            expired = True
                        elif days_until <= 30:
                            expiring_soon = True
                    except Exception:
                        pass

                if expired or expiring_soon:
                    urgency = 'hot'
                    urgency_note = 'Permit expiring soon or already expired'
                elif is_high_pressure:
                    urgency = 'warm'
                    urgency_note = 'High pressure boiler - maintenance critical'
                else:
                    urgency = 'new'
                    urgency_note = 'Active boiler permit'

                # Parse application date
                posted_at = None
                if application_date:
                    try:
                        dt = datetime.fromisoformat(application_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'SF BOILER PERMIT: {full_addr}']
                content_parts.append(f'Address: {full_addr_with_location}')
                if neighborhood:
                    content_parts.append(f'Neighborhood: {neighborhood}')
                content_parts.append(f'Permit #: {permit_number}')
                if days_ago:
                    content_parts.append(f'Applied: {days_ago}')
                if description:
                    content_parts.append(f'Description: {description}')
                if boiler_type:
                    content_parts.append(f'Boiler Type: {boiler_type}')
                if model:
                    content_parts.append(f'Model: {model}')
                if boiler_serial_number:
                    content_parts.append(f'Serial: {boiler_serial_number}')
                if status:
                    content_parts.append(f'Status: {status}')
                if expiration_date:
                    content_parts.append(f'Expires: {expiration_date}')
                content_parts.append(f'Urgency: {urgency_note}')

                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {full_addr} — Permit {permit_number} — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?permit_number={permit_number}',
                        content=content,
                        author=full_addr,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_boiler_permits',
                            'permit_number': permit_number,
                            'address': full_addr_with_location,
                            'boiler_type': boiler_type,
                            'status': status,
                            'urgency': urgency,
                            'neighborhood': neighborhood,
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='public_records',
                        source_type='boiler_permits',
                        contact_name=full_addr,
                        contact_business=full_addr,
                        contact_address=full_addr_with_location,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF boiler permit error for {permit_number}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"SF boiler permits error: {e}")
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
