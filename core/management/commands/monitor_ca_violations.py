"""
Monitor Cal/OSHA workplace safety violations.
Property owners / businesses with violations need contractors to fix issues.
Businesses with violations may need compliance services.

Data: OSHA enforcement data API (federal, includes CA).
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

# Federal OSHA enforcement data — covers all states including CA
OSHA_API = 'https://enforcedata.dol.gov/api/osha_enforcement'


class Command(BaseCommand):
    help = 'Monitor Cal/OSHA violations for CA leads'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=500)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--county', type=str, default='')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']
        county = options.get('county', '')

        run = MonitorRun.objects.create(
            monitor_name='ca_osha_violations',
            details={'days': days, 'county': county},
        )

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CAL/OSHA VIOLATION MONITOR")
        self.stdout.write(f"  Days: {days} | Limit: {limit} | County: {county or 'all'}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'skipped': 0}
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        try:
            params = {
                'p_state': 'CA',
                'p_start_date': cutoff,
                'p_page_size': str(min(limit, 100)),
            }

            self.stdout.write(f"Fetching OSHA enforcement data for CA since {cutoff}...")
            resp = requests.get(OSHA_API, params=params, timeout=30)

            if resp.status_code != 200:
                self.stdout.write(self.style.WARNING(
                    f"OSHA API returned {resp.status_code}.\n"
                    "This API may require different parameters or may be rate-limited.\n"
                    "Trying alternative endpoint..."
                ))
                # Try the inspection-level API
                self._try_inspection_api(days, limit, dry_run, stats, county)
            else:
                raw = resp.json()
                records = raw if isinstance(raw, list) else raw.get('results', raw.get('data', []))
                self.stdout.write(f"Fetched {len(records)} OSHA violation records")

                for rec in records:
                    establishment = rec.get('estab_name', '') or rec.get('establishment_name', '')
                    address = rec.get('site_address', '') or rec.get('address', '')
                    city = rec.get('site_city', '') or rec.get('city', '')
                    violation_type = rec.get('viol_type', '') or rec.get('violation_type', '')
                    penalty = rec.get('current_penalty', '') or rec.get('penalty', '0')
                    insp_date = rec.get('open_date', '') or rec.get('inspection_date', '')

                    if not establishment:
                        continue
                    if county and county.lower() not in city.lower():
                        stats['skipped'] += 1
                        continue

                    try:
                        penalty_num = float(str(penalty).replace(',', '').replace('$', ''))
                    except (ValueError, TypeError):
                        penalty_num = 0

                    content = (
                        f"Cal/OSHA Violation: {establishment}\n"
                        f"Address: {address}, {city}, CA\n"
                        f"Type: {violation_type}\n"
                        f"Penalty: ${penalty_num:,.0f}\n"
                        f"Inspection Date: {insp_date}"
                    )

                    if dry_run:
                        self.stdout.write(f"  [DRY] {establishment} | {city} | ${penalty_num:,.0f}")
                        stats['created'] += 1
                        continue

                    lead, created, _ = process_lead(
                        platform='public_records',
                        source_url='https://enforcedata.dol.gov/',
                        content=content,
                        author=establishment,
                        raw_data=rec,
                        state='CA',
                        region=city,
                        source_group='public_records',
                        source_type='violations',
                        contact_business=establishment,
                        contact_address=f"{address}, {city}, CA" if address else f"{city}, CA",
                    )

                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1

        except Exception as e:
            logger.error(f"Cal/OSHA monitor error: {e}")
            stats['errors'] += 1

        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['created'] + stats['duplicates'] + stats['skipped']
        run.finish(status='success' if not stats['errors'] else 'partial')

        self.stdout.write(f"\nResults: {stats['created']} created, {stats['duplicates']} dupes, {stats['errors']} errors")

    def _try_inspection_api(self, days, limit, dry_run, stats, county):
        """Alternative OSHA inspection endpoint."""
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
            url = 'https://enforcedata.dol.gov/api/osha_inspection'
            resp = requests.get(url, params={
                'p_state': 'CA',
                'p_start_date': cutoff,
                'p_page_size': str(min(limit, 50)),
            }, timeout=30)

            if resp.status_code == 200:
                raw = resp.json()
                records = raw if isinstance(raw, list) else raw.get('results', [])
                self.stdout.write(f"Fetched {len(records)} from inspection API")

                for rec in records:
                    biz = rec.get('estab_name', '')
                    city = rec.get('site_city', '')
                    addr = rec.get('site_address', '')
                    if not biz:
                        continue
                    if county and county.lower() not in city.lower():
                        stats['skipped'] += 1
                        continue

                    content = f"Cal/OSHA Inspection: {biz}\nAddress: {addr}, {city}, CA"

                    if dry_run:
                        self.stdout.write(f"  [DRY] {biz} | {city}")
                        stats['created'] += 1
                        continue

                    lead, created, _ = process_lead(
                        platform='public_records',
                        source_url='https://enforcedata.dol.gov/',
                        content=content,
                        author=biz,
                        raw_data=rec,
                        state='CA',
                        region=city,
                        source_group='public_records',
                        source_type='violations',
                        contact_business=biz,
                        contact_address=f"{addr}, {city}, CA" if addr else f"{city}, CA",
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
            else:
                self.stdout.write(f"Inspection API also returned {resp.status_code}. Monitor needs configuration.")

        except Exception as e:
            logger.error(f"OSHA inspection fallback error: {e}")
            stats['errors'] += 1
