"""
LA Code Enforcement Monitor
API: https://data.lacity.org/resource/u82d-eh7z.json (Socrata SODA)
Dataset: Code Enforcement cases for Los Angeles

Key fields:
  - apno: case number
  - apname: inspection district
  - stno, stsub, predir, stname, suffix, postdir: address components
  - zip: postal code
  - adddttm: date case generated (Floating Timestamp)
  - resdttm: date case closed
  - aptype: case type (PACE, VEIP, CNAP, etc.)
  - stat: status (O=open, C=closed)
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.lacity.org/resource/u82d-eh7z.json'

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

DEFAULT_SERVICES = ['general contractor', 'commercial cleaning', 'code compliance']


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
    help = 'Monitor LA Code Enforcement Cases (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='la_code_enforcement',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on open cases only
        params = {
            '$where': f"stat = 'O' AND adddttm >= '{since}'",
            '$select': (
                'apno,apname,stno,stsub,predir,stname,suffix,postdir,zip,'
                'adddttm,resdttm,prclid,aptype,apc,stat'
            ),
            '$limit': limit,
            '$order': 'adddttm DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  LA CODE ENFORCEMENT MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} open code enforcement cases from LA")

            for rec in records:
                apno = (rec.get('apno', '') or '').strip()
                apname = (rec.get('apname', '') or '').strip()
                stno = (rec.get('stno', '') or '').strip()
                stsub = (rec.get('stsub', '') or '').strip()
                predir = (rec.get('predir', '') or '').strip()
                stname = (rec.get('stname', '') or '').strip()
                suffix = (rec.get('suffix', '') or '').strip()
                postdir = (rec.get('postdir', '') or '').strip()
                zipcode = (rec.get('zip', '') or '').strip()
                adddttm = rec.get('adddttm', '')
                resdttm = rec.get('resdttm', '')
                aptype = (rec.get('aptype', '') or '').strip()
                apc = (rec.get('apc', '') or '').strip()

                # Build address from components
                address_parts = [stno, stsub, predir, stname, suffix, postdir]
                full_addr = ' '.join(p for p in address_parts if p)

                if not full_addr:
                    continue

                full_addr_with_zip = f"{full_addr}, Los Angeles, CA {zipcode}".strip()

                # Parse case generation date
                posted_at = None
                if adddttm:
                    try:
                        dt = datetime.fromisoformat(adddttm.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                # Calculate days open
                days_open = 0
                if posted_at:
                    days_open = (timezone.now() - posted_at).days

                # Determine urgency
                is_enforcement_action = any(
                    prog in aptype.upper()
                    for prog in ['PACE', 'VEIP', 'CNAP']
                )

                if is_enforcement_action:
                    urgency = 'hot'
                    urgency_note = f'Enforcement action program: {aptype}'
                elif days_open > 30:
                    urgency = 'warm'
                    urgency_note = f'Open for {days_open} days'
                else:
                    urgency = 'new'
                    urgency_note = f'Recent case (open {days_open} days)'

                # Detect services from case type
                services = _detect_services(aptype)

                # Build rich content
                content_parts = [f'LA CODE ENFORCEMENT: {full_addr}']
                content_parts.append(f'Case #: {apno}')
                if aptype:
                    content_parts.append(f'Type: {aptype}')
                if apname:
                    content_parts.append(f'District: {apname}')
                if apc:
                    content_parts.append(f'APC: {apc}')
                if resdttm:
                    content_parts.append(f'Status: Closed')
                else:
                    content_parts.append(f'Status: OPEN ({days_open} days)')
                content_parts.append(f'Address: {full_addr_with_zip}')
                content_parts.append(f'Services needed: {", ".join(services[:5])}')
                content_parts.append(f'Urgency: {urgency_note}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(
                        f"  [DRY] {full_addr} — {aptype} — {urgency.upper()}"
                    )
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?apno={apno}',
                        content=content,
                        author=full_addr,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'la_code_enforcement',
                            'case_number': apno,
                            'case_type': aptype,
                            'address': full_addr_with_zip,
                            'days_open': days_open,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CA',
                        region='Los Angeles',
                        source_group='public_records',
                        source_type='code_enforcement',
                        contact_name=full_addr,
                        contact_business=full_addr,
                        contact_address=full_addr_with_zip,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"LA code enforcement error for {full_addr}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"LA code enforcement monitor error: {e}")
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
