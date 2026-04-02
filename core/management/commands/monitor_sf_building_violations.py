"""
SF Building Violations (Notices of Violation) Monitor
API: https://data.sfgov.org/resource/nbtm-fbw5.json
Dataset: 511K records, updated daily

Fields used for rich violation detail:
  - complaint_number, date_filed, status
  - street_number, street_name, street_suffix, unit, zipcode
  - nov_category_description  (general category)
  - item                      (specific ordinances violated)
  - nov_item_description      (inspector comments about the violation)
  - code_violation_desc       (description of code violations)
  - receiving_division, assigned_division
  - work_without_permit, additional_work_beyond_permit,
    expired_permit, cancelled_permit, unsafe_building   (boolean flags)
  - neighborhoods_analysis_boundaries
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

# Violation keywords -> services needed
VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber', 'sewer service'],
    'pipe': ['plumber'],
    'water heater': ['plumber'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'light': ['electrician'],
    'fire': ['fire safety', 'electrician'],
    'smoke detector': ['fire safety'],
    'sprinkler': ['fire safety'],
    'extinguisher': ['fire safety'],
    'roof': ['roofer', 'general contractor'],
    'stair': ['general contractor'],
    'handrail': ['general contractor'],
    'foundation': ['general contractor', 'structural engineer'],
    'structural': ['general contractor', 'structural engineer'],
    'window': ['general contractor', 'window repair'],
    'door': ['general contractor'],
    'elevator': ['elevator repair'],
    'mold': ['mold remediation'],
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'rat': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'paint': ['painter'],
    'lead paint': ['lead abatement', 'painter'],
    'heating': ['HVAC'],
    'ventilation': ['HVAC'],
    'hvac': ['HVAC'],
    'boiler': ['HVAC', 'plumber'],
    'trash': ['waste management', 'commercial cleaning'],
    'garbage': ['waste management', 'commercial cleaning'],
    'fence': ['fencing contractor'],
    'sidewalk': ['concrete contractor'],
    'concrete': ['concrete contractor'],
    'permit': ['general contractor'],
    'demolition': ['demolition contractor'],
}

DEFAULT_SERVICES = ['general contractor', 'plumber', 'electrician']


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
    help = 'Monitor SF Building Violations'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='sf_building_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        url = 'https://data.sfgov.org/resource/nbtm-fbw5.json'
        params = {
            '$where': f"date_filed > '{since}'",
            '$select': (
                'complaint_number,item_sequence_number,date_filed,status,'
                'street_number,street_name,street_suffix,unit,zipcode,'
                'nov_category_description,item,nov_item_description,'
                'code_violation_desc,receiving_division,assigned_division,'
                'work_without_permit,additional_work_beyond_permit,'
                'expired_permit,cancelled_permit,unsafe_building,'
                'neighborhoods_analysis_boundaries'
            ),
            '$limit': limit,
            '$order': 'date_filed DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SF BUILDING VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} violation records from SF")

            # Group by complaint_number (multiple items per complaint)
            complaints = {}
            for rec in records:
                complaint = rec.get('complaint_number', '')
                if not complaint:
                    continue

                if complaint not in complaints:
                    parts = [
                        rec.get('street_number', ''),
                        rec.get('street_name', ''),
                        rec.get('street_suffix', ''),
                    ]
                    address = ' '.join(p for p in parts if p).strip()
                    unit = rec.get('unit', '')
                    if unit:
                        address += f" #{unit}"

                    complaints[complaint] = {
                        'complaint': complaint,
                        'address': address,
                        'zipcode': rec.get('zipcode', ''),
                        'neighborhood': rec.get('neighborhoods_analysis_boundaries', ''),
                        'date_filed': rec.get('date_filed', ''),
                        'status': rec.get('status', ''),
                        'receiving_division': rec.get('receiving_division', ''),
                        'assigned_division': rec.get('assigned_division', ''),
                        'items': [],
                        'flags': {
                            'unsafe_building': False,
                            'work_without_permit': False,
                            'additional_work_beyond_permit': False,
                            'expired_permit': False,
                            'cancelled_permit': False,
                        },
                    }

                # Collect violation items
                item_data = {}
                category = rec.get('nov_category_description', '').strip()
                ordinance = rec.get('item', '').strip()
                description = rec.get('nov_item_description', '').strip()
                code_desc = rec.get('code_violation_desc', '').strip()

                if category or ordinance or description or code_desc:
                    item_data['category'] = category
                    item_data['ordinance'] = ordinance
                    item_data['description'] = description
                    item_data['code_violation'] = code_desc
                    complaints[complaint]['items'].append(item_data)

                # Collect boolean flags
                flags = complaints[complaint]['flags']
                if str(rec.get('unsafe_building', '')).lower() in ('true', '1', 'yes'):
                    flags['unsafe_building'] = True
                if str(rec.get('work_without_permit', '')).lower() in ('true', '1', 'yes'):
                    flags['work_without_permit'] = True
                if str(rec.get('additional_work_beyond_permit', '')).lower() in ('true', '1', 'yes'):
                    flags['additional_work_beyond_permit'] = True
                if str(rec.get('expired_permit', '')).lower() in ('true', '1', 'yes'):
                    flags['expired_permit'] = True
                if str(rec.get('cancelled_permit', '')).lower() in ('true', '1', 'yes'):
                    flags['cancelled_permit'] = True

            self.stdout.write(f"Grouped into {len(complaints)} unique complaints")

            # Process each complaint
            for complaint_num, comp in complaints.items():
                address = comp['address']
                zipcode = comp['zipcode']
                neighborhood = comp['neighborhood']
                date_filed = comp['date_filed']
                status = comp['status']
                items = comp['items']
                flags = comp['flags']
                full_addr = f"{address}, San Francisco, CA {zipcode}".strip()

                # Determine urgency
                is_critical = flags['unsafe_building']
                flag_labels = []
                if flags['unsafe_building']:
                    flag_labels.append('UNSAFE BUILDING')
                if flags['work_without_permit']:
                    flag_labels.append('Work Without Permit')
                if flags['additional_work_beyond_permit']:
                    flag_labels.append('Work Beyond Permit Scope')
                if flags['expired_permit']:
                    flag_labels.append('Expired Permit')
                if flags['cancelled_permit']:
                    flag_labels.append('Cancelled Permit')

                # Check violation text for critical keywords
                all_text = ' '.join(
                    (i.get('description', '') + ' ' + i.get('code_violation', ''))
                    for i in items
                )
                if any(kw in all_text.lower() for kw in
                       ['unsafe', 'imminent', 'hazard', 'structural', 'fire',
                        'collapse', 'emergency', 'condemned']):
                    is_critical = True

                if is_critical or len(items) >= 3:
                    urgency = 'hot'
                    urgency_note = 'Critical building violation — immediate action needed'
                elif flag_labels or len(items) >= 2:
                    urgency = 'warm'
                    urgency_note = 'Active building violation requiring remediation'
                else:
                    urgency = 'new'
                    urgency_note = 'Building code violation filed'

                services = _detect_services(all_text)

                # Build rich content
                content_parts = [f'SF Building Violation: #{complaint_num}']
                content_parts.append(f'Address: {full_addr}')
                if neighborhood:
                    content_parts.append(f'Neighborhood: {neighborhood}')
                if comp['assigned_division']:
                    content_parts.append(f'Division: {comp["assigned_division"]}')
                content_parts.append(f'Status: {status}')

                if flag_labels:
                    content_parts.append(f'Flags: {", ".join(flag_labels)}')

                content_parts.append(f'Violations: {len(items)} item(s)')

                for i, item in enumerate(items[:5]):
                    parts = []
                    if item.get('category'):
                        parts.append(item['category'])
                    if item.get('ordinance'):
                        parts.append(f"Ordinance: {item['ordinance']}")
                    if item.get('code_violation'):
                        parts.append(item['code_violation'][:200])
                    elif item.get('description'):
                        parts.append(item['description'][:200])

                    prefix = f'  [{i+1}] '
                    content_parts.append(prefix + ' | '.join(parts))

                if date_filed:
                    try:
                        dt = datetime.fromisoformat(date_filed.replace('Z', '+00:00'))
                        days_ago = (timezone.now() - timezone.make_aware(dt.replace(tzinfo=None))).days
                        content_parts.append(f'Filed: {days_ago} days ago')
                    except Exception:
                        content_parts.append(f'Filed: {date_filed}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                # Parse filed date
                posted_at = None
                if date_filed:
                    try:
                        dt = datetime.fromisoformat(date_filed.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                if dry_run:
                    self.stdout.write(f"  [DRY] #{complaint_num}: {full_addr}")
                    self.stdout.write(f"         {len(items)} violations | {urgency.upper()}")
                    if flag_labels:
                        self.stdout.write(f"         Flags: {', '.join(flag_labels)}")
                    for item in items[:3]:
                        desc = item.get('code_violation') or item.get('description') or item.get('category', '')
                        self.stdout.write(f"         - {desc[:100]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'https://data.sfgov.org/resource/nbtm-fbw5.json?complaint_number={complaint_num}',
                        content=content,
                        author=f'Complaint #{complaint_num}',
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_building_violations',
                            'complaint_number': complaint_num,
                            'address': full_addr,
                            'neighborhood': neighborhood,
                            'status': status,
                            'division': comp['assigned_division'],
                            'violation_count': len(items),
                            'flags': flag_labels,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='public_records',
                        source_type='violations',
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF violation error for #{complaint_num}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"SF building violations error: {e}")
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
