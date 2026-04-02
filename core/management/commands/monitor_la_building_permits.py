"""
LA Building Permits Monitor
API: https://data.lacity.org/resource/gwh9-jnip.json  (Socrata SODA)
Dataset: LA Building Permits Submitted from 2020 to Present, 500K+ rows

Rich fields:
  - permit_nbr, primary_address, zip_code
  - permit_group, permit_type, permit_sub_type, use_desc
  - submitted_date, issue_date, status_desc, status_date
  - valuation, construction, height, work_desc
  - lat, lon
  - ev, solar (special flags for leads)
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.lacity.org/resource/gwh9-jnip.json'

VIOLATION_SERVICE_MAP = {
    'solar': ['solar installation', 'solar contractor'],
    'ev': ['EV charging', 'electrical contractor'],
    'electric': ['electrical contractor', 'electrician'],
    'plumb': ['plumber', 'plumbing'],
    'hvac': ['HVAC', 'heating contractor'],
    'roofing': ['roofer', 'roofing contractor'],
    'foundation': ['foundation repair', 'general contractor'],
    'structural': ['structural engineer', 'general contractor'],
    'demolition': ['demolition contractor', 'general contractor'],
    'excavat': ['excavation', 'general contractor'],
    'concrete': ['concrete contractor', 'concrete work'],
    'framing': ['framing contractor', 'general contractor'],
    'drywall': ['drywall contractor', 'general contractor'],
    'paint': ['painter', 'painting contractor'],
    'flooring': ['flooring contractor', 'floor installation'],
    'tile': ['tile contractor', 'tile installation'],
    'cabinet': ['cabinet maker', 'kitchen contractor'],
    'window': ['window installation', 'contractor'],
    'door': ['door installation', 'contractor'],
    'deck': ['deck contractor', 'carpenter'],
    'pool': ['pool contractor', 'pool installation'],
    'garage': ['garage contractor', 'general contractor'],
    'addition': ['general contractor', 'carpenter'],
    'remodel': ['remodeling contractor', 'general contractor'],
    'renovation': ['renovation contractor', 'general contractor'],
    'kitchen': ['kitchen contractor', 'general contractor'],
    'bathroom': ['bathroom contractor', 'general contractor'],
}

DEFAULT_SERVICES = ['general contractor', 'construction company']


def _detect_services(text, ev=None, solar=None):
    if not text:
        services = set(DEFAULT_SERVICES)
    else:
        text_lower = text.lower()
        services = set()
        for key, svc_list in VIOLATION_SERVICE_MAP.items():
            if key in text_lower:
                services.update(svc_list)
        if not services:
            services = set(DEFAULT_SERVICES)

    # Add special flags for EV and solar
    if ev:
        services.add('EV charging')
    if solar:
        services.add('solar installation')

    return list(services)


def _parse_valuation(val_str):
    """Safely parse valuation string to float."""
    if not val_str:
        return 0.0
    try:
        # Remove common currency symbols and formatting
        val_str = str(val_str).replace('$', '').replace(',', '').strip()
        return float(val_str)
    except (ValueError, TypeError):
        return 0.0


class Command(BaseCommand):
    help = 'Monitor LA Building Permits (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='la_building_permits',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        # Fetch recently issued permits
        params = {
            '$where': f"issue_date >= '{since}'",
            '$select': (
                'permit_nbr,primary_address,zip_code,'
                'permit_group,permit_type,permit_sub_type,use_desc,'
                'submitted_date,issue_date,status_desc,valuation,'
                'work_desc,lat,lon,ev,solar'
            ),
            '$limit': limit,
            '$order': 'issue_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  LA BUILDING PERMITS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} building permits from LA")

            for rec in records:
                permit_nbr = rec.get('permit_nbr', '')
                primary_address = (rec.get('primary_address', '') or '').strip()
                zip_code = rec.get('zip_code', '')
                permit_group = (rec.get('permit_group', '') or '').strip()
                permit_type = (rec.get('permit_type', '') or '').strip()
                permit_sub_type = (rec.get('permit_sub_type', '') or '').strip()
                use_desc = (rec.get('use_desc', '') or '').strip()
                submitted_date = rec.get('submitted_date', '')
                issue_date = rec.get('issue_date', '')
                status_desc = (rec.get('status_desc', '') or '').strip()
                work_desc = (rec.get('work_desc', '') or '').strip()
                lat = rec.get('lat')
                lon = rec.get('lon')
                ev = rec.get('ev')
                solar = rec.get('solar')

                # Parse valuation carefully
                valuation_str = rec.get('valuation', '')
                valuation = _parse_valuation(valuation_str)

                if not primary_address:
                    continue

                full_addr = f"{primary_address}, Los Angeles, CA {zip_code}".strip()
                display_name = primary_address

                # Detect services from permit description
                permit_desc = f"{permit_group} {permit_type} {use_desc} {work_desc}".strip()
                services = _detect_services(permit_desc, ev=ev, solar=solar)

                # Urgency based on valuation
                if valuation > 500000:
                    urgency = 'hot'
                    urgency_note = f'High valuation permit (${valuation:,.0f})'
                elif valuation > 100000:
                    urgency = 'warm'
                    urgency_note = f'Significant project (${valuation:,.0f})'
                else:
                    urgency = 'new'
                    urgency_note = f'Standard permit issued'

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
                content_parts = [f'LA BUILDING PERMIT: {display_name}']
                content_parts.append(f'Permit #: {permit_nbr}')
                content_parts.append(f'Address: {full_addr}')
                if lat and lon:
                    content_parts.append(f'Location: {lat}, {lon}')
                if permit_group:
                    content_parts.append(f'Permit Group: {permit_group}')
                if permit_type:
                    content_parts.append(f'Type: {permit_type}')
                if permit_sub_type:
                    content_parts.append(f'Sub-Type: {permit_sub_type}')
                if use_desc:
                    content_parts.append(f'Use: {use_desc}')
                if work_desc:
                    content_parts.append(f'Work: {work_desc}')
                content_parts.append(f'Status: {status_desc}')
                if valuation > 0:
                    content_parts.append(f'Valuation: ${valuation:,.0f}')

                # Special flags for EV and solar
                flags = []
                if ev:
                    flags.append('EV Charging')
                if solar:
                    flags.append('Solar Installation')
                if flags:
                    content_parts.append(f'Special: {", ".join(flags)}')

                if days_ago:
                    content_parts.append(f'Issued: {days_ago}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} — {permit_type} — ${valuation:,.0f} — {urgency.upper()}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?permit_nbr={permit_nbr}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'la_building_permits',
                            'permit_nbr': permit_nbr,
                            'address': full_addr,
                            'permit_type': permit_type,
                            'permit_group': permit_group,
                            'use_desc': use_desc,
                            'valuation': valuation,
                            'status': status_desc,
                            'urgency': urgency,
                            'services_mapped': services,
                            'ev_charging': bool(ev),
                            'solar': bool(solar),
                        },
                        state='CA',
                        region='Los Angeles',
                        source_group='public_records',
                        source_type='building_permits',
                        contact_name=display_name,
                        contact_business=display_name,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"LA building permit error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"LA building permits error: {e}")
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
