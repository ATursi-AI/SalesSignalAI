"""
Connecticut Storage Tank Facility Monitor
API: https://data.ct.gov/resource/utni-rddb.json (Socrata SODA)
Dataset: Currently active storage tank installations with hazardous substance tracking

Rich fields:
  - facilityid, facilitynm, facilityaddr, facilitycity, facilityzip
  - agencytankid, tankstatuscd, capacitygalsnum, substancecd
  - closuretypecd, tankconstructioncd, installationdt, tanklat, tanklon
  - State: CT, Region: facilitycity
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.ct.gov/resource/utni-rddb.json'

HAZARDOUS_SUBSTANCES = {
    'gasoline', 'diesel', 'fuel oil', 'heating oil', 'kerosene',
    'propane', 'butane', 'benzene', 'toluene', 'xylene',
    'methanol', 'ethanol', 'chlorine', 'ammonia',
}

VIOLATION_SERVICE_MAP = {
    'storage': ['environmental remediation', 'tank removal'],
    'tank': ['tank maintenance', 'environmental remediation'],
    'fuel': ['fuel system specialist'],
    'hazard': ['environmental remediation', 'hazmat specialist'],
    'chemical': ['environmental remediation', 'hazmat specialist'],
    'contamination': ['environmental remediation'],
    'remediation': ['environmental remediation'],
}

DEFAULT_SERVICES = ['environmental remediation', 'tank maintenance']


def _detect_services(text):
    if not text:
        return DEFAULT_SERVICES
    text_lower = text.lower()
    services = set()
    for key, svc_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


def _is_hazardous_substance(substance_code):
    if not substance_code:
        return False
    return any(haz in substance_code.lower() for haz in HAZARDOUS_SUBSTANCES)


class Command(BaseCommand):
    help = 'Monitor Connecticut Storage Tank Facilities (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='ct_storage_tanks',
            details={'days': days, 'limit': limit},
        )

        params = {
            '$where': "tankstatuscd = 'Currently In Use'",
            '$select': (
                'facilityid,facilitynm,facilityaddr,facilitycity,facilityzip,'
                'agencytankid,tankstatuscd,capacitygalsnum,substancecd,'
                'closuretypecd,tankconstructioncd,installationdt,tanklat,tanklon'
            ),
            '$limit': limit,
            '$order': 'installationdt DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CONNECTICUT STORAGE TANKS MONITOR")
        self.stdout.write(f"  Status: Currently In Use | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} active storage tanks from Connecticut")

            for rec in records:
                facilityid = (rec.get('facilityid', '') or '').strip()
                facilitynm = (rec.get('facilitynm', '') or '').strip()
                facilityaddr = (rec.get('facilityaddr', '') or '').strip()
                facilitycity = (rec.get('facilitycity', '') or '').strip()
                facilityzip = (rec.get('facilityzip', '') or '').strip()
                agencytankid = (rec.get('agencytankid', '') or '').strip()
                capacitygalsnum = rec.get('capacitygalsnum', 0)
                substancecd = (rec.get('substancecd', '') or '').strip()
                installationdt = rec.get('installationdt', '')

                if not facilityaddr or not facilitynm:
                    continue

                full_addr = f"{facilityaddr}, {facilitycity}, CT {facilityzip}".strip()
                display_name = facilitynm

                # Determine urgency
                try:
                    capacity = float(capacitygalsnum) if capacitygalsnum else 0
                except (ValueError, TypeError):
                    capacity = 0

                is_large = capacity > 10000
                is_hazardous = _is_hazardous_substance(substancecd)

                if is_large:
                    urgency = 'hot'
                    urgency_note = f'Large capacity tank ({capacity:,.0f} gallons)'
                elif is_hazardous:
                    urgency = 'warm'
                    urgency_note = f'Hazardous substance: {substancecd}'
                else:
                    urgency = 'new'
                    urgency_note = f'Storage tank in use'

                # Parse installation date
                posted_at = None
                if installationdt:
                    try:
                        dt = datetime.fromisoformat(installationdt.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                services = _detect_services(substancecd + ' storage tank')

                # Build rich content
                content_parts = [f'CONNECTICUT STORAGE TANK: {display_name}']
                content_parts.append(f'Facility: {display_name}')
                content_parts.append(f'Address: {full_addr}')
                content_parts.append(f'Tank ID: {agencytankid}')
                if substancecd:
                    content_parts.append(f'Substance: {substancecd}')
                content_parts.append(f'Capacity: {capacity:,.0f} gallons')
                if installationdt:
                    content_parts.append(f'Installed: {installationdt}')
                content_parts.append(f'Status: Currently In Use')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:4])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {facilityaddr} — {capacity:,.0f} gal — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?facility={facilityid}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'ct_storage_tanks',
                            'facility_name': display_name,
                            'facility_id': facilityid,
                            'tank_id': agencytankid,
                            'address': full_addr,
                            'substance': substancecd,
                            'capacity_gallons': capacity,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CT',
                        region=facilitycity,
                        source_group='public_records',
                        source_type='storage_tanks',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"CT storage tank error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"CT storage tanks error: {e}")
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
