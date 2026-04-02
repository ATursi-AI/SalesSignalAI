"""
Texas TCEQ Environmental Remediation Monitor
API: https://data.texas.gov/resource/bssc-pq4k.json (Socrata SODA)
Dataset: TCEQ remediation correspondence and environmental enforcement actions

Rich fields:
  - pgm_area_cd (program area: IHWCA=hazardous waste, LPST=leaking tank, etc.)
  - lgl_ident_txt, comm_id, comm_recd_sent_dt, comm_doc_dt
  - alt_reg_ent_name, dir_cd, comm_typ_cd, deliv_txt, phys_loc_desc_txt
  - city_name, cnty_name
  - State: TX, Region: cnty_name (county)
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.texas.gov/resource/bssc-pq4k.json'

VIOLATION_SERVICE_MAP = {
    'remediation': ['environmental remediation', 'environmental consultant'],
    'hazard': ['environmental remediation', 'hazmat specialist'],
    'waste': ['waste management', 'environmental remediation'],
    'contamination': ['environmental remediation', 'remediation contractor'],
    'soil': ['soil remediation specialist'],
    'groundwater': ['groundwater remediation'],
    'tank': ['tank removal', 'environmental remediation'],
    'fuel': ['fuel spill response'],
    'cleanup': ['environmental remediation', 'cleanup contractor'],
}

DEFAULT_SERVICES = ['environmental remediation', 'environmental consultant']


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
    help = 'Monitor Texas TCEQ Environmental Remediation (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='tceq_remediation',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        params = {
            '$where': f"comm_recd_sent_dt >= '{since}'",
            '$select': (
                'pgm_area_cd,lgl_ident_txt,comm_recd_sent_dt,dir_cd,'
                'comm_typ_cd,comm_doc_dt,comm_id,alt_reg_ent_name,'
                'deliv_txt,phys_loc_desc_txt,city_name,cnty_name'
            ),
            '$limit': limit,
            '$order': 'comm_recd_sent_dt DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  TEXAS TCEQ REMEDIATION MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} remediation records from TCEQ")

            for rec in records:
                pgm_area = (rec.get('pgm_area_cd', '') or '').strip()
                lgl_ident = (rec.get('lgl_ident_txt', '') or '').strip()
                site_name = (rec.get('alt_reg_ent_name', '') or '').strip()
                comm_date = rec.get('comm_recd_sent_dt', '')
                comm_type = (rec.get('comm_typ_cd', '') or '').strip()
                deliv_txt = (rec.get('deliv_txt', '') or '').strip()
                phys_loc = (rec.get('phys_loc_desc_txt', '') or '').strip()
                city_name = (rec.get('city_name', '') or '').strip()
                cnty_name = (rec.get('cnty_name', '') or '').strip()
                comm_id = (rec.get('comm_id', '') or '').strip()

                if not site_name and not deliv_txt and not phys_loc:
                    continue

                display_name = site_name or lgl_ident or 'TCEQ Site'
                location = deliv_txt or phys_loc or city_name
                full_addr = f"{location}, {city_name}, TX".strip() if location else f"{city_name}, TX".strip()

                # Determine urgency based on program area
                is_hazwaste = pgm_area == 'IHWCA'
                is_leaking_tank = pgm_area == 'LPST'

                if is_hazwaste or is_leaking_tank:
                    urgency = 'hot'
                    urgency_note = f'{pgm_area} - High priority remediation'
                else:
                    urgency = 'warm'
                    urgency_note = f'{pgm_area} - Environmental remediation required'

                # Parse communication date
                posted_at = None
                if comm_date:
                    try:
                        dt = datetime.fromisoformat(comm_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                services = _detect_services(comm_type + ' ' + pgm_area)

                # Build rich content
                content_parts = [f'TEXAS TCEQ REMEDIATION: {display_name}']
                content_parts.append(f'Site: {display_name}')
                if location:
                    content_parts.append(f'Location: {full_addr}')
                if pgm_area:
                    content_parts.append(f'Program: {pgm_area}')
                if comm_type:
                    content_parts.append(f'Communication Type: {comm_type}')
                if lgl_ident:
                    content_parts.append(f'Legal ID: {lgl_ident}')
                if days_ago:
                    content_parts.append(f'Date: {days_ago}')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:4])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} ({pgm_area}) — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?comm_id={comm_id}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'tceq_remediation',
                            'site_name': display_name,
                            'location': full_addr,
                            'program_area': pgm_area,
                            'communication_type': comm_type,
                            'legal_id': lgl_ident,
                            'comm_id': comm_id,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='TX',
                        region=cnty_name,
                        source_group='public_records',
                        source_type='environmental_remediation',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"TCEQ remediation error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"TCEQ remediation error: {e}")
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
