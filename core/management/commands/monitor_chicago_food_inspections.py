"""
Chicago Food Inspections Monitor
API: https://data.cityofchicago.org/resource/4ijn-s7e5.json  (Socrata SODA)
Dataset: 308K rows, updated daily

Rich fields:
  - dba_name, aka_name, license_, facility_type
  - risk (Risk 1 High / Risk 2 Medium / Risk 3 Low)
  - address, city, state, zip, latitude, longitude
  - inspection_date, inspection_type, results
  - violations  (pipe-delimited text with violation number, description, and comments)
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

SODA_URL = 'https://data.cityofchicago.org/resource/4ijn-s7e5.json'

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

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'general contractor']


def _detect_services(text):
    if not text:
        return DEFAULT_SERVICES
    text_lower = text.lower()
    services = set()
    for key, svc_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


def _parse_violations(violation_text):
    """
    Chicago food violations are pipe-delimited:
    '1. FOOD IN SOUND CONDITION ... - Comments: Observed ...|
     2. FACILITIES TO MAINTAIN ... - Comments: ...'
    """
    if not violation_text:
        return []
    violations = []
    # Split on pipe, then parse each block
    parts = violation_text.split('|')
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Extract violation number, description, and comments
        comment_split = part.split('- Comments:', 1)
        description = comment_split[0].strip()
        comments = comment_split[1].strip() if len(comment_split) > 1 else ''
        # Try to extract the violation number
        num_match = re.match(r'^(\d+)\.\s*', description)
        violation_num = num_match.group(1) if num_match else ''
        if num_match:
            description = description[num_match.end():]
        violations.append({
            'number': violation_num,
            'description': description[:200],
            'comments': comments[:200],
        })
    return violations


class Command(BaseCommand):
    help = 'Monitor Chicago Food Inspections (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='chicago_food_inspections',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on inspections with actionable results
        params = {
            '$where': (
                f"inspection_date >= '{since}' AND "
                f"results IN('Fail', 'Pass w/ Conditions', 'Not Ready')"
            ),
            '$select': (
                'inspection_id,dba_name,aka_name,license_,facility_type,'
                'risk,address,city,state,zip,latitude,longitude,'
                'inspection_date,inspection_type,results,violations'
            ),
            '$limit': limit,
            '$order': 'inspection_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  CHICAGO FOOD INSPECTIONS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} failed/conditional inspections from Chicago")

            for rec in records:
                inspection_id = rec.get('inspection_id', '')
                dba_name = (rec.get('dba_name', '') or '').strip()
                aka_name = (rec.get('aka_name', '') or '').strip()
                address = (rec.get('address', '') or '').strip()
                city = rec.get('city', 'Chicago')
                state = rec.get('state', 'IL')
                zipcode = rec.get('zip', '')
                facility_type = (rec.get('facility_type', '') or '').strip()
                risk = (rec.get('risk', '') or '').strip()
                results = (rec.get('results', '') or '').strip()
                inspection_type = (rec.get('inspection_type', '') or '').strip()
                inspection_date = rec.get('inspection_date', '')
                violation_text = rec.get('violations', '')

                if not address:
                    continue

                full_addr = f"{address}, {city}, {state} {zipcode}".strip()
                display_name = dba_name or aka_name or address

                # Parse structured violations
                violations = _parse_violations(violation_text)
                all_text = ' '.join(
                    f"{v['description']} {v['comments']}" for v in violations
                )
                services = _detect_services(all_text)

                # Urgency based on result and risk
                is_fail = results.lower() == 'fail'
                is_high_risk = 'risk 1' in risk.lower()

                if is_fail and is_high_risk:
                    urgency = 'hot'
                    urgency_note = f'FAILED inspection — {risk} facility'
                elif is_fail:
                    urgency = 'hot'
                    urgency_note = f'FAILED health inspection'
                elif len(violations) >= 5:
                    urgency = 'warm'
                    urgency_note = f'{len(violations)} violations found on conditional pass'
                else:
                    urgency = 'warm'
                    urgency_note = f'Passed with conditions — needs remediation'

                # Parse inspection date
                posted_at = None
                if inspection_date:
                    try:
                        dt = datetime.fromisoformat(inspection_date.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'CHICAGO FOOD INSPECTION: {display_name}']
                content_parts.append(f'Business: {display_name}')
                if aka_name and aka_name != dba_name:
                    content_parts.append(f'AKA: {aka_name}')
                content_parts.append(f'Address: {full_addr}')
                if facility_type:
                    content_parts.append(f'Type: {facility_type}')
                if risk:
                    content_parts.append(f'Risk: {risk}')
                content_parts.append(f'Result: {results}')
                if inspection_type:
                    content_parts.append(f'Inspection: {inspection_type}')
                if days_ago:
                    content_parts.append(f'Date: {days_ago}')

                content_parts.append(f'Violations: {len(violations)}')

                for i, v in enumerate(violations[:8]):
                    parts = []
                    if v['description']:
                        parts.append(v['description'][:150])
                    if v['comments']:
                        parts.append(f'Inspector: {v["comments"][:150]}')
                    prefix = f'  [{v["number"] or i+1}] '
                    content_parts.append(prefix + ' | '.join(parts))

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {address} — {results} — {urgency.upper()}")
                    for v in violations[:2]:
                        self.stdout.write(f"         - {v['description'][:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?inspection_id={inspection_id}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'chicago_food_inspections',
                            'inspection_id': inspection_id,
                            'business_name': display_name,
                            'address': full_addr,
                            'facility_type': facility_type,
                            'risk': risk,
                            'result': results,
                            'violation_count': len(violations),
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='IL',
                        region='Chicago',
                        source_group='health',
                        source_type='food_inspections',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Chicago food inspection error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Chicago food inspections error: {e}")
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
