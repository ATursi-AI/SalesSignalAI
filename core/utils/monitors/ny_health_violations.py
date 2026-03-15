"""
NY health department violation monitor for SalesSignal AI.

Monitors health department violation data from multiple sources:
  a) NYC — Restaurant inspection results via NYC Open Data (SODA API)
     Dataset: 43nn-pn8j (DOHMH New York City Restaurant Inspection Results)
  b) Long Island — Nassau/Suffolk county health department sites (HTML scrape)

Health violations are HIGH-URGENCY leads because restaurants risk closure
if they don't fix violations before the follow-up inspection.

Critical violations (critical_flag='Critical') -> urgency='hot'
Non-critical violations -> urgency='warm'

Lead categories: Commercial Cleaning, Pest Control, HVAC, Kitchen Equipment Repair
"""
import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# NYC Open Data configuration
# -------------------------------------------------------------------
NYC_OPEN_DATA_BASE = 'https://data.cityofnewyork.us/resource'
NYC_RESTAURANT_INSPECTIONS_DATASET = '43nn-pn8j'

# Long Island health department URLs (HTML scrape targets)
LI_HEALTH_URLS = {
    'nassau': 'https://www.nassaucountyny.gov/health-inspections',
    'suffolk': 'https://www.suffolkcountyny.gov/health-inspections',
}

# Borough code mapping for NYC data
BORO_MAP = {
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

# Violation description keywords -> services needed
VIOLATION_SERVICE_MAP = {
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'rat': ['pest control', 'exterminator'],
    'roach': ['pest control', 'exterminator'],
    'cockroach': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'fly': ['pest control', 'exterminator'],
    'vermin': ['pest control', 'exterminator'],
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber', 'sewer service'],
    'ventilation': ['HVAC'],
    'exhaust': ['HVAC'],
    'hood': ['commercial kitchen cleaning', 'HVAC'],
    'temperature': ['HVAC', 'refrigeration repair'],
    'refriger': ['refrigeration repair', 'kitchen equipment repair'],
    'cooler': ['refrigeration repair', 'kitchen equipment repair'],
    'freezer': ['refrigeration repair', 'kitchen equipment repair'],
    'cold holding': ['refrigeration repair', 'kitchen equipment repair'],
    'hot holding': ['kitchen equipment repair'],
    'cleaning': ['commercial cleaning', 'deep cleaning'],
    'sanit': ['commercial cleaning', 'deep cleaning'],
    'wash': ['commercial cleaning', 'plumber'],
    'floor': ['commercial cleaning', 'flooring'],
    'ceiling': ['general contractor'],
    'wall': ['painter', 'general contractor'],
    'mold': ['mold remediation'],
    'grease': ['grease trap cleaning', 'commercial kitchen cleaning'],
    'fire': ['fire safety', 'electrician'],
    'extinguisher': ['fire safety'],
    'electrical': ['electrician'],
    'lighting': ['electrician'],
    'trash': ['commercial cleaning', 'waste management'],
    'garbage': ['commercial cleaning', 'waste management'],
    'restroom': ['plumber', 'commercial cleaning'],
    'hand wash': ['plumber', 'commercial cleaning'],
}

# Default services for any health violation
DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'HVAC', 'kitchen equipment repair']


