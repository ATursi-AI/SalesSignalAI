"""
Building permit monitor for SalesSignal AI.

Scrapes county building permit portals for new permit filings.
Each county is configured via a PermitSource database record — adding a new
county is a database entry, not a code change.

Permit types are automatically mapped to service categories:
- Renovation/remodel → plumber, electrician, painter, flooring
- Roofing → roofer
- Plumbing → plumber
- Electrical → electrician
- New construction → all trades
- Demolition → general contractor, junk removal

Works nationwide — no hardcoded regions.
"""
import csv
import io
import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import PermitSource
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Permit type → relevant service keywords for matching
PERMIT_SERVICE_MAP = {
    'renovation': ['contractor', 'plumber', 'electrician', 'painter', 'flooring', 'drywall'],
    'remodel': ['contractor', 'plumber', 'electrician', 'painter', 'flooring', 'drywall'],
    'kitchen': ['contractor', 'plumber', 'electrician', 'countertop', 'cabinet', 'flooring'],
    'bathroom': ['contractor', 'plumber', 'electrician', 'tile', 'flooring'],
    'roofing': ['roofer', 'roofing contractor'],
    'roof': ['roofer', 'roofing contractor'],
    'plumbing': ['plumber', 'plumbing contractor'],
    'electrical': ['electrician', 'electrical contractor'],
    'hvac': ['hvac', 'heating', 'air conditioning'],
    'mechanical': ['hvac', 'heating', 'air conditioning'],
    'new construction': ['general contractor', 'plumber', 'electrician', 'roofer',
                         'hvac', 'painter', 'flooring', 'landscaper'],
    'addition': ['general contractor', 'plumber', 'electrician', 'roofer'],
    'demolition': ['general contractor', 'demolition', 'junk removal'],
    'fence': ['fence contractor', 'fencing'],
    'deck': ['deck builder', 'contractor'],
    'pool': ['pool contractor', 'pool builder'],
    'solar': ['solar installer', 'electrician'],
    'siding': ['siding contractor', 'exterior'],
    'window': ['window replacement', 'window installer'],
    'painting': ['painter', 'painting contractor'],
    'landscaping': ['landscaper', 'landscaping'],
    'foundation': ['foundation repair', 'general contractor'],
    'fire damage': ['restoration', 'general contractor', 'painter', 'electrician'],
    'water damage': ['restoration', 'plumber', 'mold remediation'],
}


class PermitScraper(BaseScraper):
    MONITOR_NAME = 'permit'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours — permits don't change that fast
    RESPECT_ROBOTS = True


def _detect_services_from_permit(permit_type):
    """Map a permit type string to relevant service keywords."""
    if not permit_type:
        return ['general contractor']

    permit_lower = permit_type.lower()
    services = set()

    for key, service_list in PERMIT_SERVICE_MAP.items():
        if key in permit_lower:
            services.update(service_list)

    return list(services) if services else ['general contractor']


def _parse_date(date_str):
    """Try to parse common date formats from permit portals."""
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

    # Try ISO format as fallback
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        pass

    return None


def _format_currency(value_str):
    """Extract numeric value from currency string like '$45,000' or '45000'."""
    if not value_str:
        return ''
    cleaned = re.sub(r'[^\d.]', '', str(value_str))
    try:
        amount = float(cleaned)
        return f'${amount:,.0f}'
    except (ValueError, TypeError):
        return value_str


def _scrape_html_table(scraper, source):
    """Scrape permits from an HTML table page using configured CSS selectors."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = source.css_selectors or {}

    # Find the table/container
    table_sel = selectors.get('table_selector', 'table')
    table = soup.select_one(table_sel)
    if not table:
        logger.warning(f'[permit] No table found at {source.source_url} with selector: {table_sel}')
        return []

    # Find rows
    row_sel = selectors.get('row_selector', 'tr')
    rows = table.select(row_sel)

    permits = []
    for row in rows[1:]:  # skip header row
        cells = row.select('td')
        if not cells:
            continue

        try:
            permit = {
                'permit_type': _extract_cell(cells, selectors.get('permit_type', '0')),
                'address': _extract_cell(cells, selectors.get('address', '1')),
                'filing_date': _extract_cell(cells, selectors.get('filing_date', '2')),
                'estimated_value': _extract_cell(cells, selectors.get('estimated_value', '')),
                'owner_name': _extract_cell(cells, selectors.get('owner_name', '')),
                'contractor_name': _extract_cell(cells, selectors.get('contractor_name', '')),
                'status': _extract_cell(cells, selectors.get('status', '')),
            }
            if permit['address']:
                permits.append(permit)
        except (IndexError, AttributeError):
            continue

    return permits


def _extract_cell(cells, selector):
    """Extract text from a table cell. Selector can be a column index or CSS selector."""
    if not selector:
        return ''
    try:
        idx = int(selector)
        if 0 <= idx < len(cells):
            return cells[idx].get_text(strip=True)
    except ValueError:
        pass
    return ''


def _scrape_csv(scraper, source):
    """Download and parse a CSV file of permits."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    selectors = source.css_selectors or {}
    reader = csv.DictReader(io.StringIO(resp.text))

    permits = []
    for row in reader:
        permit = {
            'permit_type': row.get(selectors.get('permit_type', 'permit_type'), ''),
            'address': row.get(selectors.get('address', 'address'), ''),
            'filing_date': row.get(selectors.get('filing_date', 'date'), ''),
            'estimated_value': row.get(selectors.get('estimated_value', 'value'), ''),
            'owner_name': row.get(selectors.get('owner_name', 'owner'), ''),
            'contractor_name': row.get(selectors.get('contractor_name', 'contractor'), ''),
            'status': row.get(selectors.get('status', 'status'), ''),
        }
        if permit['address']:
            permits.append(permit)

    return permits


