"""
Chicago Ordinance Violations Monitor (Administrative Hearings)
API: https://data.cityofchicago.org/resource/awqx-tuwv.json  (Socrata SODA)
Dataset: 829K rows, updated daily

Rich fields:
  - case_id, violation_date, violation_location
  - violation_code, violation_description
  - respondents (property owner / business name)
  - case_disposition, disposition_paid_date
  - imposed_fine, current_amount_due
  - hearing_date, hearing_status
  - issuing_department, violation_type
  - address, latitude, longitude
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.cityofchicago.org/resource/awqx-tuwv.json'

VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'fire': ['fire safety', 'electrician'],
    'smoke detector': ['fire safety'],
    'roof': ['roofer', 'general contractor'],
    'structural': ['general contractor', 'structural engineer'],
    'foundation': ['general contractor', 'structural engineer'],
    'masonry': ['masonry contractor'],
    'brick': ['masonry contractor'],
    'tuckpoint': ['masonry contractor'],
    'window': ['general contractor'],
    'door': ['general contractor'],
    'stair': ['general contractor'],
    'porch': ['general contractor'],
    'deck': ['general contractor'],
    'fence': ['fencing contractor'],
    'sidewalk': ['concrete contractor'],
    'concrete': ['concrete contractor'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'boiler': ['HVAC'],
    'mold': ['mold remediation'],
    'pest': ['pest control'],
    'rodent': ['pest control'],
    'rat': ['pest control'],
    'trash': ['waste management', 'commercial cleaning'],
    'garbage': ['waste management'],
    'debris': ['commercial cleaning', 'demolition contractor'],
    'vacant': ['general contractor', 'security'],
    'abandon': ['general contractor', 'security'],
    'demolition': ['demolition contractor'],
    'paint': ['painter'],
    'lead': ['lead abatement'],
    'asbestos': ['asbestos abatement'],
    'permit': ['general contractor'],
    'food': ['commercial cleaning', 'pest control'],
    'sanit': ['commercial cleaning'],
    'health': ['commercial cleaning'],
    'sign': ['sign contractor'],
    'parking': ['paving contractor'],
    'landscap': ['landscaping'],
    'tree': ['tree service'],
    'weed': ['landscaping'],
    'grass': ['landscaping'],
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
    help = 'Monitor Chicago Ordinance Violations / Administrative Hearings (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='chicago_ordinance_violations',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        params = {
            '$where': (
                f"violation_date >= '{since}' AND "
                f"case_disposition IS NOT NULL"
            ),
            '$select': (
                'case_id,violation_date,violation_location,'
                'violation_code,violation_description,'
                'respondents,case_disposition,imposed_fine,current_amount_due,'
                'hearing_date,hearing_status,'
                'issuing_department,violation_type,'
                'address,latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'violation_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CHICAGO ORDINANCE VIOLATIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} ordinance violations from Chicago")

            # Group by respondent + address for consolidated leads
            properties = {}
            for rec in records:
                address = (rec.get('address', '') or '').strip()
                respondent = (rec.get('respondents', '') or '').strip()
                if not address and not respondent:
                    continue

                key = f"{respondent}|{address}".lower()
                if key not in properties:
                    properties[key] = {
                        'address': address,
                        'respondent': respondent,
                        'latitude': rec.get('latitude', ''),
                        'longitude': rec.get('longitude', ''),
                        'cases': [],
                    }

                case = {
                    'case_id': rec.get('case_id', ''),
                    'date': rec.get('violation_date', ''),
                    'code': rec.get('violation_code', ''),
                    'description': (rec.get('violation_description', '') or '').strip(),
                    'location': (rec.get('violation_location', '') or '').strip(),
                    'disposition': (rec.get('case_disposition', '') or '').strip(),
                    'imposed_fine': rec.get('imposed_fine', ''),
                    'amount_due': rec.get('current_amount_due', ''),
                    'department': (rec.get('issuing_department', '') or '').strip(),
                    'violation_type': (rec.get('violation_type', '') or '').strip(),
                    'hearing_status': (rec.get('hearing_status', '') or '').strip(),
                }
                properties[key]['cases'].append(case)

            self.stdout.write(f"Grouped into {len(properties)} respondents/properties")

            for prop_key, prop in properties.items():
                address = prop['address']
                respondent = prop['respondent']
                cases = prop['cases']
                if not cases:
                    continue

                full_addr = f"{address}, Chicago, IL" if address else 'Chicago, IL'

                # Build all text for service detection
                all_text = ' '.join(c['description'] for c in cases)
                services = _detect_services(all_text)

                # Calculate total fines
                total_fine = 0
                total_due = 0
                for c in cases:
                    try:
                        total_fine += float(c['imposed_fine'] or 0)
                    except (ValueError, TypeError):
                        pass
                    try:
                        total_due += float(c['amount_due'] or 0)
                    except (ValueError, TypeError):
                        pass

                # Urgency: high fines or multiple cases
                if total_due >= 5000 or len(cases) >= 5:
                    urgency = 'hot'
                    urgency_note = f'${total_due:,.0f} outstanding — {len(cases)} violations'
                elif total_due >= 1000 or len(cases) >= 3:
                    urgency = 'warm'
                    urgency_note = f'${total_due:,.0f} in fines — {len(cases)} violations'
                else:
                    urgency = 'new'
                    urgency_note = 'Ordinance violation filed'

                # Most recent violation date
                dates = []
                for c in cases:
                    if c['date']:
                        try:
                            dt = datetime.fromisoformat(c['date'].replace('Z', '+00:00'))
                            dates.append(timezone.make_aware(dt.replace(tzinfo=None)))
                        except Exception:
                            pass
                posted_at = max(dates) if dates else None

                # Build rich content
                content_parts = [f'CHICAGO ORDINANCE VIOLATION: {respondent or address}']
                if respondent:
                    content_parts.append(f'Respondent: {respondent}')
                content_parts.append(f'Address: {full_addr}')
                content_parts.append(f'Cases: {len(cases)}')
                if total_fine > 0:
                    content_parts.append(f'Total Fines Imposed: ${total_fine:,.0f}')
                if total_due > 0:
                    content_parts.append(f'Amount Due: ${total_due:,.0f}')
                if posted_at:
                    days_ago = (timezone.now() - posted_at).days
                    content_parts.append(f'Most recent: {days_ago} days ago')

                for i, c in enumerate(cases[:6]):
                    parts = []
                    if c['description']:
                        parts.append(c['description'][:150])
                    if c['department']:
                        parts.append(f'Dept: {c["department"]}')
                    if c['disposition']:
                        parts.append(f'{c["disposition"]}')
                    if c['imposed_fine']:
                        try:
                            fine_val = float(c['imposed_fine'])
                            parts.append(f'Fine: ${fine_val:,.0f}')
                        except (ValueError, TypeError):
                            pass
                    content_parts.append(f'  [{i+1}] ' + ' | '.join(parts))

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {respondent or address} — {len(cases)} cases — ${total_due:,.0f} due — {urgency.upper()}")
                    for c in cases[:2]:
                        self.stdout.write(f"         - {c['description'][:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?respondents={respondent}',
                        content=content,
                        author=respondent,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'chicago_ordinance_violations',
                            'respondent': respondent,
                            'address': full_addr,
                            'case_count': len(cases),
                            'total_fine': total_fine,
                            'amount_due': total_due,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='IL',
                        region='Chicago',
                        source_group='public_records',
                        source_type='ordinance_violations',
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Chicago ordinance violation error for {respondent}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Chicago ordinance violations error: {e}")
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
