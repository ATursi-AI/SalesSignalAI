"""
LA Building Code Enforcement Monitor
API: https://data.lacity.org/resource/u82d-eh7z.json
Dataset: 28,836 open cases, updated weekly
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
    help = 'Monitor LA Building Code Enforcement cases'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=1000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='la_building_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        url = 'https://data.lacity.org/resource/u82d-eh7z.json'
        params = {
            '$where': f"adddttm > '{since}' AND stat='O'",
            '$limit': limit,
            '$order': 'adddttm DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  LA BUILDING CODE ENFORCEMENT MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0}

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            records = resp.json()
            self.stdout.write(f"Fetched {len(records)} open cases from LA")

            for rec in records:
                case_num = rec.get('apno', '')
                if not case_num:
                    continue

                parts = [rec.get('stno', ''), rec.get('predir', ''), rec.get('stname', ''),
                         rec.get('suffix', ''), rec.get('postdir', '')]
                address = ' '.join(p for p in parts if p).strip()
                zipcode = rec.get('zip', '')
                case_type = rec.get('aptype', '')
                district = rec.get('apname', '')
                case_date = rec.get('adddttm', '')

                content = (
                    f"LA Building Violation: Case #{case_num}\n"
                    f"Address: {address}, Los Angeles, CA {zipcode}\n"
                    f"Type: {case_type}\n"
                    f"District: {district}\n"
                    f"Date: {case_date}"
                )

                if dry_run:
                    self.stdout.write(f"  [DRY] CA-LA-BLD-{case_num}: {address}, {zipcode}")
                    stats['created'] += 1
                    continue

                lead, created, _ = process_lead(
                    platform='public_records',
                    source_url=f'https://data.lacity.org/resource/u82d-eh7z.json?apno={case_num}',
                    content=content,
                    author=f'Case #{case_num}',
                    raw_data=rec,
                    state='CA',
                    region='Los Angeles',
                    source_group='public_records',
                    source_type='building_violations',
                    contact_address=f"{address}, Los Angeles, CA {zipcode}",
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['duplicates'] += 1

        except Exception as e:
            logger.error(f"LA building violations error: {e}")
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['created'] + stats['duplicates']
        run.finish(status='success' if not stats['errors'] else 'partial')

        self.stdout.write(f"\nResults: {stats['created']} created, {stats['duplicates']} dupes, {stats['errors']} errors")
