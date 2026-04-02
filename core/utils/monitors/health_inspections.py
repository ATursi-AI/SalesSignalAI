"""
Health inspection monitor for SalesSignal AI.

Scrapes county/city health department databases for restaurant inspection failures.
Restaurants that fail health inspections need deep cleaning, pest control,
kitchen equipment repair, HVAC work, and plumbing — urgently.

A failed inspection is both forced demand AND time-sensitive. The restaurant
must fix violations before the follow-up inspection or face closure.

Each jurisdiction is configured via a HealthInspectionSource database record —
adding a new jurisdiction is a database entry, not a code change.

Nationwide — no hardcoded regions.
"""
import csv
import io
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import HealthInspectionSource
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Inspection violation type → services needed
VIOLATION_SERVICE_MAP = {
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'cockroach': ['pest control', 'exterminator'],
    'mice': ['pest control', 'exterminator'],
    'rat': ['pest control', 'exterminator'],
    'plumbing': ['plumber'],
    'leak': ['plumber'],
    'drain': ['plumber', 'drain cleaning'],
    'sewage': ['plumber', 'sewer service'],
    'ventilation': ['HVAC'],
    'exhaust': ['HVAC'],
    'hood': ['commercial kitchen cleaning', 'HVAC'],
    'temperature': ['HVAC', 'refrigeration repair'],
    'refriger': ['refrigeration repair'],
    'cooler': ['refrigeration repair'],
    'freezer': ['refrigeration repair'],
    'cleaning': ['commercial cleaning', 'deep cleaning'],
    'sanitation': ['commercial cleaning', 'deep cleaning'],
    'sanitiz': ['commercial cleaning'],
    'floor': ['flooring', 'commercial cleaning'],
    'ceiling': ['general contractor'],
    'wall': ['painter', 'general contractor'],
    'mold': ['mold remediation'],
    'water damage': ['water damage restoration'],
    'electrical': ['electrician'],
    'lighting': ['electrician'],
    'fire': ['fire safety', 'electrician'],
    'extinguisher': ['fire safety'],
    'grease': ['grease trap cleaning', 'commercial kitchen cleaning'],
    'hood cleaning': ['commercial kitchen cleaning'],
    'trash': ['commercial cleaning', 'waste management'],
    'dumpster': ['waste management'],
    'restroom': ['plumber', 'commercial cleaning'],
    'handwash': ['plumber', 'commercial cleaning'],
}

# Default services for any failed inspection
DEFAULT_SERVICES = ['commercial cleaning', 'pest control', 'plumber']


class HealthInspectionScraper(BaseScraper):
    MONITOR_NAME = 'health_inspection'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _detect_services(violations_text):
    """Map inspection violation descriptions to relevant services."""
    if not violations_text:
        return DEFAULT_SERVICES

    text_lower = violations_text.lower()
    services = set()

    for key, service_list in VIOLATION_SERVICE_MAP.items():
        if key in text_lower:
            services.update(service_list)

    return list(services) if services else DEFAULT_SERVICES


def _parse_date(date_str):
    """Parse common date formats from health department portals."""
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

    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        pass

    return None


def _parse_score(score_str):
    """Parse an inspection score from various formats."""
    if not score_str:
        return None
    try:
        # Handle "85/100", "85%", "85", "B" grade formats
        score_str = score_str.strip().replace('%', '').split('/')[0]
        return float(score_str)
    except (ValueError, IndexError):
        pass

    # Letter grade mapping
    grade_map = {'A': 95, 'B': 85, 'C': 75, 'D': 65, 'F': 50}
    return grade_map.get(score_str.strip().upper())


def _extract_cell(cells, selector):
    """Extract text from a table cell by index."""
    if not selector:
        return ''
    try:
        idx = int(selector)
        if 0 <= idx < len(cells):
            return cells[idx].get_text(strip=True)
    except ValueError:
        pass
    return ''


def _scrape_html_table(scraper, source):
    """Scrape health inspections from an HTML table page."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = source.css_selectors or {}

    table_sel = selectors.get('table_selector', 'table')
    table = soup.select_one(table_sel)
    if not table:
        logger.warning(f'[health_inspection] No table found at {source.source_url}')
        return []

    row_sel = selectors.get('row_selector', 'tr')
    rows = table.select(row_sel)

    inspections = []
    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if not cells:
            continue

        try:
            inspection = {
                'restaurant_name': _extract_cell(cells, selectors.get('restaurant_name', '0')),
                'address': _extract_cell(cells, selectors.get('address', '1')),
                'inspection_date': _extract_cell(cells, selectors.get('inspection_date', '2')),
                'score': _extract_cell(cells, selectors.get('score', '3')),
                'grade': _extract_cell(cells, selectors.get('grade', '')),
                'violations': _extract_cell(cells, selectors.get('violations', '')),
            }
            if inspection['restaurant_name']:
                inspections.append(inspection)
        except (IndexError, AttributeError):
            continue

    return inspections


def _scrape_api(scraper, source):
    """Fetch health inspections from an API endpoint."""
    config = source.api_config or {}
    endpoint = config.get('endpoint', source.source_url)
    params = dict(config.get('params', {}))
    headers = config.get('headers', {})

    resp = scraper.get(endpoint, params=params, headers=headers)
    if not resp or resp.status_code != 200:
        return []

    try:
        data = resp.json()
    except Exception:
        return []

    result_key = config.get('result_key', '')
    items = data.get(result_key, data) if result_key else data
    if not isinstance(items, list):
        items = [items]

    selectors = source.css_selectors or {}
    inspections = []
    for item in items:
        inspection = {
            'restaurant_name': item.get(selectors.get('restaurant_name', 'name'), ''),
            'address': item.get(selectors.get('address', 'address'), ''),
            'inspection_date': item.get(selectors.get('inspection_date', 'date'), ''),
            'score': str(item.get(selectors.get('score', 'score'), '')),
            'grade': item.get(selectors.get('grade', 'grade'), ''),
            'violations': item.get(selectors.get('violations', 'violations'), ''),
        }
        if inspection['restaurant_name']:
            inspections.append(inspection)

    return inspections


def _scrape_csv(scraper, source):
    """Download and parse a CSV of health inspections."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    selectors = source.css_selectors or {}
    reader = csv.DictReader(io.StringIO(resp.text))

    inspections = []
    for row in reader:
        inspection = {
            'restaurant_name': row.get(selectors.get('restaurant_name', 'name'), ''),
            'address': row.get(selectors.get('address', 'address'), ''),
            'inspection_date': row.get(selectors.get('inspection_date', 'date'), ''),
            'score': row.get(selectors.get('score', 'score'), ''),
            'grade': row.get(selectors.get('grade', 'grade'), ''),
            'violations': row.get(selectors.get('violations', 'violations'), ''),
        }
        if inspection['restaurant_name']:
            inspections.append(inspection)

    return inspections


