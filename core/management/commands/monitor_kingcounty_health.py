"""
King County, WA (Seattle) food establishment health inspection monitor.

API: https://data.kingcounty.gov/resource/f29f-zza5.json (Socrata SODA)
Dataset: Food Establishment Inspection Data — 2006 to present

Rich fields with PHONE NUMBER:
  - name, phone, address, city, zip_code, latitude, longitude
  - inspection_date, inspection_type, inspection_score, inspection_result, grade
  - violation_type (RED=critical, BLUE=non-critical), violation_description, violation_points

Covers Seattle + all King County (2M+ population).

Usage:
    python manage.py monitor_kingcounty_health --days 14
    python manage.py monitor_kingcounty_health --days 7 --dry-run
"""
import logging
import requests
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models.monitoring import MonitorRun
from core.utils.monitors.lead_processor import process_lead

logger = logging.getLogger('monitors')

SODA_URL = 'https://data.kingcounty.gov/resource/f29f-zza5.json'

VIOLATION_SERVICE_MAP = {
    'plumbing': ['plumber'],
    'pipe': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber'],
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
    'temperature': ['HVAC', 'commercial refrigeration'],
    'hood': ['hood cleaning', 'HVAC'],
    'grease': ['hood cleaning', 'commercial cleaning'],
    'fire': ['fire safety'],
    'extinguisher': ['fire safety'],
    'floor': ['general contractor', 'commercial cleaning'],
    'wall': ['general contractor'],
    'ceiling': ['general contractor'],
    'mold': ['mold remediation'],
    'clean': ['commercial cleaning', 'deep cleaning'],
    'sanitiz': ['commercial cleaning'],
    'trash': ['waste management'],
    'paint': ['painter'],
    'electrical': ['electrician'],
    'light': ['electrician'],
}

DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'HVAC', 'plumber']


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
    help = (
        'Monitor King County, WA (Seattle) food establishment health inspections. '
        'Includes phone numbers, violation details, scores, and grades.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)
        parser.add_argument('--limit', type=int, default=2000)
        parser.add_argument('--city', type=str, default=None,
                            help='Filter by city (e.g. Seattle, Bellevue, Kirkland)')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        city_filter = options.get('city')
        dry_run = options['dry_run']

        run = MonitorRun.objects.create(
            monitor_name='kingcounty_health',
            details={'days': days, 'limit': limit, 'city': city_filter},
        )

        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')

        # Query for inspections with violations
        where_parts = [
            f"inspection_date >= '{since}'",
            "violation_points > 0",
        ]
        if city_filter:
            where_parts.append(f"upper(city) = '{city_filter.upper()}'")

        params = {
            '$where': ' AND '.join(where_parts),
            '$select': (
                'name,business_id,phone,address,city,zip_code,'
                'inspection_date,inspection_type,inspection_score,'
                'inspection_result,inspection_closed_business,grade,'
                'violation_type,violation_description,violation_points,'
                'latitude,longitude'
            ),
            '$limit': limit,
            '$order': 'inspection_date DESC',
        }

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  KING COUNTY (SEATTLE) HEALTH INSPECTIONS")
        self.stdout.write(f"  Since: {since} | Limit: {limit}")
        if city_filter:
            self.stdout.write(f"  City: {city_filter}")
        self.stdout.write(f"{'='*60}\n")

        stats = {'created': 0, 'duplicates': 0, 'errors': 0, 'items_scraped': 0}

        try:
            resp = requests.get(SODA_URL, params=params, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            stats['items_scraped'] = len(records)
            self.stdout.write(f"Fetched {len(records)} violation records")

            # Group by business_id + inspection_date to consolidate violations
            inspections = {}
            for rec in records:
                biz_id = (rec.get('business_id') or '').strip()
                insp_date = (rec.get('inspection_date') or '').strip()
                if not biz_id or not insp_date:
                    continue

                key = f"{biz_id}|{insp_date}"

                if key not in inspections:
                    inspections[key] = {
                        'name': (rec.get('name') or '').strip(),
                        'business_id': biz_id,
                        'phone': (rec.get('phone') or '').strip(),
                        'address': (rec.get('address') or '').strip(),
                        'city': (rec.get('city') or '').strip(),
                        'zip_code': (rec.get('zip_code') or '').strip(),
                        'inspection_date': insp_date,
                        'inspection_type': (rec.get('inspection_type') or '').strip(),
                        'inspection_result': (rec.get('inspection_result') or '').strip(),
                        'closed': rec.get('inspection_closed_business', False),
                        'latitude': rec.get('latitude', ''),
                        'longitude': rec.get('longitude', ''),
                        'score': 0,
                        'grade': (rec.get('grade') or '').strip(),
                        'violations': [],
                        'red_count': 0,
                        'blue_count': 0,
                        'total_points': 0,
                    }
                    try:
                        inspections[key]['score'] = int(float(rec.get('inspection_score', 0) or 0))
                    except (ValueError, TypeError):
                        pass

                # Add violation
                v_type = (rec.get('violation_type') or '').strip().upper()
                v_desc = (rec.get('violation_description') or '').strip()
                try:
                    v_points = int(float(rec.get('violation_points', 0) or 0))
                except (ValueError, TypeError):
                    v_points = 0

                if v_desc:
                    inspections[key]['violations'].append({
                        'type': v_type,
                        'description': v_desc,
                        'points': v_points,
                    })
                    if v_type == 'RED':
                        inspections[key]['red_count'] += 1
                    elif v_type == 'BLUE':
                        inspections[key]['blue_count'] += 1
                    inspections[key]['total_points'] += v_points

            self.stdout.write(f"Grouped into {len(inspections)} inspections")

            printed = 0
            for key, insp in inspections.items():
                biz_name = insp['name']
                if not biz_name:
                    continue

                phone = insp['phone']
                address = insp['address']
                city = insp['city']
                zipcode = insp['zip_code']
                result = insp['inspection_result']
                score = insp['score']
                red_count = insp['red_count']
                blue_count = insp['blue_count']
                total_points = insp['total_points']
                violations = insp['violations']
                closed = insp['closed']

                full_addr = f"{address}, {city}, WA {zipcode}".strip(', ')

                # Urgency
                if closed or result.lower() == 'unsatisfactory':
                    urgency = 'hot'
                    urgency_note = f'UNSATISFACTORY — {red_count} critical (RED) violations'
                elif red_count > 0:
                    urgency = 'hot'
                    urgency_note = f'{red_count} critical (RED) violation(s), {total_points} total points'
                elif total_points >= 25:
                    urgency = 'warm'
                    urgency_note = f'{total_points} violation points — needs remediation'
                else:
                    urgency = 'new'
                    urgency_note = f'{blue_count} non-critical violations, {total_points} points'

                all_violation_text = ' '.join(v['description'] for v in violations)
                services = _detect_services(all_violation_text)

                # Parse date
                posted_at = None
                try:
                    dt = datetime.fromisoformat(insp['inspection_date'].replace('Z', '+00:00'))
                    posted_at = dt if dt.tzinfo else timezone.make_aware(dt)
                except Exception:
                    pass

                days_ago = ''
                if posted_at:
                    days_ago = f'{(timezone.now() - posted_at).days} days ago'

                # Build content
                content_parts = [f'HEALTH VIOLATION: {biz_name}']
                content_parts.append(f'Address: {full_addr}')
                if phone:
                    content_parts.append(f'Phone: {phone}')
                content_parts.append(f'Result: {result}')
                if score:
                    content_parts.append(f'Score: {score} points deducted')
                if days_ago:
                    content_parts.append(f'Inspected: {days_ago}')
                content_parts.append(f'Violations: {len(violations)} total ({red_count} RED critical, {blue_count} BLUE)')

                for v in violations[:5]:
                    prefix = '[CRITICAL] ' if v['type'] == 'RED' else ''
                    content_parts.append(f'  - {prefix}{v["description"][:200]}')

                content_parts.append(f'Urgency: {urgency_note}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content = '\n'.join(content_parts)

                if dry_run:
                    if printed < 10:
                        self.stdout.write(f"\n  [{city}] {biz_name}")
                        self.stdout.write(f"    {full_addr}")
                        if phone:
                            self.stdout.write(f"    Phone: {phone}")
                        self.stdout.write(f"    Result: {result} | Violations: {len(violations)} ({red_count} RED)")
                        self.stdout.write(f"    Urgency: {urgency.upper()}")
                        printed += 1
                    stats['created'] += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='public_records',
                        source_url=f'{SODA_URL}?business_id={insp["business_id"]}',
                        content=content,
                        author='',
                        posted_at=posted_at,
                        raw_data={
                            'data_source': 'kingcounty_health',
                            'business_id': insp['business_id'],
                            'business_name': biz_name,
                            'phone': phone,
                            'address': full_addr,
                            'score': score,
                            'result': result,
                            'red_violations': red_count,
                            'blue_violations': blue_count,
                            'total_points': total_points,
                            'closed': closed,
                            'urgency': urgency,
                            'services_mapped': services,
                        },
                        state='WA',
                        region='King County',
                        source_group='health',
                        source_type='health_inspections',
                        contact_business=biz_name,
                        contact_phone=phone,
                        contact_address=full_addr,
                    )
                    if created:
                        stats['created'] += 1
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f"King County health error for {biz_name}: {e}")
                    stats['errors'] += 1

        except Exception as e:
            logger.error(f"King County health inspections error: {e}")
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
