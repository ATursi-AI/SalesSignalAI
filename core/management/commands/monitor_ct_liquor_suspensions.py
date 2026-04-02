"""
Connecticut Liquor License Suspensions Monitor
API: https://data.ct.gov/resource/i2yq-278d.json (Socrata SODA)
Dataset: Liquor license suspensions with start dates

Rich fields:
  - business, credential, suspended_through_and_including
  - start_date_of_suspension, address, town
  - State: CT, Region: town
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.ct.gov/resource/i2yq-278d.json'

VIOLATION_SERVICE_MAP = {
    'suspend': ['alcohol beverage consultant', 'compliance attorney'],
    'license': ['business attorney', 'compliance attorney'],
    'liquor': ['alcohol beverage consultant'],
    'permit': ['business attorney'],
}

DEFAULT_SERVICES = ['alcohol beverage consultant', 'business attorney']


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
    help = 'Monitor Connecticut Liquor License Suspensions (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='ct_liquor_suspensions',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        params = {
            '$where': f"start_date_of_suspension >= '{since}'",
            '$select': 'business,credential,suspended_through_and_including,start_date_of_suspension,address,town',
            '$limit': limit,
            '$order': 'start_date_of_suspension DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CONNECTICUT LIQUOR SUSPENSIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} suspensions from Connecticut")

            for rec in records:
                business = (rec.get('business', '') or '').strip()
                credential = (rec.get('credential', '') or '').strip()
                suspended_through = (rec.get('suspended_through_and_including', '') or '').strip()
                start_date = rec.get('start_date_of_suspension', '')
                address = (rec.get('address', '') or '').strip()
                town = (rec.get('town', '') or '').strip()

                if not address or not business:
                    continue

                full_addr = f"{address}, {town}, CT".strip()
                display_name = business

                # All suspensions are warm urgency (active suspension)
                urgency = 'warm'
                urgency_note = f'License suspended'

                # Parse start date
                posted_at = None
                if start_date:
                    try:
                        dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                services = _detect_services(credential + ' suspension')

                # Build rich content
                content_parts = [f'CONNECTICUT LIQUOR SUSPENSION: {display_name}']
                content_parts.append(f'Business: {display_name}')
                content_parts.append(f'Address: {full_addr}')
                if credential:
                    content_parts.append(f'Credential: {credential[:200]}')
                if start_date:
                    content_parts.append(f'Suspended: {start_date}')
                if suspended_through:
                    content_parts.append(f'Suspended Through: {suspended_through}')
                if days_ago:
                    content_parts.append(f'Date: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:4])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {address} — suspended — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?business={business}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'ct_liquor_suspensions',
                            'business_name': display_name,
                            'address': full_addr,
                            'credential': credential,
                            'suspended_through': suspended_through,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CT',
                        region=town,
                        source_group='public_records',
                        source_type='liquor_suspensions',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"CT liquor suspension error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"CT liquor suspensions error: {e}")
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
