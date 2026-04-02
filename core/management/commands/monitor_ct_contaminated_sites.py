"""
Connecticut Contaminated Sites Monitor
API: https://data.ct.gov/resource/xcxg-6jqp.json (Socrata SODA)
Dataset: REACT program contaminated sites, updated regularly

Rich fields:
  - site_id_site_id (REACT site ID), case_number, agency_id
  - case_program (VCP/IOP/IHWCA/MSD/LPST), case_name, case_address
  - official_town, case_status, cleanup_stage
  - env_use_restrictions_in_place, engineered_controls_in_place
  - site_id_longitude, site_id_latitude
"""
import logging
import re
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.ct.gov/resource/xcxg-6jqp.json'

ENVIRONMENTAL_SERVICE_MAP = {
    'waste': ['waste management', 'hazardous waste cleanup'],
    'hazardous': ['hazardous waste cleanup', 'environmental remediation'],
    'spill': ['spill response', 'environmental remediation'],
    'contamination': ['environmental remediation', 'soil testing'],
    'tank': ['tank removal', 'storage tank remediation'],
    'storage': ['tank removal', 'storage tank remediation'],
    'discharge': ['water treatment', 'environmental remediation'],
    'emission': ['air quality monitoring', 'environmental remediation'],
    'water': ['water treatment', 'environmental remediation'],
    'soil': ['soil testing', 'environmental remediation'],
    'air quality': ['air quality monitoring', 'environmental remediation'],
    'asbestos': ['asbestos abatement', 'environmental remediation'],
    'petroleum': ['petroleum cleanup', 'environmental remediation'],
    'chemical': ['chemical remediation', 'environmental remediation'],
    'cleanup': ['environmental remediation'],
    'remediation': ['environmental remediation'],
    'contaminated': ['environmental remediation', 'soil testing'],
    'investigation': ['environmental remediation', 'site assessment'],
}

DEFAULT_SERVICES = ['environmental remediation', 'soil testing']


def _detect_services(text):
    if not text:
        return DEFAULT_SERVICES
    text_lower = text.lower()
    services = set()
    for key, svc_list in ENVIRONMENTAL_SERVICE_MAP.items():
        if key in text_lower:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


class Command(BaseCommand):
    help = 'Monitor Connecticut Contaminated Sites (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='ct_contaminated_sites',
            details={'limit': limit},
        )

        # Only fetch open cases
        params = {
            '$where': "case_status = 'Open'",
            '$select': (
                'site_id_site_id,case_number,agency_id,case_program,'
                'case_name,case_address,official_town,case_status,'
                'cleanup_stage,env_use_restrictions_in_place,'
                'engineered_controls_in_place,site_id_longitude,site_id_latitude'
            ),
            '$limit': limit,
            '$order': 'case_number DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CONNECTICUT CONTAMINATED SITES MONITOR")
        self.stdout.write(f"  Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} open contaminated sites from Connecticut")

            for rec in records:
                site_id = (rec.get('site_id_site_id', '') or '').strip()
                case_number = (rec.get('case_number', '') or '').strip()
                agency_id = (rec.get('agency_id', '') or '').strip()
                case_program = (rec.get('case_program', '') or '').strip()
                case_name = (rec.get('case_name', '') or '').strip()
                case_address = (rec.get('case_address', '') or '').strip()
                official_town = (rec.get('official_town', '') or '').strip()
                case_status = (rec.get('case_status', '') or '').strip()
                cleanup_stage = (rec.get('cleanup_stage', '') or '').strip()
                env_use_restrictions = rec.get('env_use_restrictions_in_place', False)
                engineered_controls = rec.get('engineered_controls_in_place', False)
                site_longitude = rec.get('site_id_longitude', '')
                site_latitude = rec.get('site_id_latitude', '')

                if not case_number or not case_name:
                    continue

                # Build display name and address
                contact_name = case_name
                contact_business = case_name
                full_addr = f"{case_address}, {official_town}, CT".strip()

                # Detect services from case name and stage
                all_text = f"{case_name} {case_program} {cleanup_stage}".lower()
                services = _detect_services(all_text)

                # Urgency logic based on cleanup stage
                is_investigation_started = 'investigation started' in cleanup_stage.lower()
                is_cleanup_started = 'cleanup started' in cleanup_stage.lower() or 'remedial' in cleanup_stage.lower()
                has_engineered_controls = engineered_controls if isinstance(engineered_controls, bool) else (str(engineered_controls).lower() == 'true')

                if is_cleanup_started:
                    urgency = 'hot'
                    urgency_note = 'CLEANUP STARTED — Active remediation in progress'
                elif is_investigation_started:
                    urgency = 'warm'
                    urgency_note = 'INVESTIGATION STARTED — Site assessment underway'
                elif has_engineered_controls:
                    urgency = 'warm'
                    urgency_note = 'ENGINEERED CONTROLS IN PLACE — Ongoing management required'
                else:
                    urgency = 'new'
                    urgency_note = 'OPEN CASE — Status monitoring'

                # Map program type for context
                program_map = {
                    'VCP': 'Voluntary Cleanup Program',
                    'IOP': 'Innocent Owner Program',
                    'IHWCA': 'Industrial Hazardous Waste Cleanup',
                    'MSD': 'Municipal Setting',
                    'LPST': 'Leaking Petroleum Storage Tank',
                }
                program_name = program_map.get(case_program, case_program)

                # Posted_at as current time for active sites
                posted_at = timezone.now()

                # Build rich content
                content_parts = [f'CONNECTICUT CONTAMINATED SITE: {case_name}']
                content_parts.append(f'Site ID: {site_id}')
                content_parts.append(f'Case Number: {case_number}')
                content_parts.append(f'Town: {official_town}')
                content_parts.append(f'Address: {case_address}')
                content_parts.append(f'Program: {program_name}')
                content_parts.append(f'Status: {case_status}')
                content_parts.append(f'Cleanup Stage: {cleanup_stage}')

                if env_use_restrictions:
                    content_parts.append(f'Environmental Use Restrictions: IN PLACE')
                if has_engineered_controls:
                    content_parts.append(f'Engineered Controls: IN PLACE')

                if site_longitude and site_latitude:
                    content_parts.append(f'Coordinates: {site_latitude}, {site_longitude}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(
                        f"  [DRY] {case_name} ({official_town}, CT) — {program_name} — {urgency.upper()}"
                    )
                    self.stdout.write(f"         - Stage: {cleanup_stage}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?case_number={case_number}',
                        content=content,
                        author=contact_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'ct_contaminated_sites',
                            'site_id': site_id,
                            'case_number': case_number,
                            'case_name': case_name,
                            'agency_id': agency_id,
                            'case_program': case_program,
                            'program_name': program_name,
                            'town': official_town,
                            'case_status': case_status,
                            'cleanup_stage': cleanup_stage,
                            'env_use_restrictions': env_use_restrictions,
                            'engineered_controls': has_engineered_controls,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CT',
                        region=official_town,
                        source_group='public_records',
                        source_type='environmental_violations',
                        contact_name=contact_name,
                        contact_business=contact_business,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"CT contaminated site error for {case_number}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"CT contaminated sites error: {e}")
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
