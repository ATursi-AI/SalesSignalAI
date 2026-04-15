"""
Texas TCEQ Environmental Violations Monitor
API: https://data.texas.gov/resource/gyd4-wuys.json (Socrata SODA)
Dataset: ~250K violation records, updated regularly

Rich fields:
  - invstn_num, investigation_approved_date, notice_of_violation_id
  - viol_tracking_nbr, curr_viol_status, viol_status_dt
  - allegation_txt (violation description), resol_desc_txt (resolution)
  - repeat_ind (repeat offender), viol_cmpln_due_dt (compliance due date)
  - class_cd, cat_cd (A/B/C severity), invstn_typ, cnty_name, tceq_region
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

SODA_URL = 'https://data.texas.gov/resource/gyd4-wuys.json'

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
}

DEFAULT_SERVICES = ['environmental remediation', 'hazardous waste cleanup']


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
    help = 'Monitor Texas TCEQ Environmental Violations (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='tceq_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        params = {
            '$where': f"viol_status_dt >= '{since}'",
            '$select': (
                'invstn_num,investigation_approved_date,notice_of_violation_id,'
                'viol_tracking_nbr,curr_viol_status,viol_status_dt,allegation_txt,'
                'resol_desc_txt,repeat_ind,viol_cmpln_due_dt,class_cd,cat_cd,'
                'invstn_typ,cnty_name,tceq_region'
            ),
            '$limit': limit,
            '$order': 'viol_status_dt DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  TEXAS TCEQ VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} violations from TCEQ")

            for rec in records:
                invstn_num = (rec.get('invstn_num', '') or '').strip()
                notice_of_violation_id = (rec.get('notice_of_violation_id', '') or '').strip()
                viol_tracking_nbr = (rec.get('viol_tracking_nbr', '') or '').strip()
                curr_viol_status = (rec.get('curr_viol_status', '') or '').strip()
                viol_status_dt = rec.get('viol_status_dt', '')
                allegation_txt = (rec.get('allegation_txt', '') or '').strip()
                resol_desc_txt = (rec.get('resol_desc_txt', '') or '').strip()
                repeat_ind = rec.get('repeat_ind', False)
                viol_cmpln_due_dt = rec.get('viol_cmpln_due_dt', '')
                class_cd = (rec.get('class_cd', '') or '').strip()
                cat_cd = (rec.get('cat_cd', '') or '').strip()
                invstn_typ = (rec.get('invstn_typ', '') or '').strip()
                cnty_name = (rec.get('cnty_name', '') or '').strip()
                tceq_region = (rec.get('tceq_region', '') or '').strip()
                investigation_approved_date = rec.get('investigation_approved_date', '')

                if not invstn_num:
                    continue

                # Build display name and address
                contact_name = f"TCEQ Investigation {invstn_num}"
                contact_business = f"TCEQ {tceq_region} Region" if tceq_region else "TCEQ"
                contact_address = f"{cnty_name} County, TX" if cnty_name else "Texas"

                # Detect services from violation description
                services = _detect_services(allegation_txt)

                # Urgency logic
                is_category_a = cat_cd.upper() == 'A'
                is_repeat = repeat_ind if isinstance(repeat_ind, bool) else (str(repeat_ind).lower() == 'true')

                if is_category_a or is_repeat:
                    urgency = 'hot'
                    if is_repeat:
                        urgency_note = 'REPEAT OFFENDER — Category violation'
                    else:
                        urgency_note = f'CATEGORY A VIOLATION — Most serious'
                elif cat_cd.upper() == 'B':
                    urgency = 'warm'
                    urgency_note = 'Category B violation — Significant concern'
                else:
                    urgency = 'new'
                    urgency_note = 'Category C violation — Baseline concern'

                # Parse violation date
                posted_at = None
                if viol_status_dt:
                    try:
                        dt = datetime.fromisoformat(viol_status_dt.replace('Z', '+00:00'))
                        posted_at = dt if dt.tzinfo else timezone.make_aware(dt)
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'TEXAS TCEQ VIOLATION: Investigation {invstn_num}']
                content_parts.append(f'County: {cnty_name}')
                content_parts.append(f'Region: {tceq_region}')
                if invstn_typ:
                    content_parts.append(f'Investigation Type: {invstn_typ}')
                content_parts.append(f'Violation Tracking: {viol_tracking_nbr}')
                content_parts.append(f'Status: {curr_viol_status}')
                if cat_cd:
                    content_parts.append(f'Category: {cat_cd}')
                if class_cd:
                    content_parts.append(f'Class: {class_cd}')
                if is_repeat:
                    content_parts.append(f'Repeat Offender: YES')
                if days_ago:
                    content_parts.append(f'Violation Date: {days_ago}')
                if viol_cmpln_due_dt:
                    content_parts.append(f'Compliance Due: {viol_cmpln_due_dt}')

                if allegation_txt:
                    content_parts.append(f'Allegation: {allegation_txt[:300]}')
                if resol_desc_txt:
                    content_parts.append(f'Resolution: {resol_desc_txt[:300]}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(
                        f"  [DRY] {contact_name} ({cnty_name}) — {cat_cd} — {urgency.upper()}"
                    )
                    if allegation_txt:
                        self.stdout.write(f"         - {allegation_txt[:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?invstn_num={invstn_num}',
                        content=content,
                        author=contact_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'tceq_violations',
                            'invstn_num': invstn_num,
                            'notice_of_violation_id': notice_of_violation_id,
                            'viol_tracking_nbr': viol_tracking_nbr,
                            'county': cnty_name,
                            'region': tceq_region,
                            'investigation_type': invstn_typ,
                            'category': cat_cd,
                            'class': class_cd,
                            'status': curr_viol_status,
                            'repeat_offender': is_repeat,
                            'violation_description': allegation_txt,
                            'resolution': resol_desc_txt,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='TX',
                        region=cnty_name,
                        source_group='public_records',
                        source_type='environmental_violations',
                        contact_name=contact_name,
                        contact_business=contact_business,
                        contact_address=contact_address,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"TCEQ violation error for {invstn_num}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"TCEQ violations error: {e}")
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
