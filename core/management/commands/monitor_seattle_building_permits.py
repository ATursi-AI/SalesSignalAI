"""
Seattle Building Permits Monitor
API: https://data.seattle.gov/resource/76t5-zqzr.json  (Socrata SODA)
Dataset: Building permits from the City of Seattle

Rich fields:
  - permitnum, permitclass, permitclassmapped, permittypemapped, permittypedesc
  - description, housingunits, estprojectcost
  - applieddate, issueddate, statuscurrent
  - originaladdress1, originalcity, originalstate, originalzip
  - contractorcompanyname, link, latitude, longitude
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.seattle.gov/resource/76t5-zqzr.json'

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
    'roofing': ['roofer'],
    'roof': ['roofer'],
    'foundation': ['foundation repair'],
    'concrete': ['general contractor', 'concrete'],
    'demolition': ['general contractor'],
    'construction': ['general contractor'],
    'renovation': ['general contractor'],
    'remodel': ['general contractor'],
}

DEFAULT_SERVICES = ['general contractor', 'construction']


def _detect_services(text):
    if not text:
        return DEFAULT_SERVICES
    text_lower = text.lower()
    services = set()
    for key, svc_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


def _parse_date(date_str):
    """
    Parse date string which may be ISO format or text.
    Returns timezone-aware datetime or None.
    """
    if not date_str:
        return None
    try:
        # Try ISO format first
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt.replace(tzinfo=None))
    except Exception:
        pass
    try:
        # Try common date formats
        for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d']:
            dt = datetime.strptime(date_str.strip(), fmt)
            return timezone.make_aware(dt)
    except Exception:
        pass
    return None


class Command(BaseCommand):
    help = 'Monitor Seattle Building Permits (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='seattle_building_permits',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Focus on issued permits
        params = {
            '$where': f"issueddate >= '{since}'",
            '$select': (
                'permitnum,permitclass,permitclassmapped,permittypemapped,permittypedesc,'
                'description,housingunits,estprojectcost,'
                'applieddate,issueddate,statuscurrent,'
                'originaladdress1,originalcity,originalstate,originalzip,'
                'contractorcompanyname,link,latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'issueddate DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SEATTLE BUILDING PERMITS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} building permits from Seattle")

            for rec in records:
                permitnum = (rec.get('permitnum', '') or '').strip()
                permitclass = (rec.get('permitclass', '') or '').strip()
                permitclassmapped = (rec.get('permitclassmapped', '') or '').strip()
                permittypemapped = (rec.get('permittypemapped', '') or '').strip()
                permittypedesc = (rec.get('permittypedesc', '') or '').strip()
                description = (rec.get('description', '') or '').strip()
                housingunits = rec.get('housingunits', '')
                estprojectcost_str = rec.get('estprojectcost', '')
                issueddate = rec.get('issueddate', '')
                statuscurrent = (rec.get('statuscurrent', '') or '').strip()
                originaladdress1 = (rec.get('originaladdress1', '') or '').strip()
                originalcity = (rec.get('originalcity', '') or '').strip() or 'Seattle'
                originalstate = (rec.get('originalstate', '') or '').strip() or 'WA'
                originalzip = (rec.get('originalzip', '') or '').strip()
                contractorcompanyname = (rec.get('contractorcompanyname', '') or '').strip()

                if not originaladdress1:
                    continue

                # Parse estimated project cost
                estprojectcost = 0
                if estprojectcost_str:
                    try:
                        estprojectcost = float(estprojectcost_str)
                    except (ValueError, TypeError):
                        pass

                full_addr = f"{originaladdress1}, {originalcity}, {originalstate} {originalzip}".strip()

                # Use contractor name if available, else address
                contact_name = contractorcompanyname or originaladdress1
                display_name = contact_name

                # Detect services from description and permit type
                services = _detect_services(description + ' ' + permittypedesc + ' ' + permitclassmapped)

                # Urgency logic based on project cost
                if estprojectcost > 500000:
                    urgency = 'hot'
                    urgency_note = f'Large project: ${estprojectcost:,.0f}'
                elif estprojectcost > 100000:
                    urgency = 'warm'
                    urgency_note = f'Significant project: ${estprojectcost:,.0f}'
                else:
                    urgency = 'new'
                    urgency_note = f'Building permit issued'

                # Parse issued date
                posted_at = _parse_date(issueddate)

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'SEATTLE BUILDING PERMIT: {display_name}']
                content_parts.append(f'Permit #: {permitnum}')
                content_parts.append(f'Address: {full_addr}')
                if contractorcompanyname:
                    content_parts.append(f'Contractor: {contractorcompanyname}')
                content_parts.append(f'Permit Type: {permittypedesc}')
                content_parts.append(f'Class: {permitclassmapped}')
                if description:
                    content_parts.append(f'Description: {description}')
                if housingunits:
                    content_parts.append(f'Housing Units: {housingunits}')
                if estprojectcost > 0:
                    content_parts.append(f'Est. Project Cost: ${estprojectcost:,.0f}')
                if statuscurrent:
                    content_parts.append(f'Status: {statuscurrent}')
                if days_ago:
                    content_parts.append(f'Issued: {days_ago}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {originaladdress1} — {permittypedesc} — {urgency.upper()}")
                    if estprojectcost > 0:
                        self.stdout.write(f"         Cost: ${estprojectcost:,.0f}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?permitnum={permitnum}',
                        content=content,
                        author=display_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'seattle_building_permits',
                            'permitnum': permitnum,
                            'permit_type': permittypemapped,
                            'permit_class': permitclassmapped,
                            'address': full_addr,
                            'contractor': contractorcompanyname,
                            'description': description,
                            'estimated_cost': estprojectcost,
                            'status': statuscurrent,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='WA',
                        region='Seattle',
                        source_group='public_records',
                        source_type='building_permits',
                        contact_name=contact_name,
                        contact_business=contractorcompanyname or originaladdress1,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Seattle building permit error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Seattle building permits error: {e}")
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
