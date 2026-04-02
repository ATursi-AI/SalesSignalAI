"""
Seattle Code Complaints Monitor
API: https://data.seattle.gov/resource/ez4a-iug7.json  (Socrata SODA)
Dataset: Code complaints from the City of Seattle

Rich fields:
  - recordnum, recordtype, recordtypemapped, recordtypedesc
  - description, opendate, lastinspdate, lastinspresult, statuscurrent
  - originaladdress1, originalcity, originalstate, originalzip
  - link, latitude, longitude
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.seattle.gov/resource/ez4a-iug7.json'

VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber', 'sewer service'],
    'hot water': ['plumber'],
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'rat': ['pest control', 'exterminator'],
    'ventilation': ['HVAC'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'refriger': ['commercial refrigeration'],
    'cooler': ['commercial refrigeration'],
    'freezer': ['commercial refrigeration'],
    'hood': ['hood cleaning', 'HVAC'],
    'grease': ['hood cleaning', 'commercial cleaning'],
    'fire': ['fire safety'],
    'extinguisher': ['fire safety'],
    'suppression': ['fire safety'],
    'floor': ['general contractor', 'commercial cleaning'],
    'wall': ['general contractor'],
    'ceiling': ['general contractor'],
    'door': ['general contractor'],
    'window': ['general contractor'],
    'mold': ['mold remediation'],
    'clean': ['commercial cleaning', 'deep cleaning'],
    'sanitiz': ['commercial cleaning'],
    'trash': ['waste management'],
    'garbage': ['waste management'],
    'dumpster': ['waste management'],
    'paint': ['painter'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'light': ['electrician'],
}

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'general contractor']


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
    help = 'Monitor Seattle Code Complaints (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='seattle_code_complaints',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on complaints with inspection results
        params = {
            '$where': f"opendate >= '{since}'",
            '$select': (
                'recordnum,recordtype,recordtypemapped,recordtypedesc,'
                'description,opendate,lastinspdate,lastinspresult,statuscurrent,'
                'originaladdress1,originalcity,originalstate,originalzip,link,latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'opendate DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SEATTLE CODE COMPLAINTS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} code complaints from Seattle")

            for rec in records:
                recordnum = (rec.get('recordnum', '') or '').strip()
                recordtype = (rec.get('recordtype', '') or '').strip()
                recordtypemapped = (rec.get('recordtypemapped', '') or '').strip()
                recordtypedesc = (rec.get('recordtypedesc', '') or '').strip()
                description = (rec.get('description', '') or '').strip()
                opendate = rec.get('opendate', '')
                lastinspdate = rec.get('lastinspdate', '')
                lastinspresult = (rec.get('lastinspresult', '') or '').strip()
                statuscurrent = (rec.get('statuscurrent', '') or '').strip()
                originaladdress1 = (rec.get('originaladdress1', '') or '').strip()
                originalcity = (rec.get('originalcity', '') or '').strip() or 'Seattle'
                originalstate = (rec.get('originalstate', '') or '').strip() or 'WA'
                originalzip = (rec.get('originalzip', '') or '').strip()

                if not originaladdress1:
                    continue

                full_addr = f"{originaladdress1}, {originalcity}, {originalstate} {originalzip}".strip()
                display_name = originaladdress1

                # Detect services from description and type
                services = _detect_services(description + ' ' + recordtypedesc)

                # Urgency logic
                is_failed = lastinspresult.lower() == 'failed'
                is_case = recordtypemapped.lower() == 'case'

                if is_failed:
                    urgency = 'hot'
                    urgency_note = f'FAILED inspection result'
                elif is_case:
                    urgency = 'warm'
                    urgency_note = f'Case opened (violation, not just complaint)'
                else:
                    urgency = 'new'
                    urgency_note = f'Code complaint filed'

                # Parse open date
                posted_at = None
                if opendate:
                    try:
                        dt = datetime.fromisoformat(opendate.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'SEATTLE CODE COMPLAINT: {display_name}']
                content_parts.append(f'Address: {full_addr}')
                content_parts.append(f'Record Type: {recordtypedesc}')
                if description:
                    content_parts.append(f'Description: {description}')
                if lastinspresult:
                    content_parts.append(f'Latest Inspection: {lastinspresult}')
                if statuscurrent:
                    content_parts.append(f'Status: {statuscurrent}')
                if days_ago:
                    content_parts.append(f'Opened: {days_ago}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} — {recordtypedesc} — {urgency.upper()}")
                    if description:
                        self.stdout.write(f"         - {description[:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?recordnum={recordnum}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'seattle_code_complaints',
                            'recordnum': recordnum,
                            'record_type': recordtypemapped,
                            'address': full_addr,
                            'description': description,
                            'latest_inspection_result': lastinspresult,
                            'status': statuscurrent,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='WA',
                        region='Seattle',
                        source_group='public_records',
                        source_type='code_complaints',
                        contact_name=display_name,
                        contact_business=originaladdress1,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Seattle code complaint error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Seattle code complaints error: {e}")
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
