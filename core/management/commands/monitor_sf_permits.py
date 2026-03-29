"""
SF Building Permits Monitor
API: https://data.sfgov.org/resource/i98e-djp9.json
Dataset: 1M+ records, updated nightly
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
    help = 'Monitor SF Building Permits'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=1000)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--min-cost', type=int, default=0, help='Min estimated cost')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']
        min_cost = options['min_cost']

        run = MonitorRun.objects.create(
            monitor_name='sf_permits',
            details={'days': days, 'limit': limit, 'min_cost': min_cost},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        url = 'https://data.sfgov.org/resource/i98e-djp9.json'

        where = f"filed_date > '{since}'"
        if min_cost > 0:
            where += f" AND estimated_cost > '{min_cost}'"

        params = {'$where': where, '$limit': limit, '$order': 'filed_date DESC'}

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SF BUILDING PERMITS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit} | Min cost: ${min_cost}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0}

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            records = resp.json()
            self.stdout.write(f"Fetched {len(records)} permits from SF")

            for rec in records:
                permit = rec.get('permit_number', '')
                if not permit:
                    continue

                parts = [rec.get('street_number', ''), rec.get('street_name', ''), rec.get('street_suffix', '')]
                address = ' '.join(p for p in parts if p).strip()
                unit = rec.get('unit', '')
                if unit:
                    address += f" #{unit}"
                zipcode = rec.get('zipcode', '')
                permit_type = rec.get('permit_type_definition', '')
                desc = rec.get('description', '')
                cost = rec.get('estimated_cost', '')
                neighborhood = rec.get('neighborhoods_analysis_boundaries', '')
                filed = rec.get('filed_date', '')

                content = (
                    f"SF Building Permit: #{permit}\n"
                    f"Address: {address}, San Francisco, CA {zipcode}\n"
                    f"Type: {permit_type}\n"
                    f"Description: {desc}\n"
                    f"Estimated Cost: ${cost}\n"
                    f"Neighborhood: {neighborhood}\n"
                    f"Filed: {filed}"
                )

                if dry_run:
                    self.stdout.write(f"  [DRY] CA-SF-PRM-{permit}: {address} | ${cost} | {permit_type}")
                    stats['created'] += 1
                    continue

                lead, created, _ = process_lead(
                    platform='public_records',
                    source_url=f'https://data.sfgov.org/resource/i98e-djp9.json?permit_number={permit}',
                    content=content,
                    author=f'Permit #{permit}',
                    raw_data=rec,
                    state='CA',
                    region='San Francisco',
                    source_group='public_records',
                    source_type='permits',
                    contact_address=f"{address}, San Francisco, CA {zipcode}",
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['duplicates'] += 1

        except Exception as e:
            logger.error(f"SF permits error: {e}")
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['created'] + stats['duplicates']
        run.finish(status='success' if not stats['errors'] else 'partial')

        self.stdout.write(f"\nResults: {stats['created']} created, {stats['duplicates']} dupes, {stats['errors']} errors")
