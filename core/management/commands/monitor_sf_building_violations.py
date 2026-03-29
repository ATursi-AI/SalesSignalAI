"""
SF Building Violations (Notices of Violation) Monitor
API: https://data.sfgov.org/resource/nbtm-fbw5.json
Dataset: 510,597 records, updated daily
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')


class Command(BaseCommand):
    help = 'Monitor SF Building Violations'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=1000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='sf_building_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        url = 'https://data.sfgov.org/resource/nbtm-fbw5.json'
        params = {
            '$where': f"date_filed > '{since}'",
            '$limit': limit,
            '$order': 'date_filed DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SF BUILDING VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0}

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            records = resp.json()
            self.stdout.write(f"Fetched {len(records)} violations from SF")

            for rec in records:
                complaint = rec.get('complaint_number', '')
                if not complaint:
                    continue

                parts = [rec.get('street_number', ''), rec.get('street_name', ''), rec.get('street_suffix', '')]
                address = ' '.join(p for p in parts if p).strip()
                unit = rec.get('unit', '')
                if unit:
                    address += f" #{unit}"
                zipcode = rec.get('zipcode', '')
                category = rec.get('nov_category_description', '')
                detail = rec.get('nov_item_description', '')
                neighborhood = rec.get('neighborhoods_analysis_boundaries', '')
                filed = rec.get('date_filed', '')

                content = (
                    f"SF Building Violation: #{complaint}\n"
                    f"Address: {address}, San Francisco, CA {zipcode}\n"
                    f"Category: {category}\n"
                    f"Detail: {detail}\n"
                    f"Neighborhood: {neighborhood}\n"
                    f"Filed: {filed}"
                )

                if dry_run:
                    self.stdout.write(f"  [DRY] CA-SF-BLD-{complaint}: {address} | {category}")
                    stats['created'] += 1
                    continue

                lead, created, _ = process_lead(
                    platform='public_records',
                    source_url=f'https://data.sfgov.org/resource/nbtm-fbw5.json?complaint_number={complaint}',
                    content=content,
                    author=f'Complaint #{complaint}',
                    raw_data=rec,
                    state='CA',
                    region='San Francisco',
                    source_group='public_records',
                    source_type='violations',
                    contact_address=f"{address}, San Francisco, CA {zipcode}",
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['duplicates'] += 1

        except Exception as e:
            logger.error(f"SF building violations error: {e}")
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['created'] + stats['duplicates']
        run.finish(status='success' if not stats['errors'] else 'partial')

        self.stdout.write(f"\nResults: {stats['created']} created, {stats['duplicates']} dupes, {stats['errors']} errors")
