"""
Code violation monitor for SalesSignal AI.

Scrapes municipal code enforcement databases for property violations.
When a property gets a code violation, the owner is legally REQUIRED
to fix it — this is forced demand. Overgrown lawn = mandatory landscaping.
Damaged roof = mandatory roofing. Peeling paint = mandatory painting.

Each municipality is configured via a CodeViolationSource database record —
adding a new city is a database entry, not a code change.

Nationwide — no hardcoded regions.
"""
import csv
import io
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import CodeViolationSource
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Violation type → relevant services needed to fix
VIOLATION_SERVICE_MAP = {
    'overgrown': ['landscaping', 'lawn care', 'tree service'],
    'vegetation': ['landscaping', 'lawn care', 'tree service'],
    'lawn': ['landscaping', 'lawn care'],
    'weeds': ['landscaping', 'lawn care'],
    'tree': ['tree service', 'landscaping'],
    'roof': ['roofing', 'general contractor'],
    'roofing': ['roofing', 'general contractor'],
    'paint': ['painter', 'general contractor'],
    'exterior': ['painter', 'siding', 'general contractor'],
    'fence': ['fencing', 'general contractor'],
    'sidewalk': ['concrete', 'general contractor', 'masonry'],
    'driveway': ['concrete', 'asphalt', 'general contractor'],
    'plumbing': ['plumber'],
    'sewer': ['plumber', 'sewer service'],
    'drain': ['plumber', 'drain cleaning'],
    'electrical': ['electrician'],
    'wiring': ['electrician'],
    'hvac': ['HVAC'],
    'heating': ['HVAC'],
    'cooling': ['HVAC'],
    'pest': ['pest control', 'exterminator'],
    'rodent': ['pest control', 'exterminator'],
    'insect': ['pest control', 'exterminator'],
    'mold': ['mold remediation', 'water damage restoration'],
    'water damage': ['water damage restoration', 'plumber'],
    'structural': ['general contractor', 'structural engineer'],
    'foundation': ['foundation repair', 'general contractor'],
    'window': ['window replacement', 'glazier'],
    'gutter': ['gutter cleaning', 'gutter installation'],
    'trash': ['junk removal', 'cleaning'],
    'debris': ['junk removal', 'cleaning'],
    'abandoned vehicle': ['towing', 'junk removal'],
    'sign': ['signage'],
    'fire': ['fire damage restoration', 'general contractor'],
    'smoke detector': ['electrician', 'fire safety'],
    'pool': ['pool service', 'fencing'],
    'stair': ['general contractor', 'carpentry'],
    'handrail': ['general contractor', 'carpentry'],
    'porch': ['general contractor', 'carpentry'],
    'deck': ['deck builder', 'general contractor'],
    'garage': ['garage door', 'general contractor'],
    'retaining wall': ['masonry', 'landscaping'],
    'graffiti': ['pressure washing', 'painter'],
    'sanitation': ['commercial cleaning', 'plumber'],
}

# Default services for violations that don't match specific types
DEFAULT_SERVICES = ['general contractor', 'handyman']


class CodeViolationScraper(BaseScraper):
    MONITOR_NAME = 'code_violation'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _detect_services(violation_type):
    """Map a violation type to relevant services needed to fix it."""
    if not violation_type:
        return DEFAULT_SERVICES

    vtype_lower = violation_type.lower()
    services = set()

    for key, service_list in VIOLATION_SERVICE_MAP.items():
        if key in vtype_lower:
            services.update(service_list)

    return list(services) if services else DEFAULT_SERVICES


