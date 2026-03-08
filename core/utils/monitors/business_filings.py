"""
New business filing monitor for SalesSignal AI.

Scrapes state corporation databases for recently filed LLCs and corporations.
New businesses need services before they even open — commercial cleaning,
IT setup, insurance, signage, interior buildout, HVAC, security.

Each state is configured via a StateBusinessFilingSource database record —
adding a new state is a database entry, not a code change.

Nationwide — no hardcoded regions.
"""
import csv
import io
import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import StateBusinessFilingSource
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Business type → likely services needed
BUSINESS_SERVICE_MAP = {
    'dental': ['commercial cleaning', 'IT support', 'insurance', 'HVAC', 'plumber'],
    'medical': ['commercial cleaning', 'IT support', 'insurance', 'HVAC', 'medical waste'],
    'clinic': ['commercial cleaning', 'IT support', 'insurance', 'HVAC'],
    'restaurant': ['commercial cleaning', 'pest control', 'HVAC', 'plumber', 'grease trap', 'signage'],
    'cafe': ['commercial cleaning', 'pest control', 'HVAC', 'signage'],
    'bar': ['commercial cleaning', 'pest control', 'HVAC', 'security', 'signage'],
    'bakery': ['commercial cleaning', 'pest control', 'HVAC', 'signage'],
    'salon': ['commercial cleaning', 'plumber', 'HVAC', 'signage', 'interior design'],
    'spa': ['commercial cleaning', 'plumber', 'HVAC', 'interior design'],
    'gym': ['commercial cleaning', 'HVAC', 'plumber', 'signage', 'security'],
    'fitness': ['commercial cleaning', 'HVAC', 'plumber', 'signage'],
    'retail': ['commercial cleaning', 'security', 'signage', 'IT support'],
    'store': ['commercial cleaning', 'security', 'signage', 'IT support'],
    'shop': ['commercial cleaning', 'security', 'signage'],
    'consulting': ['IT support', 'office cleaning', 'insurance'],
    'law': ['IT support', 'office cleaning', 'insurance', 'security'],
    'legal': ['IT support', 'office cleaning', 'insurance'],
    'accounting': ['IT support', 'office cleaning', 'insurance'],
    'real estate': ['office cleaning', 'IT support', 'signage', 'photography'],
    'construction': ['insurance', 'accounting', 'IT support'],
    'landscaping': ['insurance', 'accounting', 'equipment repair'],
    'plumbing': ['insurance', 'accounting', 'IT support'],
    'electric': ['insurance', 'accounting', 'IT support'],
    'auto': ['commercial cleaning', 'signage', 'security', 'HVAC'],
    'daycare': ['commercial cleaning', 'pest control', 'security', 'insurance', 'HVAC'],
    'child care': ['commercial cleaning', 'pest control', 'security', 'insurance'],
    'veterinar': ['commercial cleaning', 'pest control', 'HVAC', 'plumber'],
    'pet': ['commercial cleaning', 'pest control', 'signage'],
    'hotel': ['commercial cleaning', 'HVAC', 'plumber', 'pest control', 'security', 'landscaping'],
    'motel': ['commercial cleaning', 'HVAC', 'pest control', 'security'],
    'warehouse': ['commercial cleaning', 'security', 'HVAC', 'pest control'],
    'office': ['office cleaning', 'IT support', 'security', 'HVAC'],
    'tech': ['IT support', 'office cleaning', 'security'],
}

# Default services for any new business
DEFAULT_SERVICES = ['commercial cleaning', 'IT support', 'insurance', 'signage', 'HVAC']


class BusinessFilingScraper(BaseScraper):
    MONITOR_NAME = 'business_filing'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _detect_services_from_name(business_name):
    """Map a business name to likely services needed."""
    if not business_name:
        return DEFAULT_SERVICES

    name_lower = business_name.lower()
    services = set()

    for key, service_list in BUSINESS_SERVICE_MAP.items():
        if key in name_lower:
            services.update(service_list)

    return list(services) if services else DEFAULT_SERVICES


