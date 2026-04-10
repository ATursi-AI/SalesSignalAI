"""
Scrape SODA datasets from the DatasetRegistry.
Uses stored field mappings to extract contact info and create leads.

Usage:
    python manage.py scrape_registry --dataset-id 43nn-pn8j --days 3 --dry-run
    python manage.py scrape_registry --state NY --days 7
    python manage.py scrape_registry --all --limit 500
"""
import logging
import time
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models.data_sources import DatasetRegistry, ScrapeRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

DELAY_BETWEEN_DATASETS = 2.0


def _parse_date(date_str):
    """Parse various date formats from SODA APIs."""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d',
                '%m/%d/%Y', '%Y%m%d', '%m/%d/%y']:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = timezone.make_aware(dt)
            return dt
        except (ValueError, TypeError):
            continue
    return None


def _build_content(record, dataset):
    """Build a human-readable content string from a record."""
    parts = [f"{dataset.name} ({dataset.state})"]

    # Try common address fields
    addr_parts = []
    for f in ['street_number', 'stno', 'house_number', 'street_name', 'stname',
              'street_suffix', 'suffix', 'address', 'site_address']:
        v = record.get(f, '')
        if v:
            addr_parts.append(str(v).strip())
    if addr_parts:
        parts.append(f"Address: {' '.join(addr_parts)}")

    # Try common name/business fields
    for f in ['dba', 'business_name', 'respondent_name', 'owner_name', 'corp_name',
              'legalname', 'name', 'establishment_name', 'camis']:
        v = record.get(f, '')
        if v:
            parts.append(f"Business: {v}")
            break

    # Try description/violation fields
    for f in ['violation_description', 'description', 'violation_type', 'aptype',
              'nov_category_description', 'permit_type_definition']:
        v = record.get(f, '')
        if v:
            parts.append(str(v)[:300])
            break

    # Date
    if dataset.date_field:
        v = record.get(dataset.date_field, '')
        if v:
            parts.append(f"Date: {v}")

    return '\n'.join(parts)


def _scrape_dataset(dataset, days, limit, dry_run):
    """Scrape a single dataset. Returns stats dict."""
    run = ScrapeRun.objects.create(dataset=dataset, status='running')
    stats = {'fetched': 0, 'created': 0, 'duplicates': 0, 'errors': 0,
             'with_phone': 0, 'with_name': 0}

    url = f"https://{dataset.portal_domain}/resource/{dataset.dataset_id}.json"
    params = {'$limit': limit, '$order': ':id'}

    # Date filter
    if dataset.date_field and days:
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        params['$where'] = f"{dataset.date_field} > '{since}'"

    # Status filter (e.g. stat='O' for LA building)
    if dataset.status_field:
        existing_where = params.get('$where', '')
        status_clause = f"{dataset.status_field}='O'"  # default: open
        if existing_where:
            params['$where'] = f"{existing_where} AND {status_clause}"
        else:
            params['$where'] = status_clause

    # Auth token
    headers = {}
    app_token = getattr(settings, 'SODA_APP_TOKEN', '') or getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    if app_token:
        headers['X-App-Token'] = app_token

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        if resp.status_code != 200:
            error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            run.error_message = error_msg
            run.finish(status='failed', error_message=error_msg)
            return stats, error_msg

        records = resp.json()
        stats['fetched'] = len(records)

    except Exception as e:
        error_msg = str(e)
        run.finish(status='failed', error_message=error_msg)
        return stats, error_msg

    # Process records
    for record in records:
        try:
            # Map contact fields
            phone = str(record.get(dataset.phone_field, '') or '').strip() if dataset.phone_field else ''
            name = str(record.get(dataset.name_field, '') or '').strip() if dataset.name_field else ''
            address = str(record.get(dataset.address_field, '') or '').strip() if dataset.address_field else ''

            if phone:
                stats['with_phone'] += 1
            if name:
                stats['with_name'] += 1

            # Try to find a business name from common fields
            biz = ''
            for f in ['dba', 'business_name', 'owner_s_business_name', 'owner_business_name',
                       'corp_name', 'legalname', 'name', 'establishment_name']:
                v = record.get(f, '')
                if v:
                    biz = str(v).strip()
                    break

            content = _build_content(record, dataset)
            event_date = _parse_date(record.get(dataset.date_field, '')) if dataset.date_field else None

            if dry_run:
                label = f"  [DRY] {name or biz or 'unknown'}"
                if phone:
                    label += f" | {phone}"
                if address:
                    label += f" | {address[:40]}"
                print(label)
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=url,
                content=content,
                author=name or biz or '',
                posted_at=event_date,
                raw_data=record,
                state=dataset.state,
                region=dataset.city or '',
                source_group='public_records',
                source_type=dataset.data_type,
                contact_phone=phone,
                contact_name=name,
                contact_address=address,
                contact_business=biz,
            )

            if created:
                stats['created'] += 1
            else:
                stats['duplicates'] += 1

        except Exception as e:
            stats['errors'] += 1
            logger.error(f"Error processing record from {dataset.name}: {e}")

    # Update ScrapeRun
    run.records_fetched = stats['fetched']
    run.leads_created = stats['created']
    run.duplicates = stats['duplicates']
    run.errors = stats['errors']
    run.pct_with_phone = round(stats['with_phone'] / max(stats['fetched'], 1) * 100, 1)
    run.pct_with_name = round(stats['with_name'] / max(stats['fetched'], 1) * 100, 1)

    # Update dataset last_checked
    dataset.last_checked = timezone.now()
    dataset.total_records = stats['fetched']
    dataset.save(update_fields=['last_checked', 'total_records', 'updated_at'])

    if stats['errors'] and stats['created'] == 0:
        run.finish(status='failed')
    elif stats['errors']:
        run.finish(status='partial')
    else:
        run.finish(status='success')

    return stats, None