def _scrape_api(scraper, source):
    """Fetch permits from an API endpoint."""
    config = source.api_config or {}
    endpoint = config.get('endpoint', source.source_url)
    params = config.get('params', {})
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
    permits = []
    for item in items:
        permit = {
            'permit_type': item.get(selectors.get('permit_type', 'permit_type'), ''),
            'address': item.get(selectors.get('address', 'address'), ''),
            'filing_date': item.get(selectors.get('filing_date', 'date'), ''),
            'estimated_value': item.get(selectors.get('estimated_value', 'value'), ''),
            'owner_name': item.get(selectors.get('owner_name', 'owner'), ''),
            'contractor_name': item.get(selectors.get('contractor_name', 'contractor'), ''),
            'status': item.get(selectors.get('status', 'status'), ''),
        }
        if permit['address']:
            permits.append(permit)

    return permits


def _scrape_source(scraper, source):
    """Dispatch to the correct scraper based on source.scrape_method."""
    method = source.scrape_method
    if method == 'html_table':
        return _scrape_html_table(scraper, source)
    elif method == 'csv_download':
        return _scrape_csv(scraper, source)
    elif method == 'api':
        return _scrape_api(scraper, source)
    elif method == 'pdf_report':
        logger.info(f'[permit] PDF scraping not yet implemented for {source.name}')
        return []
    else:
        logger.warning(f'[permit] Unknown scrape method: {method}')
        return []


def monitor_permits(source_ids=None, max_age_days=30, dry_run=False):
    """
    Monitor building permit portals for new filings.

    Reads active PermitSource records and scrapes each configured portal.
    Maps permit types to service categories automatically.
    Creates Lead records with platform='permit'.

    Args:
        source_ids: list of PermitSource IDs (default: all active)
        max_age_days: skip permits older than this many days
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = PermitScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    sources = PermitSource.objects.filter(is_active=True)
    if source_ids:
        sources = sources.filter(id__in=source_ids)

    if not sources.exists():
        logger.info('No active PermitSource records configured')
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
        logger.info(f'[permit] Scraping: {source.name} ({source.county}, {source.state})')

        try:
            permits = _scrape_source(scraper, source)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[permit] Error scraping {source.name}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(permits)

        for permit in permits:
            try:
                address = permit.get('address', '')
                permit_type = permit.get('permit_type', '')
                filing_date = _parse_date(permit.get('filing_date', ''))
                value = _format_currency(permit.get('estimated_value', ''))
                owner = permit.get('owner_name', '')

                if not address:
                    continue

                # Skip old permits
                if filing_date and filing_date < cutoff:
                    continue

                # Map permit type to services
                services = _detect_services_from_permit(permit_type)

                # Build lead content
                content_parts = [
                    f'Building Permit Filed: {permit_type}' if permit_type else 'Building Permit Filed',
                    f'Address: {address}',
                ]
                if value:
                    content_parts.append(f'Estimated Value: {value}')
                if owner:
                    content_parts.append(f'Property Owner: {owner}')
                content_parts.append(f'County: {source.county}, {source.state}')
                content_parts.append(f'Services likely needed: {", ".join(services)}')

                content = '\n'.join(content_parts)
                source_url = source.source_url

                if dry_run:
                    logger.info(f'[DRY RUN] Would create permit lead: {address} — {permit_type}')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='permit',
                    source_url=source_url,
                    content=content,
                    author=owner,
                    posted_at=filing_date,
                    raw_data={
                        'permit_type': permit_type,
                        'address': address,
                        'estimated_value': value,
                        'owner_name': owner,
                        'contractor': permit.get('contractor_name', ''),
                        'county': source.county,
                        'state': source.state,
                        'services_mapped': services,
                    },
                    state=source.state or '',
                    region=source.county or '',
                    source_group='public_records',
                    source_type='permits',
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
                logger.error(f'[permit] Error processing permit at {address}: {e}')
                stats['errors'] += 1

        # Update source last_scraped
        source.last_scraped = timezone.now()
        source.save(update_fields=['last_scraped'])

    logger.info(f'Permit monitor complete: {stats}')
    return stats
