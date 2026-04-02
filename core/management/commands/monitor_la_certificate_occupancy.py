"""
LA Certificate of Occupancy Monitor
API: https://data.lacity.org/resource/3f9m-afei.json (Socrata SODA)
Dataset: LA Certificate of Occupancy cases with contractor and applicant contact info

Key fields:
  - cofo_number: certificate ID
  - cofo_issue_date: when certificate was issued
  - latest_status: current status
  - permit_type, permit_sub_type, permit_category: permit classification
  - address_start, street_direction, street_name, street_suffix, zip_code: property address
  - work_description, valuation: project details
  - contractors_business_name, contractor_address: contractor info
  - principal_first_name, principal_last_name: principal applicant
  - applicant_first_name, applicant_last_name, applicant_business_name: applicant info
  - zone, occupancy: zoning and occupancy classification
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.lacity.org/resource/3f9m-afei.json'

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
    'cooling': ['HVAC'],
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
    'roof': ['roofing'],
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
    'tile': ['tile contractor'],
    'flooring': ['flooring contractor'],
    'carpet': ['flooring contractor'],
}

DEFAULT_SERVICES = ['general contractor', 'commercial cleaning', 'building permits']


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
    help = 'Monitor LA Certificate of Occupancy (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='la_certificate_occupancy',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        # Focus on recently issued certificates
        params = {
            '$where': f"cofo_issue_date >= '{since}'",
            '$select': (
                'cofo_number,cofo_issue_date,latest_status,pcis_permit,'
                'permit_type,permit_sub_type,work_description,valuation,'
                'address_start,street_direction,street_name,street_suffix,zip_code,'
                'contractors_business_name,contractor_address,contractor_city,'
                'principal_first_name,principal_last_name,'
                'applicant_first_name,applicant_last_name,applicant_business_name,'
                'zone,occupancy'
            ),
            '$limit': limit,
            '$order': 'cofo_issue_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  LA CERTIFICATE OF OCCUPANCY MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} certificates of occupancy from LA")

            for rec in records:
                cofo_number = (rec.get('cofo_number', '') or '').strip()
                cofo_issue_date = rec.get('cofo_issue_date', '')
                latest_status = (rec.get('latest_status', '') or '').strip()
                permit_type = (rec.get('permit_type', '') or '').strip()
                permit_sub_type = (rec.get('permit_sub_type', '') or '').strip()
                work_description = (rec.get('work_description', '') or '').strip()
                valuation_str = (rec.get('valuation', '') or '').strip()

                # Parse valuation as float
                valuation = 0.0
                try:
                    if valuation_str:
                        valuation = float(valuation_str)
                except (ValueError, TypeError):
                    pass

                # Build property address from components
                address_start = (rec.get('address_start', '') or '').strip()
                street_direction = (rec.get('street_direction', '') or '').strip()
                street_name = (rec.get('street_name', '') or '').strip()
                street_suffix = (rec.get('street_suffix', '') or '').strip()
                zipcode = (rec.get('zip_code', '') or '').strip()

                address_parts = [address_start, street_direction, street_name, street_suffix]
                property_address = ' '.join(p for p in address_parts if p)

                if not property_address:
                    continue

                full_addr = f"{property_address}, Los Angeles, CA {zipcode}".strip()

                # Get contractor info
                contractors_business = (rec.get('contractors_business_name', '') or '').strip()
                contractor_address = (rec.get('contractor_address', '') or '').strip()

                # Get applicant/principal contact
                applicant_first = (rec.get('applicant_first_name', '') or '').strip()
                applicant_last = (rec.get('applicant_last_name', '') or '').strip()
                applicant_business = (rec.get('applicant_business_name', '') or '').strip()
                principal_first = (rec.get('principal_first_name', '') or '').strip()
                principal_last = (rec.get('principal_last_name', '') or '').strip()

                # Determine contact name (applicant preferred over principal)
                contact_name = ''
                if applicant_first or applicant_last:
                    contact_name = f"{applicant_first} {applicant_last}".strip()
                elif principal_first or principal_last:
                    contact_name = f"{principal_first} {principal_last}".strip()

                # Determine contact business
                contact_business = applicant_business or contractors_business or ''

                # Parse issue date
                posted_at = None
                if cofo_issue_date:
                    try:
                        dt = datetime.fromisoformat(cofo_issue_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                # Determine urgency based on valuation
                if valuation > 500000:
                    urgency = 'hot'
                    urgency_note = f'High-value project: ${valuation:,.0f}'
                elif valuation > 100000:
                    urgency = 'warm'
                    urgency_note = f'Medium-value project: ${valuation:,.0f}'
                else:
                    urgency = 'new'
                    urgency_note = f'Project valuation: ${valuation:,.0f}' if valuation > 0 else 'New permit issued'

                # Detect services from work description
                services = _detect_services(work_description)

                # Build rich content
                content_parts = [f'LA CERTIFICATE OF OCCUPANCY: {property_address}']
                content_parts.append(f'Certificate #: {cofo_number}')
                if permit_type:
                    content_parts.append(f'Permit Type: {permit_type}')
                if permit_sub_type:
                    content_parts.append(f'Subtype: {permit_sub_type}')
                if work_description:
                    content_parts.append(f'Work: {work_description[:200]}')
                content_parts.append(f'Valuation: ${valuation:,.0f}')
                content_parts.append(f'Status: {latest_status}')
                content_parts.append(f'Address: {full_addr}')
                if contact_name:
                    content_parts.append(f'Applicant: {contact_name}')
                if contact_business:
                    content_parts.append(f'Business: {contact_business}')
                if contractors_business:
                    content_parts.append(f'Contractor: {contractors_business}')
                content_parts.append(f'Services needed: {", ".join(services[:5])}')
                content_parts.append(f'Urgency: {urgency_note}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(
                        f"  [DRY] {property_address} — ${valuation:,.0f} — {urgency.upper()}"
                    )
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?cofo_number={cofo_number}',
                        content=content,
                        author=contact_name or property_address,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'la_certificate_occupancy',
                            'certificate_number': cofo_number,
                            'permit_type': permit_type,
                            'work_description': work_description,
                            'valuation': valuation,
                            'address': full_addr,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CA',
                        region='Los Angeles',
                        source_group='public_records',
                        source_type='certificate_of_occupancy',
                        contact_name=contact_name or property_address,
                        contact_business=contact_business,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"LA CoFO error for {property_address}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"LA certificate of occupancy monitor error: {e}")
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