class Command(BaseCommand):
    help = 'Scrape SODA datasets from the DatasetRegistry'

    def add_arguments(self, parser):
        parser.add_argument('--dataset-id', type=str, help='Specific dataset_id to scrape')
        parser.add_argument('--state', type=str, help='Scrape all active datasets for a state (NY, CA, etc.)')
        parser.add_argument('--all', action='store_true', help='Scrape all active datasets')
        parser.add_argument('--days', type=int, default=7, help='Lookback days (default: 7)')
        parser.add_argument('--limit', type=int, default=1000, help='Max records per dataset (default: 1000)')
        parser.add_argument('--dry-run', action='store_true', help='Fetch and log without creating leads')

    def handle(self, *args, **options):
        dataset_id = options.get('dataset_id')
        state = options.get('state')
        scrape_all = options.get('all')
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        # Build queryset
        datasets = DatasetRegistry.objects.filter(is_active=True)
        if dataset_id:
            datasets = datasets.filter(dataset_id=dataset_id)
        elif state:
            datasets = datasets.filter(state=state.upper())
        elif not scrape_all:
            self.stdout.write(self.style.ERROR('Specify --dataset-id, --state, or --all'))
            return

        count = datasets.count()
        if count == 0:
            self.stdout.write(self.style.WARNING('No matching active datasets found.'))
            return

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  REGISTRY SCRAPER — {count} dataset(s)")
        self.stdout.write(f"  Days: {days} | Limit: {limit} | Dry run: {dry_run}")
        self.stdout.write(f"{'='*60}\n")

        total = {'fetched': 0, 'created': 0, 'duplicates': 0, 'errors': 0}

        for i, ds in enumerate(datasets):
            if i > 0:
                time.sleep(DELAY_BETWEEN_DATASETS)

            self.stdout.write(f"\n--- [{ds.state}] {ds.name} ({ds.dataset_id}) ---")
            stats, error = _scrape_dataset(ds, days, limit, dry_run)

            if error:
                self.stdout.write(self.style.ERROR(f"  ERROR: {error[:100]}"))
            else:
                phone_pct = round(stats['with_phone'] / max(stats['fetched'], 1) * 100)
                self.stdout.write(
                    f"  {stats['fetched']} fetched, {stats['created']} leads, "
                    f"{stats['duplicates']} dupes, {stats['errors']} errors "
                    f"| {phone_pct}% have phone"
                )

            for k in total:
                total[k] += stats.get(k, 0)

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(
            f"  TOTAL: {total['fetched']} fetched, {total['created']} leads, "
            f"{total['duplicates']} duplicates, {total['errors']} errors"
        )
        self.stdout.write(f"{'='*60}\n")
