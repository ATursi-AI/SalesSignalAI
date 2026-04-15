"""
Montgomery County, MD Permits Monitor
API: https://data.montgomerycountymd.gov/resource/{dataset_id}.json (Socrata SODA)
Covers: Commercial, Demolition, Mechanical, Fence, Electrical, ROW, Well permits
Datasets: 7 Socrata endpoints, updated periodically

Field variations:
- Most datasets: permitno, addeddate, stno, stname, suffix, city, state, zip
- Fence/Well: permit_number, added_date, street_number, street_name, street_suffix
All have: status, description/description_of_work, declaredvaluation, issueddate/issue_date
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

MC_DATASETS = {
    'commercial': {
        'url': 'https://data.montgomerycountymd.gov/resource/i26v-w6bd.json',
        'label': 'Commercial Permit',
        'date_field': 'addeddate',
    },
    'demolition': {
        'url': 'https://data.montgomerycountymd.gov/resource/b6ht-fw3x.json',
        'label': 'Demolition Permit',
        'date_field': 'addeddate',
    },
    'mechanical': {
        'url': 'https://data.montgomerycountymd.gov/resource/ih88-a6aa.json',
        'label': 'Mechanical Permit',
        'date_field': 'addeddate',
    },
    'fence': {
        'url': 'https://data.montgomerycountymd.gov/resource/9quz-avmj.json',
        'label': 'Fence Permit',
        'date_field': 'added_date',
    },
    'electrical': {
        'url': 'https://data.montgomerycountymd.gov/resource/qxie-8qnp.json',
        'label': 'Electrical Permit',
        'date_field': 'addeddate',
    },
    'right_of_way': {
        'url': 'https://data.montgomerycountymd.gov/resource/2b9e-mbxk.json',
        'label': 'Public ROW Permit',
        'date_field': 'addeddate',
    },
    'well': {
        'url': 'https://data.montgomerycountymd.gov/resource/vddw-tec2.json',
        'label': 'Well Permit',
        'date_field': 'added_date',
    },
}

VIOLATION_SERVICE_MAP = {
    'commercial': ['general contractor', 'commercial construction'],
    'demolition': ['demolition contractor', 'general contractor'],
    'mechanical': ['HVAC', 'mechanical contractor'],
    'fence': ['fence contractor', 'wood contractor'],
    'electrical': ['electrician', 'electrical contractor'],
    'right of way': ['general contractor', 'site prep'],
    'well': ['well drilling', 'water systems'],
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'cooling': ['HVAC'],
    'ventilation': ['HVAC'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'lighting': ['electrician'],
    'roof': ['roofing contractor'],
    'roofing': ['roofing contractor'],
    'siding': ['general contractor'],
    'door': ['general contractor'],
    'window': ['general contractor'],
    'construction': ['general contractor'],
    'contractor': ['general contractor'],
    'build': ['general contractor'],
}

DEFAULT_SERVICES = ['general contractor']


def _detect_services(permit_type, description, worktype):
    """
    Detect services from permit type, description, and worktype fields.
    """
    text = f"{permit_type} {description} {worktype}".lower()

    # Check permit type first
    for permit_key, svc_list in VIOLATION_SERVICE_MAP.items():
        if permit_key in permit_type.lower():
            return svc_list

    # Check description and worktype
    services = set()
    for key, svc_list in VIOLATION_SERVICE_MAP.items():
        if key in text:
            services.update(svc_list)

    return list(services) if services else DEFAULT_SERVICES


class Command(BaseCommand):
    help = 'Monitor Montgomery County, MD Permits (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='mc_permits',
            details={'days': days, 'limit': limit, 'datasets': len(MC_DATASETS)},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  MONTGOMERY COUNTY PERMITS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit} (per dataset)")
        self.stdout.write(f"  Datasets: {len(MC_DATASETS)}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}
        dataset_stats = {}

        # Divide limit across all datasets
        limit_per_dataset = max(1, limit // len(MC_DATASETS))

        for dataset_key, dataset_config in MC_DATASETS.items():
            dataset_stats[dataset_key] = {'created': 0, 'errors': 0, 'items': 0}

            url = dataset_config['url']
            label = dataset_config['label']
            date_field = dataset_config['date_field']

            # Build query for this dataset
            try:
                # Socrata query: select common fields, filter by date, limit results
                params = {
                    '$where': f"{date_field} >= '{since}T00:00:00'",
                    '$order': f'{date_field} DESC',
                    '$limit': limit_per_dataset,
                }

                self.stdout.write(f"\nFetching {label}...", ending=' ')

                resp = requests.get(url, params=params, timeout=60)
                resp.raise_for_status()
                records = resp.json()
                dataset_stats[dataset_key]['items'] = len(records)
                stats['items_scraped'] += len(records)

                self.stdout.write(f"Got {len(records)} records")

                for rec in records:
                    try:
                        # Extract permit identifier
                        permit_no = (rec.get('permitno') or rec.get('permit_number') or rec.get('permit_no') or '').strip()
                        if not permit_no:
                            continue

                        # Extract address components (handle field name variations)
                        street_num = (rec.get('stno') or rec.get('street_number') or rec.get('st_no') or '').strip()
                        street_name = (rec.get('stname') or rec.get('street_name') or rec.get('st_name') or '').strip()
                        street_suffix = (rec.get('suffix') or rec.get('street_suffix') or '').strip()
                        city = (rec.get('city') or '').strip()
                        state = rec.get('state', 'MD')
                        zipcode = (rec.get('zip') or rec.get('zip_code') or '').strip()

                        # Build full address
                        addr_parts = [street_num, street_name, street_suffix]
                        addr_parts = [p for p in addr_parts if p]
                        full_addr = ', '.join(addr_parts)

                        if not full_addr:
                            continue

                        full_addr_with_city = f"{full_addr}, {city}, {state} {zipcode}".strip()

                        # Extract permit details
                        status = (rec.get('status') or '').strip()
                        description = (rec.get('description') or rec.get('description_of_work') or '').strip()
                        worktype = (rec.get('worktype') or rec.get('work_type') or '').strip()
                        declared_value = rec.get('declaredvaluation', 0)
                        if isinstance(declared_value, str):
                            try:
                                declared_value = float(declared_value.replace('$', '').replace(',', ''))
                            except (ValueError, AttributeError):
                                declared_value = 0

                        issued_date_str = rec.get('issueddate') or rec.get('issue_date') or rec.get('issued_date') or ''
                        added_date_str = rec.get(date_field) or ''

                        # Parse posted_at from added/issued date
                        posted_at = None
                        for date_str in [added_date_str, issued_date_str]:
                            if date_str:
                                try:
                                    dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                                    if dt.tzinfo:
                                        posted_at = dt
                                    else:
                                        posted_at = timezone.make_aware(dt)
                                    break
                                except Exception:
                                    pass

                        # Detect services from permit type, description, worktype
                        services = _detect_services(label, description, worktype)

                        # Determine urgency based on permit type and value
                        if dataset_key in ('commercial', 'demolition'):
                            if declared_value > 500000:
                                urgency = 'hot'
                                urgency_note = f'Large permit: ${declared_value:,.0f}'
                            elif declared_value > 100000:
                                urgency = 'warm'
                                urgency_note = f'Medium permit: ${declared_value:,.0f}'
                            else:
                                urgency = 'new'
                                urgency_note = f'Permit value: ${declared_value:,.0f}'
                        else:
                            urgency = 'new'
                            urgency_note = f'{status}'

                        # Build rich content
                        content_parts = [f'{label.upper()}: {permit_no}']
                        content_parts.append(f'Permit #: {permit_no}')
                        content_parts.append(f'Address: {full_addr_with_city}')
                        content_parts.append(f'Type: {label}')
                        if status:
                            content_parts.append(f'Status: {status}')
                        if declared_value > 0:
                            content_parts.append(f'Value: ${declared_value:,.0f}')
                        if description:
                            content_parts.append(f'Description: {description[:200]}')
                        if worktype:
                            content_parts.append(f'Work Type: {worktype[:100]}')
                        content_parts.append(f'Urgency: {urgency_note}')
                        content_parts.append(f'Services needed: {", ".join(services[:4])}')

                        content = '\n'.join(content_parts)

                        if dry_run:
                            self.stdout.write(
                                f"  [DRY] {label} #{permit_no} @ {full_addr} — "
                                f"${declared_value:,.0f} — {urgency.upper()}"
                            )
                            dataset_stats[dataset_key]['created'] += 1
                            continue

                        # Process lead
                        try:
                            lead, created, num_assigned = process_lead(
                                platform='public_records',
                                source_url=f'{url}?permit_number={permit_no}',
                                content=content,
                                author=permit_no,
                                posted_at=posted_at,
                                raw_data={
                                    'data_source': 'montgomery_county_permits',
                                    'permit_type': dataset_key,
                                    'permit_label': label,
                                    'permit_number': permit_no,
                                    'address': full_addr_with_city,
                                    'status': status,
                                    'declared_value': declared_value,
                                    'description': description,
                                    'worktype': worktype,
                                    'urgency': urgency,
                                    'services_mapped': services,
                                },
                                state='MD',
                                region='Montgomery County',
                                source_group='public_records',
                                source_type='mc_permits',
                                contact_name=full_addr,
                                contact_business=full_addr,
                                contact_address=full_addr_with_city,
                            )
                            if created:
                                stats['created'] += 1
                                dataset_stats[dataset_key]['created'] += 1
                            else:
                                stats['duplicates'] += 1
                        except Exception as e:
                            logger.error(f"MC permit error ({label} #{permit_no}): {e}")
                            stats['errors'] += 1
                            dataset_stats[dataset_key]['errors'] += 1

                    except Exception as e:
                        logger.error(f"MC permit record error ({label}): {e}")
                        stats['errors'] += 1
                        dataset_stats[dataset_key]['errors'] += 1

            except Exception as e:
                logger.error(f"MC permits {dataset_key} fetch error: {e}")
                stats['errors'] += 1
                dataset_stats[dataset_key]['errors'] += 1
                self.stdout.write(self.style.ERROR(f"  Error fetching {label}: {e}"))

        # Update run stats
        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['items_scraped']
        run.details['dataset_stats'] = dataset_stats
        run.finish(status='success' if not stats['errors'] else 'partial')

        # Print summary
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write("DATASET SUMMARY:")
        for dataset_key, ds_stats in dataset_stats.items():
            label = MC_DATASETS[dataset_key]['label']
            self.stdout.write(
                f"  {label:20s} | {ds_stats['items']:3d} records | "
                f"{ds_stats['created']:3d} created | {ds_stats['errors']:2d} errors"
            )
        self.stdout.write(f"{'='*60}")
        self.stdout.write(
            f"\nTOTAL: {stats['created']} created, "
            f"{stats['duplicates']} dupes, {stats['errors']} errors "
            f"({stats['items_scraped']} records scanned)"
        )
