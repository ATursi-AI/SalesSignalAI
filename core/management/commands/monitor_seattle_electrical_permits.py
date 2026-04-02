"""
Seattle Electrical Permits Monitor
API: https://data.seattle.gov/resource/c4tj-daue.json  (Socrata SODA)
Dataset: Electrical permits

Rich fields:
  - permitnum, permitclass, permitclassmapped, permittypemapped
  - description, estprojectcost, issueddate, statuscurrent
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

SODA_URL = 'https://data.seattle.gov/resource/c4tj-daue.json'


def _parse_date(date_str):
    """
    Parse date from Seattle API. May be TEXT or timestamp format.
    Try multiple date formats before giving up.
    """
    if not date_str:
        return None

    # Try ISO format with timezone
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt.replace(tzinfo=None))
    except Exception:
        pass

    # Try basic ISO format
    try:
        dt = datetime.strptime(date_str.strip(), '%Y-%m-%d')
        return timezone.make_aware(dt)
    except Exception:
        pass

    # Try with time component
    try:
        dt = datetime.strptime(date_str.strip()[:19], '%Y-%m-%dT%H:%M:%S')
        return timezone.make_aware(dt)
    except Exception:
        pass

    # Try US format
    try:
        dt = datetime.strptime(date_str.strip(), '%m/%d/%Y')
        return timezone.make_aware(dt)
    except Exception:
        pass

    logger.debug(f"Could not parse date: {date_str}")
    return None


class Command(BaseCommand):
    help = 'Monitor Seattle Electrical Permits (Socrata)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='seattle_electrical_permits',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        # Query Seattle electrical permits
        params = {
            '$where': f"issueddate >= '{since}'",
            '$select': (
                'permitnum,permitclass,permitclassmapped,permittypemapped,'
                'description,estprojectcost,issueddate,statuscurrent,'
                'originaladdress1,originalcity,originalstate,originalzip,'
                'contractorcompanyname,link,latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'issueddate DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SEATTLE ELECTRICAL PERMITS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} electrical permits from Seattle")

            for rec in records:
                permitnum = rec.get('permitnum', '')
                permitclass = (rec.get('permitclass', '') or '').strip()
                permitclassmapped = (rec.get('permitclassmapped', '') or '').strip()
                permittypemapped = (rec.get('permittypemapped', '') or '').strip()
                description = (rec.get('description', '') or '').strip()
                estprojectcost_str = rec.get('estprojectcost', '')
                issueddate = rec.get('issueddate', '')
                statuscurrent = (rec.get('statuscurrent', '') or '').strip()
                address = (rec.get('originaladdress1', '') or '').strip()
                city = rec.get('originalcity', 'Seattle')
                state = rec.get('originalstate', 'WA')
                zipcode = rec.get('originalzip', '')
                contractor = (rec.get('contractorcompanyname', '') or '').strip()
                link = rec.get('link', '')
                latitude = rec.get('latitude', '')
                longitude = rec.get('longitude', '')

                if not address:
                    continue

                # Parse project cost
                estprojectcost = 0
                if estprojectcost_str:
                    try:
                        estprojectcost = int(float(str(estprojectcost_str).replace(',', '')))
                    except Exception:
                        pass

                # Parse issued date
                posted_at = _parse_date(issueddate)

                full_addr = f"{address}, {city}, {state} {zipcode}".strip()
                display_name = contractor or address

                # Determine urgency based on project cost
                if estprojectcost > 100000:
                    urgency = 'hot'
                    urgency_note = f'Large project: ${estprojectcost:,}'
                elif estprojectcost > 25000:
                    urgency = 'warm'
                    urgency_note = f'Medium project: ${estprojectcost:,}'
                else:
                    urgency = 'new'
                    urgency_note = 'Electrical permit issued'

                # Calculate days ago
                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build rich content
                content_parts = [f'SEATTLE ELECTRICAL PERMIT: {permitclassmapped or permitclass}']
                content_parts.append(f'Permit #: {permitnum}')
                if contractor:
                    content_parts.append(f'Contractor: {contractor}')
                content_parts.append(f'Address: {full_addr}')
                if permitclassmapped:
                    content_parts.append(f'Class: {permitclassmapped}')
                if permittypemapped:
                    content_parts.append(f'Type: {permittypemapped}')
                if description:
                    content_parts.append(f'Description: {description[:300]}')
                if estprojectcost > 0:
                    content_parts.append(f'Est. Project Cost: ${estprojectcost:,}')
                content_parts.append(f'Status: {statuscurrent}')
                if days_ago:
                    content_parts.append(f'Issued: {days_ago}')
                if link:
                    content_parts.append(f'Link: {link}')
                content_parts.append(f'Urgency: {urgency_note}')

                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {display_name} @ {address} — {urgency.upper()} — ${estprojectcost:,}")
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
                            'data_source': 'seattle_electrical_permits',
                            'permitnum': permitnum,
                            'contractor': contractor,
                            'address': full_addr,
                            'permit_class': permitclassmapped or permitclass,
                            'permit_type': permittypemapped,
                            'description': description,
                            'est_project_cost': estprojectcost,
                            'status': statuscurrent,
                            'urgency': urgency,
                            'latitude': latitude,
                            'longitude': longitude,
                        },
                        state='WA',
                        region='Seattle',
                        source_group='public_records',
                        source_type='electrical_permits',
                        contact_name=contractor or address,
                        contact_business=contractor,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Seattle electrical permit error for {display_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Seattle electrical permits error: {e}")
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
