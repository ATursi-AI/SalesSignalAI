"""
Austin Construction Permits Monitor
API: https://data.austintexas.gov/resource/3syk-w9eu.json  (Socrata SODA)
Dataset: Construction permit applications with contractor and applicant details

Rich fields:
  - permittype, permit_type_desc, work_class, permit_class_mapped
  - contractor_company_name, contractor_full_name, contractor_phone, contractor_address1
  - applicant_full_name, applicant_org, applicant_phone
  - original_address1, original_city, original_state, original_zip
  - issue_date, status_current, total_job_valuation
  - description, latitude, longitude
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

SODA_URL = 'https://data.austintexas.gov/resource/3syk-w9eu.json'

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

DEFAULT_SERVICES = ['general contractor', 'commercial cleaning', 'project management']


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
    help = 'Monitor Austin Construction Permits (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='austin_construction_permits',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Select relevant fields
        params = {
            '$where': f"issue_date >= '{since}'",
            '$select': (
                'permittype,permit_type_desc,permit_number,permit_class_mapped,'
                'work_class,permit_location,description,issue_date,status_current,'
                'original_address1,original_city,original_state,original_zip,'
                'total_job_valuation,contractor_company_name,contractor_full_name,'
                'contractor_phone,contractor_address1,applicant_full_name,'
                'applicant_org,applicant_phone,latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'issue_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  AUSTIN CONSTRUCTION PERMITS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} construction permits from Austin")

            for rec in records:
                permit_number = (rec.get('permit_number', '') or '').strip()
                permit_type = (rec.get('permit_type_desc', '') or '').strip()
                work_class = (rec.get('work_class', '') or '').strip()
                description = (rec.get('description', '') or '').strip()
                issue_date = rec.get('issue_date', '')
                status = (rec.get('status_current', '') or '').strip()

                # Address components
                address1 = (rec.get('original_address1', '') or '').strip()
                city = (rec.get('original_city', '') or 'Austin').strip()
                state = (rec.get('original_state', '') or 'TX').strip()
                zipcode = (rec.get('original_zip', '') or '').strip()

                # Contractor info
                contractor_name = (rec.get('contractor_full_name', '') or '').strip()
                contractor_company = (rec.get('contractor_company_name', '') or '').strip()
                contractor_phone = (rec.get('contractor_phone', '') or '').strip()
                contractor_addr = (rec.get('contractor_address1', '') or '').strip()

                # Applicant info
                applicant_name = (rec.get('applicant_full_name', '') or '').strip()
                applicant_org = (rec.get('applicant_org', '') or '').strip()
                applicant_phone = (rec.get('applicant_phone', '') or '').strip()

                # Job valuation
                try:
                    job_value = float(rec.get('total_job_valuation', 0) or 0)
                except (ValueError, TypeError):
                    job_value = 0

                if not address1:
                    continue

                full_addr = f"{address1}, {city}, {state} {zipcode}".strip()

                # Determine primary contact (applicant preferred, fallback to contractor)
                contact_name = applicant_name or contractor_name or address1
                contact_phone = applicant_phone or contractor_phone or ''
                contact_business = applicant_org or contractor_company or contact_name
                contact_address = full_addr

                # Detect services from description
                services = _detect_services(description)

                # Urgency based on job valuation
                if job_value > 500000:
                    urgency = 'hot'
                    urgency_note = f'Major construction project — ${job_value:,.0f} valuation'
                elif job_value > 100000:
                    urgency = 'warm'
                    urgency_note = f'Significant project — ${job_value:,.0f} valuation'
                else:
                    urgency = 'new'
                    urgency_note = f'Construction project — ${job_value:,.0f} valuation'

                # Parse issue date
                posted_at = None
                if issue_date:
                    try:
                        dt = datetime.fromisoformat(issue_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'AUSTIN CONSTRUCTION PERMIT: {contact_name}']
                content_parts.append(f'Permit #: {permit_number}')
                content_parts.append(f'Type: {permit_type}' if permit_type else '')
                content_parts.append(f'Address: {full_addr}')
                content_parts.append(f'Work Class: {work_class}' if work_class else '')
                content_parts.append(f'Status: {status}' if status else '')
                content_parts.append(f'Valuation: ${job_value:,.0f}')

                if applicant_name:
                    content_parts.append(f'Applicant: {applicant_name}')
                if applicant_org:
                    content_parts.append(f'Applicant Org: {applicant_org}')
                if applicant_phone:
                    content_parts.append(f'Applicant Phone: {applicant_phone}')

                if contractor_name:
                    content_parts.append(f'Contractor: {contractor_name}')
                if contractor_company:
                    content_parts.append(f'Contractor Company: {contractor_company}')
                if contractor_phone:
                    content_parts.append(f'Contractor Phone: {contractor_phone}')
                if contractor_addr:
                    content_parts.append(f'Contractor Address: {contractor_addr}')

                if description:
                    content_parts.append(f'Description: {description[:300]}')

                if days_ago:
                    content_parts.append(f'Issued: {days_ago}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')

                # Remove empty parts
                content_parts = [p for p in content_parts if p]
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(
                        f"  [DRY] {contact_name} @ {address1} — "
                        f"${job_value:,.0f} — {urgency.upper()}"
                    )
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?permit_number={permit_number}',
                        content=content,
                        author=contact_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'austin_construction_permits',
                            'permit_number': permit_number,
                            'permit_type': permit_type,
                            'work_class': work_class,
                            'job_valuation': job_value,
                            'applicant_name': applicant_name,
                            'applicant_org': applicant_org,
                            'applicant_phone': applicant_phone,
                            'contractor_name': contractor_name,
                            'contractor_company': contractor_company,
                            'contractor_phone': contractor_phone,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='TX',
                        region='Austin',
                        source_group='public_records',
                        source_type='construction_permits',
                        contact_name=contact_name,
                        contact_phone=contact_phone,
                        contact_business=contact_business,
                        contact_address=contact_address,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Austin construction permit error for {contact_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Austin construction permits error: {e}")
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