class HealthViolationScraper(BaseScraper):
    MONITOR_NAME = 'ny_health_violation'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 40
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _parse_date(date_str):
    """Parse common date formats from health department data."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y',
        '%Y-%m-%dT%H:%M:%S', '%m-%d-%Y',
        '%b %d, %Y', '%B %d, %Y',
        '%d-%b-%Y', '%Y/%m/%d',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue

    # ISO format fallback
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        pass

    return None


def _detect_services(violation_text):
    """Map violation description text to relevant services needed."""
    if not violation_text:
        return DEFAULT_SERVICES

    text_lower = violation_text.lower()
    services = set()

    for key, service_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(service_list)

    return list(services) if services else DEFAULT_SERVICES


def _build_nyc_address(record):
    """Build full address from NYC Open Data inspection fields."""
    parts = []
    building = record.get('building', '').strip()
    street = record.get('street', '').strip()
    if building and street:
        parts.append(f'{building} {street}')
    elif street:
        parts.append(street)

    boro = record.get('boro', '')
    boro_name = BORO_MAP.get(str(boro).upper(), str(boro))
    if boro_name:
        parts.append(boro_name)

    parts.append('NY')

    zipcode = record.get('zipcode', '').strip()
    if zipcode:
        parts.append(zipcode)

    return ', '.join(parts)


def _fetch_nyc_inspections(scraper, days):
    """
    Fetch NYC restaurant inspection results from NYC Open Data SODA API.
    Returns list of violation dicts grouped by restaurant.
    """
    cutoff_date = (timezone.now() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')

    # Query for recent inspections with violations
    url = (
        f'{NYC_OPEN_DATA_BASE}/{NYC_RESTAURANT_INSPECTIONS_DATASET}.json'
        f'?$where=inspection_date > \'{cutoff_date}\''
        f'&$limit=1000'
        f'&$order=inspection_date DESC'
    )

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_health_violation] Error fetching NYC Open Data: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[ny_health_violation] NYC Open Data returned '
            f'{resp.status_code if resp else "None"}'
        )
        return []

    try:
        data = resp.json()
    except Exception:
        logger.error('[ny_health_violation] Failed to parse NYC Open Data JSON')
        return []

    if not isinstance(data, list):
        return []

    # Group violations by restaurant (CAMIS is unique restaurant ID)
    restaurants = {}
    for record in data:
        camis = record.get('camis', '')
        if not camis:
            continue

        if camis not in restaurants:
            restaurants[camis] = {
                'camis': camis,
                'business_name': record.get('dba', '').strip(),
                'address': _build_nyc_address(record),
                'boro': BORO_MAP.get(
                    str(record.get('boro', '')).upper(),
                    str(record.get('boro', '')),
                ),
                'zipcode': record.get('zipcode', '').strip(),
                'cuisine': record.get('cuisine_description', '').strip(),
                'inspection_date': record.get('inspection_date', ''),
                'score': record.get('score', ''),
                'grade': record.get('grade', ''),
                'violations': [],
                'has_critical': False,
                'source': 'nyc_open_data',
            }

        violation_desc = record.get('violation_description', '').strip()
        violation_code = record.get('violation_code', '').strip()
        critical_flag = record.get('critical_flag', '').strip()

        if violation_desc:
            restaurants[camis]['violations'].append({
                'code': violation_code,
                'description': violation_desc,
                'critical': critical_flag.lower() == 'critical',
            })
            if critical_flag.lower() == 'critical':
                restaurants[camis]['has_critical'] = True

    logger.info(
        f'[ny_health_violation] Fetched {len(data)} violation records for '
        f'{len(restaurants)} restaurants from NYC Open Data'
    )
    return list(restaurants.values())


def _scrape_li_health_inspections(scraper, county):
    """
    Scrape Long Island (Nassau/Suffolk) county health department sites.
    Returns list of violation dicts.
    """
    county_lower = county.lower() if county else ''
    url = LI_HEALTH_URLS.get(county_lower)
    if not url:
        logger.warning(f'[ny_health_violation] No URL configured for county: {county}')
        return []

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_health_violation] Error fetching {county} health dept: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[ny_health_violation] {county} health dept returned '
            f'{resp.status_code if resp else "None"}'
        )
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.select_one('table.inspection-results') or soup.select_one('table')
    if not table:
        logger.warning(f'[ny_health_violation] No table found on {county} health dept page')
        return []

    rows = table.select('tr')
    restaurants = []

    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if len(cells) < 3:
            continue

        try:
            biz_name = cells[0].get_text(strip=True)
            address = cells[1].get_text(strip=True) if len(cells) > 1 else ''
            inspection_date = cells[2].get_text(strip=True) if len(cells) > 2 else ''
            violations_text = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            score = cells[4].get_text(strip=True) if len(cells) > 4 else ''

            if not biz_name:
                continue

            # Check for critical keywords
            has_critical = any(
                term in violations_text.lower()
                for term in ('critical', 'imminent', 'closure', 'shut down')
            )

            restaurants.append({
                'business_name': biz_name,
                'address': f'{address}, {county.title()} County, NY' if address else '',
                'boro': '',
                'zipcode': '',
                'cuisine': '',
                'inspection_date': inspection_date,
                'score': score,
                'grade': '',
                'violations': [{'description': violations_text, 'critical': has_critical}],
                'has_critical': has_critical,
                'source': f'{county_lower}_health_dept',
            })
        except (IndexError, AttributeError):
            continue

    logger.info(f'[ny_health_violation] Scraped {len(restaurants)} records from {county} health dept')
    return restaurants


def monitor_ny_health_violations(source='nyc', county=None, days=30, dry_run=False, remote=False):
    """
    Monitor NY health department violations for restaurant failures.

    Health violations are high-urgency leads — restaurants must fix violations
    before follow-up inspection or face closure.

    Args:
        source: Data source — 'nyc' for NYC Open Data, 'li' for Long Island,
                'all' for both
        county: County filter for Long Island ('nassau', 'suffolk').
                Ignored for NYC source.
        days: How many days back to search (default: 30)
        dry_run: If True, log matches without creating Lead records
        remote: If True, skip HTML scraping and use only API sources

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = HealthViolationScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    stats = {
        'sources_checked': 0,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    all_restaurants = []
    cutoff = timezone.now() - timedelta(days=days)

    # Source 1: NYC Open Data
    if source in ('nyc', 'all'):
        stats['sources_checked'] += 1
        try:
            nyc_results = _fetch_nyc_inspections(scraper, days)
            all_restaurants.extend(nyc_results)
        except RateLimitHit:
            logger.warning('[ny_health_violation] Rate limited on NYC Open Data')
        except Exception as e:
            logger.error(f'[ny_health_violation] Error with NYC Open Data: {e}')
            stats['errors'] += 1

    # Source 2: Long Island county health departments (HTML scrape)
    if source in ('li', 'all') and not remote and not scraper.is_stopped:
        counties_to_check = []
        if county:
            counties_to_check = [county.lower()]
        else:
            counties_to_check = ['nassau', 'suffolk']

        for cty in counties_to_check:
            if scraper.is_stopped:
                break
            stats['sources_checked'] += 1
            try:
                li_results = _scrape_li_health_inspections(scraper, cty)
                all_restaurants.extend(li_results)
            except RateLimitHit:
                logger.warning(f'[ny_health_violation] Rate limited on {cty} health dept')
                break
            except Exception as e:
                logger.error(f'[ny_health_violation] Error with {cty} health dept: {e}')
                stats['errors'] += 1

    stats['items_scraped'] = len(all_restaurants)

    # Process each restaurant with violations
    seen = set()
    for restaurant in all_restaurants:
        try:
            biz_name = restaurant.get('business_name', '').strip()
            address = restaurant.get('address', '').strip()
            violations = restaurant.get('violations', [])
            has_critical = restaurant.get('has_critical', False)
            inspection_date_str = restaurant.get('inspection_date', '')
            score = restaurant.get('score', '')
            grade = restaurant.get('grade', '')
            cuisine = restaurant.get('cuisine', '')
            camis = restaurant.get('camis', '')

            if not biz_name:
                continue

            # Must have violations to be a lead
            if not violations:
                continue

            # Dedup by restaurant identity
            dedup_key = f'{biz_name.lower()}|{address.lower()}|{inspection_date_str}'
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Parse inspection date
            inspection_date = _parse_date(inspection_date_str)
            if inspection_date and inspection_date < cutoff:
                continue

            # Combine violation descriptions
            all_violation_text = ' '.join(
                v.get('description', '') for v in violations
            )

            # Detect services from violation text
            services = _detect_services(all_violation_text)

            # Determine urgency
            if has_critical:
                urgency_level = 'hot'
                urgency_note = 'CRITICAL violation — restaurant risks closure'
            else:
                urgency_level = 'warm'
                urgency_note = 'Non-critical violation — must fix before follow-up'

            # Count critical vs non-critical
            critical_count = sum(1 for v in violations if v.get('critical'))
            total_violations = len(violations)

            # Build lead content
            content_parts = [
                f'HEALTH VIOLATION: {biz_name}',
            ]
            if cuisine:
                content_parts.append(f'Cuisine: {cuisine}')
            if address:
                content_parts.append(f'Address: {address}')
            if score:
                content_parts.append(f'Score: {score}')
            if grade:
                content_parts.append(f'Grade: {grade}')
            if inspection_date:
                days_ago = (timezone.now() - inspection_date).days
                content_parts.append(f'Inspected: {days_ago} days ago')

            content_parts.append(
                f'Violations: {total_violations} total '
                f'({critical_count} critical)'
            )

            # Include top violation descriptions (truncated)
            for v in violations[:5]:
                desc = v.get('description', '')
                if desc:
                    prefix = '[CRITICAL] ' if v.get('critical') else ''
                    content_parts.append(f'  - {prefix}{desc[:200]}')

            content_parts.append(f'Urgency: {urgency_note}')
            content_parts.append(f'Services needed: {", ".join(services[:6])}')

            content = '\n'.join(content_parts)

            source_url = (
                f'{NYC_OPEN_DATA_BASE}/{NYC_RESTAURANT_INSPECTIONS_DATASET}'
                if restaurant.get('source') == 'nyc_open_data'
                else LI_HEALTH_URLS.get(restaurant.get('source', '').replace('_health_dept', ''), '')
            )

            if dry_run:
                logger.info(
                    f'[DRY RUN] Would create health violation lead: '
                    f'{biz_name} ({critical_count} critical, {total_violations} total)'
                )
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author='',
                posted_at=inspection_date,
                raw_data={
                    'source_type': 'health_violation',
                    'business_name': biz_name,
                    'address': address,
                    'camis': camis,
                    'cuisine': cuisine,
                    'inspection_date': inspection_date_str,
                    'score': score,
                    'grade': grade,
                    'critical_count': critical_count,
                    'total_violations': total_violations,
                    'has_critical': has_critical,
                    'urgency_level': urgency_level,
                    'violation_descriptions': [
                        v.get('description', '')[:300] for v in violations[:10]
                    ],
                    'services_mapped': services,
                    'data_source': restaurant.get('source', ''),
                },
                state='NY',
                region=restaurant.get('boro', ''),
                source_group='public_records',
                source_type='health_inspections',
                contact_business=biz_name,
                contact_address=address,
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1

        except RateLimitHit:
            break
        except Exception as e:
            logger.error(
                f'[ny_health_violation] Error processing violation for '
                f'{restaurant.get("business_name", "unknown")}: {e}'
            )
            stats['errors'] += 1

    logger.info(f'NY health violation monitor complete: {stats}')
    return stats