def _scrape_source(scraper, source):
    """Dispatch to the correct scraper based on source.scrape_method."""
    method = source.scrape_method
    if method == 'html_table':
        return _scrape_html_table(scraper, source)
    elif method == 'api':
        return _scrape_api(scraper, source)
    elif method == 'csv_download':
        return _scrape_csv(scraper, source)
    else:
        logger.warning(f'[health_inspection] Unknown scrape method: {method}')
        return []


def monitor_health_inspections(source_ids=None, max_age_days=30, dry_run=False):
    """
    Monitor health department databases for restaurant inspection failures.

    Reads active HealthInspectionSource records and scrapes each portal.
    Focuses on failed inspections (score below threshold).
    Maps violation types to service categories automatically.
    Creates Lead records with platform='health_inspection'.

    Args:
        source_ids: list of HealthInspectionSource IDs (default: all active)
        max_age_days: skip inspections older than this many days
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = HealthInspectionScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    sources = HealthInspectionSource.objects.filter(is_active=True)
    if source_ids:
        sources = sources.filter(id__in=source_ids)

    if not sources.exists():
        logger.info('No active HealthInspectionSource records configured')
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0}

    stats = {
        'sources_checked': 0,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    cutoff = timezone.now() - timedelta(days=max_age_days)
    sources_list = scraper.shuffle(list(sources))

    for source in sources_list:
        if scraper.is_stopped:
            break

        stats['sources_checked'] += 1
        logger.info(f'[health_inspection] Scraping: {source.jurisdiction}, {source.state}')

        try:
            inspections = _scrape_source(scraper, source)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[health_inspection] Error scraping {source.jurisdiction}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(inspections)

        for inspection in inspections:
            try:
                name = inspection.get('restaurant_name', '')
                address = inspection.get('address', '')
                insp_date = _parse_date(inspection.get('inspection_date', ''))
                score = _parse_score(inspection.get('score', ''))
                grade = inspection.get('grade', '')
                violations_text = inspection.get('violations', '')

                if not name:
                    continue

                # Skip old inspections
                if insp_date and insp_date < cutoff:
                    continue

                # Only create leads for FAILING inspections
                threshold = source.failing_threshold or 70
                is_failure = False

                if score is not None and score < threshold:
                    is_failure = True
                elif grade and grade.upper() in ('C', 'D', 'F'):
                    is_failure = True
                elif violations_text and any(
                    term in violations_text.lower()
                    for term in ('critical', 'imminent', 'closure', 'failed', 'unsatisfactory')
                ):
                    is_failure = True

                if not is_failure:
                    continue

                # Detect services from violations
                services = _detect_services(violations_text)

                # Build lead content
                content_parts = [
                    f'HEALTH INSPECTION FAILURE: {name}',
                ]
                if address:
                    content_parts.append(f'Address: {address}')
                if score is not None:
                    content_parts.append(f'Score: {score}/{100}')
                if grade:
                    content_parts.append(f'Grade: {grade}')
                if insp_date:
                    days_ago = (timezone.now() - insp_date).days
                    content_parts.append(f'Inspected: {days_ago} days ago')
                if violations_text:
                    content_parts.append(f'Violations: {violations_text[:500]}')
                content_parts.append(f'Jurisdiction: {source.jurisdiction}, {source.state}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content_parts.append('Restaurant must fix violations before follow-up inspection.')

                content = '\n'.join(content_parts)

                if dry_run:
                    logger.info(f'[DRY RUN] Would create inspection lead: {name} (score: {score})')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='health_inspection',
                    source_url=source.source_url,
                    content=content,
                    author='',
                    posted_at=insp_date,
                    raw_data={
                        'restaurant_name': name,
                        'address': address,
                        'score': score,
                        'grade': grade,
                        'violations': violations_text[:500],
                        'jurisdiction': source.jurisdiction,
                        'state': source.state,
                        'services_mapped': services,
                    },
                    state=source.state or '',
                    region=source.jurisdiction or '',
                    source_group='public_records',
                    source_type='health_inspections',
                    contact_business=name,
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
                logger.error(f'[health_inspection] Error processing inspection for {name}: {e}')
                stats['errors'] += 1

        # Update source last_scraped
        source.last_scraped = timezone.now()
        source.save(update_fields=['last_scraped'])

    logger.info(f'Health inspection monitor complete: {stats}')
    return stats
