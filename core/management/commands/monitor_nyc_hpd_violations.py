"""
NYC HPD (Housing Preservation & Development) Violations Monitor
API: https://data.cityofnewyork.us/resource/wvxf-dwi5.json  (Socrata SODA)
Dataset: ~5M rows, updated daily

Rich fields:
  - violationid, inspectiondate, approveddate, currentstatusdate
  - currentstatus (VIOLATION OPEN / VIOLATION CLOSED / VIOLATION DISMISSED)
  - violationstatus (Open / Close)
  - class (A=Non-Hazardous, B=Hazardous, C=Immediately Hazardous)
  - ordernumber, novdescription, novissueddate
  - boroid, boroname, block, lot, streetaddress, apartment, zip
  - story, inspectiondate
  - newpenaltydate, penalityamount (sic - API has typo)
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.cityofnewyork.us/resource/wvxf-dwi5.json'

BOROUGH_MAP = {
    '1': 'Manhattan',
    '2': 'Bronx',
    '3': 'Brooklyn',
    '4': 'Queens',
    '5': 'Staten Island',
    'MANHATTAN': 'Manhattan',
    'BRONX': 'Bronx',
    'BROOKLYN': 'Brooklyn',
    'QUEENS': 'Queens',
    'STATEN ISLAND': 'Staten Island',
}

VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'leak': ['plumber'],
    'water supply': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber', 'sewer service'],
    'toilet': ['plumber'],
    'hot water': ['plumber'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'outlet': ['electrician'],
    'light': ['electrician'],
    'fire escape': ['general contractor', 'fire safety'],
    'fire': ['fire safety'],
    'smoke detector': ['fire safety'],
    'carbon monoxide': ['fire safety'],
    'sprinkler': ['fire safety'],
    'elevator': ['elevator repair'],
    'roof': ['roofer', 'general contractor'],
    'stair': ['general contractor'],
    'handrail': ['general contractor'],
    'floor': ['general contractor'],
    'ceiling': ['general contractor'],
    'wall': ['general contractor'],
    'door': ['general contractor'],
    'window': ['general contractor', 'window repair'],
    'foundation': ['general contractor', 'structural engineer'],
    'structural': ['general contractor', 'structural engineer'],
    'heat': ['HVAC'],
    'hvac': ['HVAC'],
    'boiler': ['HVAC', 'plumber'],
    'radiator': ['HVAC'],
    'mold': ['mold remediation'],
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'bed bug': ['pest control', 'exterminator'],
    'paint': ['painter'],
    'lead': ['lead abatement', 'painter'],
    'asbestos': ['asbestos abatement'],
    'trash': ['waste management'],
    'garbage': ['waste management'],
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
    help = 'Monitor NYC HPD Housing Violations (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=3000)
        parser.add_argument('--borough', type=str, default='')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        borough = options['borough'].strip()
        dry_run = options['dry_run']

        monitor_name = 'nyc_hpd_violations'
        if borough:
            monitor_name += f'_{borough}'

        run = MonitorRun.objects.create(
            monitor_name=monitor_name,
            details={'days': days, 'limit': limit, 'borough': borough},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        where_clause = f"inspectiondate >= '{since}' AND violationstatus = 'Open'"
        if borough:
            boro_upper = borough.upper().replace('_', ' ')
            where_clause += f" AND upper(boro) = '{boro_upper}'"

        params = {
            '$where': where_clause,
            '$select': (
                'violationid,buildingid,registrationid,'
                'inspectiondate,approveddate,currentstatusdate,'
                'currentstatus,violationstatus,class,'
                'ordernumber,novid,novdescription,novissueddate,novtype,'
                'boroid,boro,housenumber,lowhousenumber,highhousenumber,'
                'streetname,streetcode,block,lot,zip,apartment,story,'
                'rentimpairing'
            ),
            '$limit': limit,
            '$order': 'inspectiondate DESC',
        }

        boro_label = borough.replace('_', ' ').title() if borough else 'All Boroughs'
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  NYC HPD VIOLATIONS MONITOR — {boro_label}")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} open HPD violations")

            # Group by building (block + lot + boroid)
            buildings = {}
            for rec in records:
                housenumber = (rec.get('housenumber', '') or '').strip()
                streetname = (rec.get('streetname', '') or '').strip()
                address = f"{housenumber} {streetname}".strip()
                boroid = rec.get('boroid', '')
                block = rec.get('block', '')
                lot = rec.get('lot', '')
                if not address:
                    continue

                key = f"{boroid}-{block}-{lot}"
                if key not in buildings:
                    boro_name = BOROUGH_MAP.get(
                        (rec.get('boro', '') or '').upper(),
                        BOROUGH_MAP.get(str(boroid), 'NYC')
                    )
                    buildings[key] = {
                        'address': address,
                        'borough': boro_name,
                        'zip': rec.get('zip', ''),
                        'block': block,
                        'lot': lot,
                        'violations': [],
                    }

                violation = {
                    'id': rec.get('violationid', ''),
                    'date': rec.get('inspectiondate', ''),
                    'class': (rec.get('class', '') or '').strip(),
                    'order': (rec.get('ordernumber', '') or '').strip(),
                    'description': (rec.get('novdescription', '') or '').strip(),
                    'story': (rec.get('story', '') or '').strip(),
                    'apartment': (rec.get('apartment', '') or '').strip(),
                    'status': (rec.get('currentstatus', '') or '').strip(),
                }
                buildings[key]['violations'].append(violation)

            self.stdout.write(f"Grouped into {len(buildings)} buildings")

            for bldg_key, bldg in buildings.items():
                address = bldg['address']
                boro = bldg['borough']
                zipcode = bldg['zip']
                violations = bldg['violations']
                if not violations:
                    continue

                full_addr = f"{address}, {boro}, NY {zipcode}".strip()

                # Count by class
                class_counts = {'A': 0, 'B': 0, 'C': 0}
                for v in violations:
                    cls = v['class'].upper()
                    if cls in class_counts:
                        class_counts[cls] += 1

                # Build all text for service detection
                all_text = ' '.join(v['description'] for v in violations)
                services = _detect_services(all_text)

                # Urgency based on class C (immediately hazardous)
                if class_counts['C'] >= 2:
                    urgency = 'hot'
                    urgency_note = f'{class_counts["C"]} Class C (Immediately Hazardous) violations'
                elif class_counts['C'] >= 1 or class_counts['B'] >= 3:
                    urgency = 'hot'
                    urgency_note = 'Hazardous violations requiring immediate attention'
                elif class_counts['B'] >= 1 or len(violations) >= 3:
                    urgency = 'warm'
                    urgency_note = f'{len(violations)} open violations — needs remediation'
                else:
                    urgency = 'new'
                    urgency_note = 'HPD violation filed'

                # Most recent inspection date
                dates = []
                for v in violations:
                    if v['date']:
                        try:
                            dt = datetime.fromisoformat(v['date'].replace('Z', '+00:00'))
                            dates.append(timezone.make_aware(dt.replace(tzinfo=None)))
                        except Exception:
                            pass
                posted_at = max(dates) if dates else None

                # Build rich content
                content_parts = [f'NYC HPD VIOLATION: {full_addr}']
                content_parts.append(f'Address: {full_addr}')
                content_parts.append(f'BBL: {bldg["block"]}/{bldg["lot"]}')
                content_parts.append(f'Open Violations: {len(violations)}')
                class_summary = ', '.join(
                    f'Class {c}: {n}' for c, n in class_counts.items() if n > 0
                )
                if class_summary:
                    content_parts.append(f'Severity: {class_summary}')
                if posted_at:
                    days_ago = (timezone.now() - posted_at).days
                    content_parts.append(f'Most recent inspection: {days_ago} days ago')

                # Show violations sorted by class (C first, then B, then A)
                sorted_violations = sorted(
                    violations,
                    key=lambda v: {'C': 0, 'B': 1, 'A': 2}.get(v['class'].upper(), 3)
                )

                for i, v in enumerate(sorted_violations[:8]):
                    parts = []
                    cls = v['class']
                    if cls:
                        label = {'C': 'IMMEDIATELY HAZARDOUS', 'B': 'HAZARDOUS', 'A': 'NON-HAZARDOUS'}.get(cls.upper(), cls)
                        parts.append(f'[Class {cls} — {label}]')
                    if v['description']:
                        parts.append(v['description'][:200])
                    if v['apartment']:
                        parts.append(f'Apt: {v["apartment"]}')
                    if v['story']:
                        parts.append(f'Floor: {v["story"]}')
                    content_parts.append(f'  [{i+1}] ' + ' | '.join(parts))

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {full_addr} — {len(violations)} violations")
                    self.stdout.write(f"         Classes: A={class_counts['A']} B={class_counts['B']} C={class_counts['C']} — {urgency.upper()}")
                    for v in sorted_violations[:2]:
                        self.stdout.write(f"         - [{v['class']}] {v['description'][:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?streetaddress={address}&boroid={bldg["block"]}',
                        content=content,
                        author='',
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'nyc_hpd_violations',
                            'address': full_addr,
                            'borough': boro,
                            'bbl': f'{bldg["block"]}/{bldg["lot"]}',
                            'violation_count': len(violations),
                            'class_a': class_counts['A'],
                            'class_b': class_counts['B'],
                            'class_c': class_counts['C'],
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='NY',
                        region=boro,
                        source_group='public_records',
                        source_type='hpd_violations',
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"HPD violation error for {full_addr}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"NYC HPD violations error: {e}")
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
