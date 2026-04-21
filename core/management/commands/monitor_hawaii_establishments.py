"""
Hawaii food establishment monitor for SalesSignal AI.

API: https://opendata.hawaii.gov/api/3/action/datastore_search (CKAN, not Socrata)
Resource: 9b0e2fd2-d353-4517-92d4-3fb5a8817eb8 — Oahu Food Establishments

6,095 food establishments with PHONE NUMBERS and permit holder (owner) names.
Fields: Establishment, Permit Holder, Telephone, address, Business Status,
        Risk Category, Facility Type, Permit Expire Date

Two lead types:
  1. EXPIRED PERMITS — establishments with expired permits need renewal
     or may be operating without valid permit (enforcement risk)
  2. HIGH RISK — Risk Category 1 establishments (most complex food prep)
     need more services: pest control, HVAC, commercial cleaning

Cross-references with myhealthdept.py Honolulu monitor for violations.

Usage:
    python manage.py monitor_hawaii_establishments --mode expired --dry-run
    python manage.py monitor_hawaii_establishments --mode high_risk
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

CKAN_URL = 'https://opendata.hawaii.gov/api/3/action/datastore_search'
RESOURCE_ID = '9b0e2fd2-d353-4517-92d4-3fb5a8817eb8'

FACILITY_SERVICES = {
    'restaurant': ['commercial cleaning', 'pest control', 'HVAC', 'grease trap', 'hood cleaning'],
    'food service': ['commercial cleaning', 'pest control', 'HVAC'],
    'caterer': ['commercial cleaning', 'pest control', 'equipment repair'],
    'bakery': ['commercial cleaning', 'pest control', 'HVAC'],
    'market': ['pest control', 'commercial cleaning', 'refrigeration'],
    'school': ['commercial cleaning', 'pest control'],
    'bar': ['commercial cleaning', 'pest control', 'security'],
    'hotel': ['commercial cleaning', 'pest control', 'HVAC', 'landscaping'],
    'hospital': ['commercial cleaning', 'HVAC', 'medical waste'],
    'convenience': ['pest control', 'commercial cleaning'],
    'shave ice': ['commercial cleaning', 'equipment repair'],
}

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'HVAC']


def _detect_services(facility_type, establishment_name):
    text = f"{facility_type} {establishment_name}".lower()
    services = set()
    for key, svc_list in FACILITY_SERVICES.items():
        if key in text:
            services.update(svc_list)
    return list(services) if services else DEFAULT_SERVICES


class Command(BaseCommand):
    help = (
        'Monitor Hawaii (Oahu) food establishments with phone numbers. '
        '6K+ establishments with owner names and phone numbers.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode', type=str, default='expired',
            choices=['expired', 'high_risk', 'all'],
            help='expired = expired permits, high_risk = Category 1, all = everything',
        )
        parser.add_argument('--limit', type=int, default=500)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        mode = options['mode']
        limit = options['limit']
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='hawaii_establishments',
            details={'mode': mode, 'limit': limit},
        )

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  HAWAII FOOD ESTABLISHMENTS MONITOR")
        self.stdout.write(f"  Mode: {mode} | Limit: {limit}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        # CKAN API uses different params than Socrata
        params = {
            'resource_id': RESOURCE_ID,
            'limit': limit,
        }

        # Add filters based on mode
        if mode == 'expired':
            params['filters'] = '{"Business Status":"Open"}'
        elif mode == 'high_risk':
            params['filters'] = '{"Risk Category":"Category 1","Business Status":"Open"}'

        try:
            resp = requests.get(CKAN_URL, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            if not data.get('success'):
                self.stdout.write(self.style.ERROR(f"API error: {data.get('error', 'Unknown')}"))
                stats['errors'] += 1
                run.errors = 1
                run.finish(status='failed')
                return

            records = data.get('result', {}).get('records', [])
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} establishments")

            now = timezone.now()
            printed = 0

            for rec in records:
                # CKAN returns ints for numeric fields — str() everything
                def s(val, default=''):
                    return str(val).strip() if val is not None else default

                name = s(rec.get('Establishment'))
                permit_holder = s(rec.get('Permit Holder'))
                phone = s(rec.get('Telephone'))
                biz_status = s(rec.get('Business Status'))
                facility_type = s(rec.get('Facility Type'))
                risk_category = s(rec.get('Risk Category'))
                permit_expire = s(rec.get('Permit Expire Date'))

                # Address fields
                street_num = s(rec.get('Establishment Address Street #'))
                street_name = s(rec.get('Establishment Address Street Name'))
                unit = s(rec.get('Establishment Address Unit #'))
                city = s(rec.get('Establishment Address City'))
                state = s(rec.get('Establishment Address State'), 'Hawaii')
                zipcode = s(rec.get('Establishment Address Zip Code'))

                if not name:
                    continue

                # Build address
                addr_parts = []
                if street_num and street_name:
                    addr = f"{street_num} {street_name}"
                    if unit:
                        addr += f" #{unit}"
                    addr_parts.append(addr)
                elif street_name:
                    addr_parts.append(street_name)
                if city:
                    addr_parts.append(city)
                addr_parts.append('HI')
                if zipcode:
                    addr_parts.append(zipcode)
                full_addr = ', '.join(addr_parts)

                # Check permit expiration for expired mode
                is_expired = False
                days_expired = 0
                if permit_expire:
                    try:
                        # CKAN returns ISO-8601 (e.g. "2013-06-04T00:00:00" or
                        # "2013-06-04T00:00:00.000"); older exports used long
                        # or slash formats. Try most-specific ISO first so the
                        # microsecond variant matches before the shorter one.
                        for fmt in [
                            '%Y-%m-%dT%H:%M:%S.%f',
                            '%Y-%m-%dT%H:%M:%S',
                            '%Y-%m-%d',
                            '%B %d, %Y',
                            '%m/%d/%Y',
                        ]:
                            try:
                                exp_dt = datetime.strptime(permit_expire, fmt)
                                exp_dt = timezone.make_aware(exp_dt)
                                if exp_dt < now:
                                    is_expired = True
                                    days_expired = (now - exp_dt).days
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass

                if mode == 'expired' and not is_expired:
                    continue

                services = _detect_services(facility_type, name)

                # Urgency
                if is_expired and days_expired > 180:
                    urgency = 'hot'
                    urgency_note = f'Permit expired {days_expired} days ago — operating without valid permit'
                elif is_expired:
                    urgency = 'warm'
                    urgency_note = f'Permit expired {days_expired} days ago — needs renewal'
                elif risk_category == 'Category 1':
                    urgency = 'warm'
                    urgency_note = 'High-risk Category 1 facility — complex food prep'
                else:
                    urgency = 'new'
                    urgency_note = f'{risk_category} facility'

                content_parts = []
                if is_expired:
                    content_parts.append(f'EXPIRED PERMIT: {name}')
                else:
                    content_parts.append(f'HAWAII FOOD ESTABLISHMENT: {name}')
                if facility_type:
                    content_parts.append(f'Type: {facility_type}')
                content_parts.append(f'Address: {full_addr}')
                if phone:
                    content_parts.append(f'Phone: {phone}')
                if permit_holder and permit_holder != name:
                    content_parts.append(f'Permit Holder: {permit_holder}')
                content_parts.append(f'Risk: {risk_category}')
                content_parts.append(f'Status: {biz_status}')
                if is_expired:
                    content_parts.append(f'Permit Expired: {permit_expire} ({days_expired} days ago)')
                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    if printed < 10:
                        self.stdout.write(f"\n  [{city or 'HI'}] {name}")
                        self.stdout.write(f"    Phone: {phone or '(none)'}")
                        self.stdout.write(f"    Holder: {permit_holder}")
                        self.stdout.write(f"    Type: {facility_type} | Risk: {risk_category}")
                        if is_expired:
                            self.stdout.write(f"    EXPIRED: {permit_expire} ({days_expired}d ago)")
                        printed += 1
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url='https://opendata.hawaii.gov/dataset/oahu-food-establishments',
                        content=content,
                        author='',
                        raw_data={
                            'data_source': 'hawaii_food_establishments',
                            'mode': mode,
                            'establishment': name,
                            'permit_holder': permit_holder,
                            'phone': phone,
                            'facility_type': facility_type,
                            'risk_category': risk_category,
                            'business_status': biz_status,
                            'permit_expire': permit_expire,
                            'is_expired': is_expired,
                            'days_expired': days_expired,
                            'services_mapped': services,
                        },
                        state='HI',
                        region='Honolulu',
                        source_group='public_records',
                        source_type='business_registry',
                        contact_name=permit_holder,
                        contact_business=name,
                        contact_phone=phone,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"Hawaii establishment error for {name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"Hawaii establishments error: {e}")
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