def _parse_date(date_str):
    """Parse common date formats from state filing portals."""
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
    """Scrape business filings from an HTML table page."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = source.css_selectors or {}

    table_sel = selectors.get('table_selector', 'table')
    table = soup.select_one(table_sel)
    if not table:
        logger.warning(f'[business_filing] No table found at {source.source_url}')
        return []

    row_sel = selectors.get('row_selector', 'tr')
    rows = table.select(row_sel)

    filings = []
    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if not cells:
            continue

        try:
            filing = {
                'business_name': _extract_cell(cells, selectors.get('business_name', '0')),
                'filing_date': _extract_cell(cells, selectors.get('filing_date', '1')),
                'entity_type': _extract_cell(cells, selectors.get('entity_type', '2')),
                'registered_agent': _extract_cell(cells, selectors.get('registered_agent', '')),
                'address': _extract_cell(cells, selectors.get('address', '')),
                'status': _extract_cell(cells, selectors.get('status', '')),
            }
            if filing['business_name']:
                filings.append(filing)
        except (IndexError, AttributeError):
            continue

    return filings


def _scrape_api(scraper, source):
    """Fetch business filings from an API endpoint."""
    config = source.api_config or {}
    endpoint = config.get('endpoint', source.source_url)
    params = dict(config.get('params', {}))

    # Add date range if configured
    search_params = source.search_params or {}
    date_range = search_params.get('date_range_days', 30)
    since = (timezone.now() - timedelta(days=date_range)).strftime('%Y-%m-%d')
    if 'date_param' in config:
        params[config['date_param']] = since

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
    filings = []
    for item in items:
        filing = {
            'business_name': item.get(selectors.get('business_name', 'name'), ''),
            'filing_date': item.get(selectors.get('filing_date', 'date'), ''),
            'entity_type': item.get(selectors.get('entity_type', 'type'), ''),
            'registered_agent': item.get(selectors.get('registered_agent', 'agent'), ''),
            'address': item.get(selectors.get('address', 'address'), ''),
            'status': item.get(selectors.get('status', 'status'), ''),
        }
        if filing['business_name']:
            filings.append(filing)

    return filings


def _scrape_csv(scraper, source):
    """Download and parse a CSV of business filings."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    selectors = source.css_selectors or {}
    reader = csv.DictReader(io.StringIO(resp.text))

    filings = []
    for row in reader:
        filing = {
            'business_name': row.get(selectors.get('business_name', 'name'), ''),
            'filing_date': row.get(selectors.get('filing_date', 'date'), ''),
            'entity_type': row.get(selectors.get('entity_type', 'type'), ''),
            'registered_agent': row.get(selectors.get('registered_agent', 'agent'), ''),
            'address': row.get(selectors.get('address', 'address'), ''),
            'status': row.get(selectors.get('status', 'status'), ''),
        }
        if filing['business_name']:
            filings.append(filing)

    return filings


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
        logger.warning(f'[business_filing] Unknown scrape method: {method}')
        return []


def monitor_business_filings(source_ids=None, max_age_days=30, dry_run=False):
    """
    Monitor state corporation databases for new business filings.

    Reads active StateBusinessFilingSource records and scrapes each portal.
    Maps new businesses to service categories automatically.
    Creates Lead records with platform='business_filing'.

    Args:
        source_ids: list of StateBusinessFilingSource IDs (default: all active)
        max_age_days: skip filings older than this many days
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = BusinessFilingScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    sources = StateBusinessFilingSource.objects.filter(is_active=True)
    if source_ids:
        sources = sources.filter(id__in=source_ids)

    if not sources.exists():
        logger.info('No active StateBusinessFilingSource records configured')
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
        logger.info(f'[business_filing] Scraping: {source.state_name} ({source.state})')

        try:
            filings = _scrape_source(scraper, source)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[business_filing] Error scraping {source.state_name}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(filings)

        for filing in filings:
            try:
                name = filing.get('business_name', '')
                filing_date = _parse_date(filing.get('filing_date', ''))
                entity_type = filing.get('entity_type', '')
                address = filing.get('address', '')
                agent = filing.get('registered_agent', '')

                if not name:
                    continue

                # Skip old filings
                if filing_date and filing_date < cutoff:
                    continue

                # Detect services from business name
                services = _detect_services_from_name(name)

                # Build lead content
                content_parts = [
                    f'New Business Filed: {name}',
                ]
                if entity_type:
                    content_parts.append(f'Entity Type: {entity_type}')
                if address:
                    content_parts.append(f'Registered Address: {address}')
                if filing_date:
                    days_ago = (timezone.now() - filing_date).days
                    content_parts.append(f'Filed: {days_ago} days ago')
                content_parts.append(f'State: {source.state_name}')
                content_parts.append(f'Services likely needed: {", ".join(services[:6])}')

                content = '\n'.join(content_parts)

                if dry_run:
                    logger.info(f'[DRY RUN] Would create filing lead: {name}')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='business_filing',
                    source_url=source.source_url,
                    content=content,
                    author='',
                    posted_at=filing_date,
                    raw_data={
                        'business_name': name,
                        'entity_type': entity_type,
                        'address': address,
                        'registered_agent': agent,
                        'state': source.state,
                        'state_name': source.state_name,
                        'services_mapped': services,
                    },
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except RateLimitHit:
                break
            except Exception as e:
                logger.error(f'[business_filing] Error processing filing {name}: {e}')
                stats['errors'] += 1

        # Update source last_scraped
        source.last_scraped = timezone.now()
        source.save(update_fields=['last_scraped'])

    logger.info(f'Business filing monitor complete: {stats}')
    return stats
