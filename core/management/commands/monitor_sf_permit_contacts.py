"""
San Francisco Permit Contacts Monitor
Pulls from THREE SF contact datasets and creates leads with phone numbers and firm names:
1. Plumbing Permits Contacts: https://data.sfgov.org/resource/k6kv-9kix.json
2. Electrical Permits Contacts: https://data.sfgov.org/resource/fdm7-jqqf.json
3. Building Permits Contacts: https://data.sfgov.org/resource/3pee-9qhc.json

Each dataset contains contractor/firm contact info linked to active permits.
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

# Socrata SODA URLs
PLUMBING_URL = 'https://data.sfgov.org/resource/k6kv-9kix.json'
ELECTRICAL_URL = 'https://data.sfgov.org/resource/fdm7-jqqf.json'
BUILDING_URL = 'https://data.sfgov.org/resource/3pee-9qhc.json'


class Command(BaseCommand):
    help = 'Monitor SF Permit Contacts (Plumbing, Electrical, Building)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='sf_permit_contacts',
            details={'days': days, 'limit': limit},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  SF PERMIT CONTACTS MONITOR")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        # Process Plumbing Permits Contacts
        self.stdout.write("\n--- Plumbing Permits Contacts ---")
        stats = self._process_plumbing_contacts(
            since, limit, dry_run, stats
        )

        # Process Electrical Permits Contacts
        self.stdout.write("\n--- Electrical Permits Contacts ---")
        stats = self._process_electrical_contacts(
            since, limit, dry_run, stats
        )

        # Process Building Permits Contacts
        self.stdout.write("\n--- Building Permits Contacts ---")
        stats = self._process_building_contacts(
            since, limit, dry_run, stats
        )

        run.leads_created = stats['created']
        run.duplicates = stats['duplicates']
        run.errors = stats['errors']
        run.items_scraped = stats['items_scraped']
        run.finish(status='success' if not stats['errors'] else 'partial')

        self.stdout.write(
            f"\nTotal Results: {stats['created']} created, "
            f"{stats['duplicates']} dupes, {stats['errors']} errors"
        )

    def _process_plumbing_contacts(self, since, limit, dry_run, stats):
        """Process plumbing permits contacts dataset"""
        params = {
            '$select': (
                'permit_number,firm_name,phone,address,city,state,zipcode,'
                'license_number,data_as_of'
            ),
            '$where': f"data_as_of >= '{since}T00:00:00'",
            '$order': 'data_as_of DESC',
            '$limit': limit,
        }

        try:
            resp = requests.get(PLUMBING_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            self.stdout.write(f"Fetched {len(records)} plumbing permit contacts")
            stats['items_scraped'] += len(records)

            for rec in records:
                permit_number = rec.get('permit_number', '').strip()
                firm_name = (rec.get('firm_name', '') or '').strip()
                phone = (rec.get('phone', '') or '').strip()
                address = (rec.get('address', '') or '').strip()
                city = rec.get('city', 'San Francisco')
                state = rec.get('state', 'CA')
                zipcode = (rec.get('zipcode', '') or '').strip()
                data_as_of = rec.get('data_as_of', '')

                if not firm_name or not permit_number:
                    continue

                firm_addr = f"{address}, {city}, {state} {zipcode}".strip() if address else firm_name

                posted_at = None
                if data_as_of:
                    try:
                        dt = datetime.fromisoformat(data_as_of.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                content_parts = [f'SF PLUMBING PERMIT CONTACT: {firm_name}']
                content_parts.append(f'Firm: {firm_name}')
                if address:
                    content_parts.append(f'Address: {firm_addr}')
                if phone:
                    content_parts.append(f'Phone: {phone}')
                content_parts.append(f'Permit #: {permit_number}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {firm_name} — Plumbing — {permit_number}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{PLUMBING_URL}?permit_number={permit_number}',
                        content=content,
                        author=firm_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_plumbing_permits',
                            'permit_type': 'plumbing',
                            'permit_number': permit_number,
                            'firm_name': firm_name,
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='public_records',
                        source_type='permit_contacts',
                        contact_name=firm_name,
                        contact_phone=phone,
                        contact_business=firm_name,
                        contact_address=firm_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF plumbing contact error for {firm_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"SF plumbing contacts error: {e}")
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(f"Plumbing error: {e}"))

        return stats

    def _process_electrical_contacts(self, since, limit, dry_run, stats):
        """Process electrical permits contacts dataset"""
        params = {
            '$select': (
                'permit_number,company_name,phone,phone2,street_number,street,'
                'street_suffix,state,zipcode,license_number,contact_type,'
                'data_as_of'
            ),
            '$where': f"data_as_of >= '{since}T00:00:00'",
            '$order': 'data_as_of DESC',
            '$limit': limit,
        }

        try:
            resp = requests.get(ELECTRICAL_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            self.stdout.write(f"Fetched {len(records)} electrical permit contacts")
            stats['items_scraped'] += len(records)

            for rec in records:
                permit_number = rec.get('permit_number', '').strip()
                company_name = (rec.get('company_name', '') or '').strip()
                phone = (rec.get('phone', '') or '').strip()
                phone2 = (rec.get('phone2', '') or '').strip()
                street_number = (rec.get('street_number', '') or '').strip()
                street = (rec.get('street', '') or '').strip()
                street_suffix = (rec.get('street_suffix', '') or '').strip()
                state = rec.get('state', 'CA')
                zipcode = (rec.get('zipcode', '') or '').strip()
                data_as_of = rec.get('data_as_of', '')

                if not company_name or not permit_number:
                    continue

                # Use first available phone
                primary_phone = phone or phone2

                # Build address
                addr_parts = [street_number, street]
                if street_suffix:
                    addr_parts.append(street_suffix)
                street_addr = ' '.join(filter(None, addr_parts))
                firm_addr = f"{street_addr}, San Francisco, {state} {zipcode}".strip()

                posted_at = None
                if data_as_of:
                    try:
                        dt = datetime.fromisoformat(data_as_of.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                content_parts = [f'SF ELECTRICAL PERMIT CONTACT: {company_name}']
                content_parts.append(f'Company: {company_name}')
                if street_addr:
                    content_parts.append(f'Address: {firm_addr}')
                if primary_phone:
                    content_parts.append(f'Phone: {primary_phone}')
                content_parts.append(f'Permit #: {permit_number}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {company_name} — Electrical — {permit_number}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{ELECTRICAL_URL}?permit_number={permit_number}',
                        content=content,
                        author=company_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_electrical_permits',
                            'permit_type': 'electrical',
                            'permit_number': permit_number,
                            'company_name': company_name,
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='public_records',
                        source_type='permit_contacts',
                        contact_name=company_name,
                        contact_phone=primary_phone,
                        contact_business=company_name,
                        contact_address=firm_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF electrical contact error for {company_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"SF electrical contacts error: {e}")
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(f"Electrical error: {e}"))

        return stats

    def _process_building_contacts(self, since, limit, dry_run, stats):
        """Process building permits contacts dataset"""
        params = {
            '$select': (
                'permit_number,first_name,last_name,role,firm_name,'
                'firm_address,firm_city,firm_state,firm_zipcode,from_date,'
                'license1,data_as_of'
            ),
            '$where': f"data_as_of >= '{since}T00:00:00'",
            '$order': 'data_as_of DESC',
            '$limit': limit,
        }

        try:
            resp = requests.get(BUILDING_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            self.stdout.write(f"Fetched {len(records)} building permit contacts")
            stats['items_scraped'] += len(records)

            for rec in records:
                permit_number = rec.get('permit_number', '').strip()
                first_name = (rec.get('first_name', '') or '').strip()
                last_name = (rec.get('last_name', '') or '').strip()
                role = (rec.get('role', '') or '').strip()
                firm_name = (rec.get('firm_name', '') or '').strip()
                firm_address = (rec.get('firm_address', '') or '').strip()
                firm_city = rec.get('firm_city', 'San Francisco')
                firm_state = rec.get('firm_state', 'CA')
                firm_zipcode = (rec.get('firm_zipcode', '') or '').strip()
                data_as_of = rec.get('data_as_of', '')

                if not permit_number:
                    continue

                # Determine contact name and business
                if firm_name:
                    contact_name = firm_name
                    contact_business = firm_name
                else:
                    contact_name = f"{first_name} {last_name}".strip()
                    contact_business = contact_name

                if not contact_name:
                    continue

                # Build firm address
                firm_addr = f"{firm_address}, {firm_city}, {firm_state} {firm_zipcode}".strip()

                posted_at = None
                if data_as_of:
                    try:
                        dt = datetime.fromisoformat(data_as_of.replace('Z', '+00:00'))
                        posted_at = timezone.make_aware(dt.replace(tzinfo=None))
                    except Exception:
                        pass

                content_parts = [f'SF BUILDING PERMIT CONTACT: {contact_name}']
                if firm_name:
                    content_parts.append(f'Firm: {firm_name}')
                if first_name or last_name:
                    content_parts.append(f'Contact: {first_name} {last_name}'.strip())
                if role:
                    content_parts.append(f'Role: {role}')
                if firm_address:
                    content_parts.append(f'Address: {firm_addr}')
                content_parts.append(f'Permit #: {permit_number}')
                content = '\n'.join(content_parts)

                if dry_run:
                    self.stdout.write(f"  [DRY] {contact_name} — Building — {permit_number}")
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{BUILDING_URL}?permit_number={permit_number}',
                        content=content,
                        author=contact_name,
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'sf_building_permits',
                            'permit_type': 'building',
                            'permit_number': permit_number,
                            'firm_name': firm_name,
                            'contact_role': role,
                        },
                        state='CA',
                        region='San Francisco',
                        source_group='public_records',
                        source_type='permit_contacts',
                        contact_name=contact_name,
                        contact_business=contact_business,
                        contact_address=firm_addr if firm_addr != firm_city else contact_business,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"SF building contact error for {contact_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"SF building contacts error: {e}")
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(f"Building error: {e}"))

        return stats
