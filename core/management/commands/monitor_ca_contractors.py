"""
Monitor California Contractor State License Board (CSLB) for new licenses.
Newly licensed contractors are potential SalesSignalAI customers — they just
started a business and need customers.

Data: CSLB public lookup + CA Secretary of State business filings.
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

# CA Secretary of State new business filings via data.ca.gov
# This is the most reliable open-data source for new CA businesses
CA_SOS_API = 'https://data.ca.gov/api/3/action/datastore_search'
CA_SOS_RESOURCE = ''  # Placeholder — needs actual resource ID from data.ca.gov


class Command(BaseCommand):
    help = 'Monitor CA new contractor licenses / business filings'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=500)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--region', type=str, default='')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']
        region = options.get('region', '')

        run = MonitorRun.objects.create(
            monitor_name='ca_contractors',
            details={'days': days, 'region': region},
        )

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CA CONTRACTOR / BUSINESS FILING MONITOR")
        self.stdout.write(f"  Days: {days} | Limit: {limit} | Region: {region or 'all'}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'skipped': 0}

        if not CA_SOS_RESOURCE:
            self.stdout.write(self.style.WARNING(
                "CA SOS resource ID not configured.\n"
                "To enable: set CA_SOS_RESOURCE in monitor_ca_contractors.py\n"
                "Find the resource at: https://data.ca.gov/dataset\n"
                "Search for: 'business filings' or 'CSLB license'\n"
            ))
            # Try OSHA establishment data as alternative (covers CA businesses)
            self._fetch_from_osha_establishments(days, limit, dry_run, stats, region)
        else:
            self._fetch_from_ca_sos(days, limit, dry_run, stats, region)

        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['created'] + stats['duplicates'] + stats['skipped']
        run.finish(status='success' if not stats['errors'] else 'partial')

        self.stdout.write(f"\nResults: {stats['created']} created, {stats['duplicates']} dupes, {stats['errors']} errors")

    def _fetch_from_ca_sos(self, days, limit, dry_run, stats, region):
        """Fetch from CA Secretary of State open data."""
        try:
            resp = requests.get(CA_SOS_API, params={
                'resource_id': CA_SOS_RESOURCE,
                'limit': limit,
                'sort': 'filing_date desc',
            }, timeout=30)

            if resp.status_code != 200:
                self.stdout.write(f"CA SOS API returned {resp.status_code}")
                stats['errors'] += 1
                return

            data = resp.json()
            records = data.get('result', {}).get('records', [])
            self.stdout.write(f"Fetched {len(records)} records from CA SOS")

            for rec in records:
                biz = rec.get('ENTITY_NAME', '') or rec.get('business_name', '')
                city = rec.get('CITY', '') or rec.get('city', '')
                filing_date = rec.get('FILING_DATE', '') or rec.get('filing_date', '')

                if region and region.lower() not in city.lower():
                    stats['skipped'] += 1
                    continue

                content = (
                    f"New CA Business Filing: {biz}\n"
                    f"City: {city}, CA\n"
                    f"Filed: {filing_date}\n"
                    f"Newly registered business — needs customers."
                )

                if dry_run:
                    self.stdout.write(f"  [DRY] {biz} | {city}")
                    stats['created'] += 1
                    continue

                lead, created, _ = process_lead(
                    platform='public_records',
                    source_url='https://bizfileonline.sos.ca.gov/',
                    content=content,
                    author=biz,
                    raw_data=rec,
                    state='CA',
                    region=city,
                    source_group='public_records',
                    source_type='business_filings',
                    contact_business=biz,
                    contact_address=f"{city}, CA",
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['duplicates'] += 1

        except Exception as e:
            logger.error(f"CA SOS fetch error: {e}")
            stats['errors'] += 1

    def _fetch_from_osha_establishments(self, days, limit, dry_run, stats, region):
        """Fallback: fetch CA establishments from federal OSHA inspection data."""
        try:
            # OSHA Inspection data — public API
            url = 'https://enforcedata.dol.gov/api/osha_inspection'
            cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            params = {
                'p_state': 'CA',
                'p_start_date': cutoff,
                'p_page_size': str(min(limit, 100)),
            }

            self.stdout.write(f"Trying OSHA inspections API for CA (last {days} days)...")
            resp = requests.get(url, params=params, timeout=30)

            if resp.status_code != 200:
                self.stdout.write(f"OSHA API returned {resp.status_code}. CA monitor needs configuration.")
                self.stdout.write("Once a data.ca.gov resource ID is added, this monitor will work automatically.")
                return

            records = resp.json() if isinstance(resp.json(), list) else resp.json().get('results', [])
            self.stdout.write(f"Fetched {len(records)} OSHA inspection records for CA")

            for rec in records:
                biz = rec.get('estab_name', '') or rec.get('establishment_name', '')
                city = rec.get('site_city', '')
                addr = rec.get('site_address', '')

                if not biz:
                    continue
                if region and region.lower() not in city.lower():
                    stats['skipped'] += 1
                    continue

                content = (
                    f"CA Business (OSHA Inspection): {biz}\n"
                    f"Address: {addr}, {city}, CA\n"
                    f"This business had a recent OSHA inspection."
                )

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
                    source_type='business_filings',
                    contact_business=biz,
                    contact_address=f"{addr}, {city}, CA" if addr else f"{city}, CA",
                )
                if created:
                    stats['created'] += 1
                else:
                    stats['duplicates'] += 1

        except Exception as e:
            logger.error(f"OSHA fallback error: {e}")
            stats['errors'] += 1
