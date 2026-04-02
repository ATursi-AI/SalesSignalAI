"""
Montgomery County Alcohol License Violations Monitor
API: https://data.montgomerycountymd.gov/resource/4tja-rkhg.json (Socrata SODA)
Dataset: Alcohol license violations with disposition records

Rich fields:
  - facilityname, address
  - violationdate, violation, disposition, dispositiondate
  - State: MD, Region: Montgomery County
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.montgomerycountymd.gov/resource/4tja-rkhg.json'

VIOLATION_SERVICE_MAP = {
    'suspend': ['alcohol beverage consultant', 'compliance attorney'],
    'revoke': ['alcohol beverage consultant', 'compliance attorney'],
    'fine': ['alcohol beverage consultant'],
    'citation': ['alcohol beverage consultant'],
    'sanitation': ['commercial cleaning'],
    'health': ['food safety consultant'],
    'fire': ['fire safety'],
    'tax': ['tax accountant'],
    'license': ['business attorney', 'compliance attorney'],
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
    help = 'Monitor Montgomery County Alcohol License Violations (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='mc_alcohol_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        params = {
            '$where': f"violationdate >= '{since}'",
            '$select': 'facilityname,address,violationdate,violation,disposition,dispositiondate',
            '$limit': limit,
            '$order': 'violationdate DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  MONTGOMERY COUNTY ALCOHOL VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} violations from Montgomery County, MD")

            for rec in records:
                facilityname = (rec.get('facilityname', '') or '').strip()
                address = (rec.get('address', '') or '').strip()
                violationdate = rec.get('violationdate', '')
                violation = (rec.get('violation', '') or '').strip()
                disposition = (rec.get('disposition', '') or '').strip()
                dispositiondate = rec.get('dispositiondate', '')

                if not address or not facilityname:
                    continue

                full_addr = f"{address}, Montgomery County, MD".strip()
                display_name = facilityname

                # Determine urgency based on disposition
                is_suspended = 'suspend' in disposition.lower()
                urgency = 'hot' if is_suspended else 'warm'
                urgency_note = f'License suspended' if is_suspended else f'Violation on record'

                # Parse violation date
                posted_at = None
                if violationdate:
                    try:
                        dt = datetime.fromisoformat(violationdate.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                services = _detect_services(violation + ' ' + disposition)

                # Build rich content
                content_parts = [f'MONTGOMERY COUNTY ALCOHOL VIOLATION: {display_name}']
                content_parts.append(f'Facility: {display_name}')
                content_parts.append(f'Address: {full_addr}')
                if violation:
                    content_parts.append(f'Violation: {violation[:200]}')
                if disposition:
                    content_parts.append(f'Disposition: {disposition[:200]}')
                if days_ago:
                    content_parts.append(f'Date: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:4])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {address} — {disposition} — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?facility={facilityname}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'mc_alcohol_violations',
                            'facility_name': display_name,
                            'address': full_addr,
                            'violation': violation,
                            'disposition': disposition,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='MD',
                        region='Montgomery County',
                        source_group='public_records',
                        source_type='alcohol_violations',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"MC alcohol violation error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"MC alcohol violations error: {e}")
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