def _parse_date(date_str):
    """Parse common date formats from code enforcement portals."""
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
    """Scrape code violations from an HTML table page."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = source.css_selectors or {}

    table_sel = selectors.get('table_selector', 'table')
    table = soup.select_one(table_sel)
    if not table:
        logger.warning(f'[code_violation] No table found at {source.source_url}')
        return []

    row_sel = selectors.get('row_selector', 'tr')
    rows = table.select(row_sel)

    violations = []
    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if not cells:
            continue

        try:
            violation = {
                'address': _extract_cell(cells, selectors.get('address', '0')),
                'violation_type': _extract_cell(cells, selectors.get('violation_type', '1')),
                'violation_date': _extract_cell(cells, selectors.get('violation_date', '2')),
                'compliance_deadline': _extract_cell(cells, selectors.get('compliance_deadline', '')),
                'owner_name': _extract_cell(cells, selectors.get('owner_name', '')),
                'status': _extract_cell(cells, selectors.get('status', '')),
            }
            if violation['address']:
                violations.append(violation)
        except (IndexError, AttributeError):
            continue

    return violations


def _scrape_api(scraper, source):
    """Fetch code violations from an API endpoint."""
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
    violations = []
    for item in items:
        violation = {
            'address': item.get(selectors.get('address', 'address'), ''),
            'violation_type': item.get(selectors.get('violation_type', 'violation_type'), ''),
            'violation_date': item.get(selectors.get('violation_date', 'date'), ''),
            'compliance_deadline': item.get(selectors.get('compliance_deadline', 'deadline'), ''),
            'owner_name': item.get(selectors.get('owner_name', 'owner'), ''),
            'status': item.get(selectors.get('status', 'status'), ''),
        }
        if violation['address']:
            violations.append(violation)

    return violations


def _scrape_csv(scraper, source):
    """Download and parse a CSV of code violations."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    selectors = source.css_selectors or {}
    reader = csv.DictReader(io.StringIO(resp.text))

    violations = []
    for row in reader:
        violation = {
            'address': row.get(selectors.get('address', 'address'), ''),
            'violation_type': row.get(selectors.get('violation_type', 'violation_type'), ''),
            'violation_date': row.get(selectors.get('violation_date', 'date'), ''),
            'compliance_deadline': row.get(selectors.get('compliance_deadline', 'deadline'), ''),
            'owner_name': row.get(selectors.get('owner_name', 'owner'), ''),
            'status': row.get(selectors.get('status', 'status'), ''),
        }
        if violation['address']:
            violations.append(violation)

    return violations


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
        logger.warning(f'[code_violation] Unknown scrape method: {method}')
        return []


def monitor_code_violations(source_ids=None, max_age_days=30, dry_run=False):
    """
    Monitor municipal code enforcement databases for property violations.

    Reads active CodeViolationSource records and scrapes each portal.
    Maps violation types to service categories automatically.
    Creates Lead records with platform='code_violation'.

    Args:
        source_ids: list of CodeViolationSource IDs (default: all active)
        max_age_days: skip violations older than this many days
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = CodeViolationScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    sources = CodeViolationSource.objects.filter(is_active=True)
    if source_ids:
        sources = sources.filter(id__in=source_ids)

    if not sources.exists():
        logger.info('No active CodeViolationSource records configured')
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
    sources = scraper.shuffle(list(sources))

    for source in sources:
        if scraper.is_stopped:
            break

        stats['sources_checked'] += 1
        logger.info(f'[code_violation] Scraping: {source.municipality}, {source.state}')

        try:
            violations = _scrape_source(scraper, source)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[code_violation] Error scraping {source.municipality}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(violations)

        for violation in violations:
            try:
                address = violation.get('address', '')
                vtype = violation.get('violation_type', '')
                vdate = _parse_date(violation.get('violation_date', ''))
                deadline = violation.get('compliance_deadline', '')
                owner = violation.get('owner_name', '')

                if not address:
                    continue

                # Skip old violations
                if vdate and vdate < cutoff:
                    continue

                # Detect services needed
                services = _detect_services(vtype)

                # Build lead content
                content_parts = [
                    f'CODE VIOLATION: {vtype}' if vtype else 'CODE VIOLATION',
                    f'Address: {address}',
                ]
                if owner:
                    content_parts.append(f'Property Owner: {owner}')
                if vdate:
                    days_ago = (timezone.now() - vdate).days
                    content_parts.append(f'Violation Date: {days_ago} days ago')
                if deadline:
                    content_parts.append(f'Compliance Deadline: {deadline}')
                content_parts.append(f'Municipality: {source.municipality}, {source.state}')
                content_parts.append(f'Services needed: {", ".join(services[:6])}')
                content_parts.append('Property owner is legally required to fix this violation.')

                content = '\n'.join(content_parts)

                if dry_run:
                    logger.info(f'[DRY RUN] Would create violation lead: {vtype} at {address}')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='code_violation',
                    source_url=source.source_url,
                    content=content,
                    author='',
                    posted_at=vdate,
                    raw_data={
                        'address': address,
                        'violation_type': vtype,
                        'compliance_deadline': deadline,
                        'owner_name': owner,
                        'municipality': source.municipality,
                        'state': source.state,
                        'services_mapped': services,
                    },
                    state=source.state or '',
                    region=source.municipality or '',
                    source_group='public_records',
                    source_type='code_enforcement',
                    contact_name=owner,
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
                logger.error(f'[code_violation] Error processing violation at {address}: {e}')
                stats['errors'] += 1

        # Update source last_scraped
        source.last_scraped = timezone.now()
        source.save(update_fields=['last_scraped'])

    logger.info(f'Code violation monitor complete: {stats}')
    return stats
