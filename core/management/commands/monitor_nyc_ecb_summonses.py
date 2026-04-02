"""
NYC OATH/ECB Summonses Monitor (Environmental Control Board)
API: https://data.cityofnewyork.us/resource/jz4z-kudi.json  (Socrata SODA)
Dataset: ~3M rows, updated regularly

Administrative summonses for building, fire, health, sanitation violations.

Rich fields:
  - isp_cur_code (issuing agency: DOB, FDNY, DOT, DSNY, DEP, etc.)
  - violation_number, violation_date
  - respondent_name, respondent_house_number, respondent_street, respondent_city, respondent_zip
  - violation_type (e.g. "Unknown/NA", "ELIG-PENALTY")
  - violation_details (descriptive text about the violation)
  - penalty_applied, penalty_balance_due, amount_paid, amount_baldue, amount_invoiced
  - hearing_date_time, hearing_result, hearing_status
  - scheduled_hearing_date
  - bin, block, lot, community_board, census_tract
  - nta (neighborhood tabulation area)
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.cityofnewyork.us/resource/jz4z-kudi.json'

AGENCY_MAP = {
    'DOB': 'Dept. of Buildings',
    'FDNY': 'Fire Department',
    'DOT': 'Dept. of Transportation',
    'DSNY': 'Dept. of Sanitation',
    'DEP': 'Dept. of Environmental Protection',
    'DOH': 'Dept. of Health',
    'DOHMH': 'Dept. of Health & Mental Hygiene',
    'HPD': 'Housing Preservation & Development',
    'DCA': 'Consumer & Worker Protection',
    'DCWP': 'Consumer & Worker Protection',
    'DPR': 'Parks & Recreation',
    'ECB': 'Environmental Control Board',
}

VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'water': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewer': ['plumber', 'sewer service'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'fire escape': ['general contractor', 'fire safety'],
    'fire': ['fire safety'],
    'smoke': ['fire safety'],
    'sprinkler': ['fire safety'],
    'elevator': ['elevator repair'],
    'scaffold': ['general contractor'],
    'roof': ['roofer', 'general contractor'],
    'structural': ['general contractor', 'structural engineer'],
    'foundation': ['general contractor', 'structural engineer'],
    'facade': ['general contractor', 'masonry contractor'],
    'masonry': ['masonry contractor'],
    'sidewalk': ['concrete contractor'],
    'concrete': ['concrete contractor'],
    'construction': ['general contractor'],
    'demolition': ['demolition contractor'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'boiler': ['HVAC'],
    'pest': ['pest control'],
    'rodent': ['pest control'],
    'rat': ['pest control'],
    'vermin': ['pest control'],
    'mold': ['mold remediation'],
    'lead': ['lead abatement'],
    'asbestos': ['asbestos abatement'],
    'paint': ['painter'],
    'trash': ['waste management', 'commercial cleaning'],
    'garbage': ['waste management'],
    'sanitation': ['waste management', 'commercial cleaning'],
    'sign': ['sign contractor'],
    'permit': ['general contractor'],
    'vacant': ['general contractor', 'security'],
    'noise': ['general contractor'],
    'fence': ['fencing contractor'],
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
    help = 'Monitor NYC OATH/ECB Administrative Summonses (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)
        parser.add_argument('--limit', type=int, default=3000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='nyc_ecb_summonses',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        params = {
            '$where': (
                f"violation_date >= '{since}' AND "
                f"penalty_imposed > 0"
            ),
            '$select': (
                'ticket_number,violation_date,issuing_agency,'
                'violation_location_borough,violation_location_city,'
                'violation_location_zip_code,'
                'respondent_address_borough,respondent_address_house,'
                'respondent_address_street_name,'
                'hearing_result,hearing_date,hearing_time,decision_date,'
                'penalty_imposed,'
                'charge_1_code,charge_1_code_section,charge_1_code_description,'
                'charge_2_code,charge_2_code_section,charge_2_code_description,'
                'charge_3_code,charge_3_code_section,charge_3_code_description'
            ),
            '$limit': limit,
            '$order': 'violation_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  NYC OATH/ECB SUMMONSES MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} ECB summonses with penalties")

            # Group by address (no respondent name in this dataset)
            respondents = {}
            for rec in records:
                house = (rec.get('respondent_address_house', '') or '').strip()
                street = (rec.get('respondent_address_street_name', '') or '').strip()
                address = f"{house} {street}".strip()
                borough = (rec.get('violation_location_borough', '') or
                           rec.get('respondent_address_borough', '') or '').strip()
                city = (rec.get('violation_location_city', '') or 'New York').strip()
                zipcode = (rec.get('violation_location_zip_code', '') or '').strip()

                if not address:
                    continue

                key = f"{address}|{borough}".lower()
                if key not in respondents:
                    respondents[key] = {
                        'name': '',  # no respondent name in dataset
                        'address': address,
                        'borough': borough,
                        'city': city,
                        'zip': zipcode,
                        'summonses': [],
                    }

                # Combine charge descriptions for rich detail
                charges = []
                for i in range(1, 4):
                    desc = (rec.get(f'charge_{i}_code_description', '') or '').strip()
                    section = (rec.get(f'charge_{i}_code_section', '') or '').strip()
                    if desc:
                        charges.append(f"{desc}" + (f" ({section})" if section else ''))

                summons = {
                    'ticket': rec.get('ticket_number', ''),
                    'date': rec.get('violation_date', ''),
                    'agency': (rec.get('issuing_agency', '') or '').strip().upper(),
                    'charges': charges,
                    'details': ' | '.join(charges),
                    'penalty': rec.get('penalty_imposed', ''),
                    'hearing_result': (rec.get('hearing_result', '') or '').strip(),
                    'decision_date': (rec.get('decision_date', '') or '').strip(),
                }
                respondents[key]['summonses'].append(summons)

            self.stdout.write(f"Grouped into {len(respondents)} respondents")

            for resp_key, resp_data in respondents.items():
                address = resp_data['address']
                borough = resp_data['borough']
                city = resp_data['city']
                zipcode = resp_data['zip']
                summonses = resp_data['summonses']
                if not summonses:
                    continue

                full_addr = f"{address}, {borough or city}, NY {zipcode}".strip(', ')

                # Build all text for service detection
                all_text = ' '.join(s['details'] for s in summonses)
                services = _detect_services(all_text)

                # Calculate total penalties
                total_penalty = 0
                for s in summonses:
                    try:
                        total_penalty += float(s['penalty'] or 0)
                    except (ValueError, TypeError):
                        pass

                # Collect agencies
                agencies = set(s['agency'] for s in summonses if s['agency'])
                agency_labels = [AGENCY_MAP.get(a, a) for a in agencies]

                # Urgency
                if total_penalty >= 10000 or len(summonses) >= 5:
                    urgency = 'hot'
                    urgency_note = f'${total_penalty:,.0f} in penalties — {len(summonses)} summonses'
                elif total_penalty >= 2500 or len(summonses) >= 3:
                    urgency = 'warm'
                    urgency_note = f'${total_penalty:,.0f} in penalties — {len(summonses)} summonses'
                else:
                    urgency = 'new'
                    urgency_note = 'ECB summons issued'

                # Most recent violation date
                dates = []
                for s in summonses:
                    if s['date']:
                        try:
                            dt = datetime.fromisoformat(s['date'].replace('Z', '+00:00'))
                            dates.append(timezone.make_aware(dt.replace(tzinfo=None)))
                        except Exception:
                            pass
                posted_at = max(dates) if dates else None

                # Build rich content
                content_parts = [f'NYC ECB SUMMONS: {address}']
                content_parts.append(f'Address: {full_addr}')
                if borough:
                    content_parts.append(f'Borough: {borough}')
                if agency_labels:
                    content_parts.append(f'Agencies: {", ".join(agency_labels[:4])}')
                content_parts.append(f'Summonses: {len(summonses)}')
                if total_penalty > 0:
                    content_parts.append(f'Total Penalties: ${total_penalty:,.0f}')
                if posted_at:
                    days_ago = (timezone.now() - posted_at).days
                    content_parts.append(f'Most recent: {days_ago} days ago')

                for i, s in enumerate(summonses[:6]):
                    parts = []
                    agency = AGENCY_MAP.get(s['agency'], s['agency'])
                    if agency:
                        parts.append(f'[{agency}]')
                    if s['details']:
                        parts.append(s['details'][:200])
                    if s['penalty']:
                        try:
                            pen = float(s['penalty'])
                            parts.append(f'Penalty: ${pen:,.0f}')
                        except (ValueError, TypeError):
                            pass
                    if s['hearing_result']:
                        parts.append(f'Result: {s["hearing_result"]}')
                    content_parts.append(f'  [{i+1}] ' + ' | '.join(parts))

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {address} ({borough}) — {len(summonses)} summonses — ${total_penalty:,.0f} penalties — {urgency.upper()}")
                    for s in summonses[:2]:
                        self.stdout.write(f"         - [{s['agency']}] {s['details'][:80]}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?ticket_number={summonses[0]["ticket"]}',
                        content=content,
                        author='',
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'nyc_ecb_summonses',
                            'address': full_addr,
                            'borough': borough,
                            'summons_count': len(summonses),
                            'agencies': list(agencies),
                            'total_penalty': total_penalty,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='NY',
                        region=city,
                        source_group='public_records',
                        source_type='ecb_summonses',
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"ECB summons error for {name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"NYC ECB summonses error: {e}")
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
