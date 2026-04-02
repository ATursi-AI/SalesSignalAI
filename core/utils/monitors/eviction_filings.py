"""
Commercial eviction filing monitor for SalesSignal AI.

Scrapes county court record portals for commercial eviction filings.
When a commercial tenant is evicted, the property owner needs the space
cleaned, repaired, and prepared for the next tenant.

IMPORTANT: Only monitors COMMERCIAL evictions. Residential evictions are
excluded due to ethical concerns around targeting vulnerable populations.

Each county is configured via a CourtRecordSource database record —
adding a new county is a database entry, not a code change.

Nationwide — no hardcoded regions.
"""
import csv
import io
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import CourtRecordSource
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Services needed after a commercial eviction
EVICTION_SERVICES = [
    'commercial cleaning', 'junk removal', 'locksmith',
    'painter', 'general contractor', 'carpet cleaning',
    'handyman', 'drywall repair', 'window cleaning',
]

# Terms that indicate COMMERCIAL property (include these)
COMMERCIAL_INDICATORS = [
    'commercial', 'office', 'retail', 'store', 'shop', 'restaurant',
    'warehouse', 'industrial', 'business', 'corp', 'llc', 'inc',
    'suite', 'unit', 'plaza', 'center', 'mall', 'complex',
    'professional', 'medical', 'dental', 'clinic',
]

# Terms that indicate RESIDENTIAL property (exclude these)
RESIDENTIAL_INDICATORS = [
    'apartment', 'apt', 'condo', 'townhouse', 'duplex', 'triplex',
    'residential', 'single family', 'mobile home', 'trailer',
    'housing authority', 'section 8',
]


class EvictionFilingScraper(BaseScraper):
    MONITOR_NAME = 'eviction_filing'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _is_commercial(text):
    """
    Determine if an eviction filing is commercial (not residential).
    Returns True only if there's evidence it's commercial.
    """
    if not text:
        return False

    text_lower = text.lower()

    # Exclude if residential indicators present
    for term in RESIDENTIAL_INDICATORS:
        if term in text_lower:
            return False

    # Include if commercial indicators present
    for term in COMMERCIAL_INDICATORS:
        if term in text_lower:
            return True

    # Default: uncertain — exclude to be safe (ethical default)
    return False


def _parse_date(date_str):
    """Parse common date formats from court record portals."""
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
    """Scrape eviction filings from an HTML table page."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = source.css_selectors or {}

    table_sel = selectors.get('table_selector', 'table')
    table = soup.select_one(table_sel)
    if not table:
        logger.warning(f'[eviction_filing] No table found at {source.source_url}')
        return []

    row_sel = selectors.get('row_selector', 'tr')
    rows = table.select(row_sel)

    filings = []
    for row in rows[1:]:
        cells = row.select('td')
        if not cells:
            continue

        try:
            filing = {
                'address': _extract_cell(cells, selectors.get('address', '0')),
                'filing_date': _extract_cell(cells, selectors.get('filing_date', '1')),
                'case_number': _extract_cell(cells, selectors.get('case_number', '2')),
                'plaintiff': _extract_cell(cells, selectors.get('plaintiff', '3')),
                'property_type': _extract_cell(cells, selectors.get('property_type', '')),
                'status': _extract_cell(cells, selectors.get('status', '')),
            }
            if filing['address']:
                filings.append(filing)
        except (IndexError, AttributeError):
            continue

    return filings


def _scrape_api(scraper, source):
    """Fetch eviction filings from an API endpoint."""
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
    filings = []
    for item in items:
        filing = {
            'address': item.get(selectors.get('address', 'address'), ''),
            'filing_date': item.get(selectors.get('filing_date', 'filing_date'), ''),
            'case_number': item.get(selectors.get('case_number', 'case_number'), ''),
            'plaintiff': item.get(selectors.get('plaintiff', 'plaintiff'), ''),
            'property_type': item.get(selectors.get('property_type', 'property_type'), ''),
            'status': item.get(selectors.get('status', 'status'), ''),
        }
        if filing['address']:
            filings.append(filing)

    return filings


def _scrape_csv(scraper, source):
    """Download and parse a CSV of eviction filings."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    selectors = source.css_selectors or {}
    reader = csv.DictReader(io.StringIO(resp.text))

    filings = []
    for row in reader:
        filing = {
            'address': row.get(selectors.get('address', 'address'), ''),
            'filing_date': row.get(selectors.get('filing_date', 'filing_date'), ''),
            'case_number': row.get(selectors.get('case_number', 'case_number'), ''),
            'plaintiff': row.get(selectors.get('plaintiff', 'plaintiff'), ''),
            'property_type': row.get(selectors.get('property_type', 'property_type'), ''),
            'status': row.get(selectors.get('status', 'status'), ''),
        }
        if filing['address']:
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
        logger.warning(f'[eviction_filing] Unknown scrape method: {method}')
        return []


def monitor_evictions(source_ids=None, max_age_days=30, dry_run=False):
    """
    Monitor county court records for commercial eviction filings.

    ONLY processes commercial evictions — residential evictions are
    excluded for ethical reasons.

    Reads active CourtRecordSource records and scrapes each portal.
    Creates Lead records with platform='eviction_filing'.

    Args:
        source_ids: list of CourtRecordSource IDs (default: all active)
        max_age_days: skip filings older than this many days
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = EvictionFilingScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    sources = CourtRecordSource.objects.filter(is_active=True)
    if source_ids:
        sources = sources.filter(id__in=source_ids)

    if not sources.exists():
        logger.info('No active CourtRecordSource records configured')
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0}

    stats = {
        'sources_checked': 0,
        'items_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
        'residential_skipped': 0,
    }

    cutoff = timezone.now() - timedelta(days=max_age_days)
    sources = scraper.shuffle(list(sources))

    for source in sources:
        if scraper.is_stopped:
            break

        stats['sources_checked'] += 1
        logger.info(f'[eviction_filing] Scraping: {source.county}, {source.state}')

        try:
            filings = _scrape_source(scraper, source)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[eviction_filing] Error scraping {source.county}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(filings)

        for filing in filings:
            try:
                address = filing.get('address', '')
                filing_date = _parse_date(filing.get('filing_date', ''))
                case_number = filing.get('case_number', '')
                plaintiff = filing.get('plaintiff', '')
                property_type = filing.get('property_type', '')

                if not address:
                    continue

                # Skip old filings
                if filing_date and filing_date < cutoff:
                    continue

                # CRITICAL: Only process commercial evictions
                combined_text = f"{address} {property_type} {plaintiff}"
                if not _is_commercial(combined_text):
                    stats['residential_skipped'] += 1
                    continue

                # Build lead content
                content_parts = [
                    f'COMMERCIAL EVICTION FILED',
                    f'Address: {address}',
                ]
                if case_number:
                    content_parts.append(f'Case #: {case_number}')
                if plaintiff:
                    content_parts.append(f'Plaintiff: {plaintiff}')
                if filing_date:
                    days_ago = (timezone.now() - filing_date).days
                    content_parts.append(f'Filed: {days_ago} days ago')
                if property_type:
                    content_parts.append(f'Property Type: {property_type}')
                content_parts.append(f'County: {source.county}, {source.state}')
                content_parts.append(
                    f'Services needed: {", ".join(EVICTION_SERVICES[:6])}'
                )
                content_parts.append(
                    'Property owner likely needs cleaning, repair, and locksmith services.'
                )

                content = '\n'.join(content_parts)

                if dry_run:
                    logger.info(f'[DRY RUN] Would create eviction lead: {address}')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='eviction_filing',
                    source_url=source.source_url,
                    content=content,
                    author='',
                    posted_at=filing_date,
                    raw_data={
                        'address': address,
                        'case_number': case_number,
                        'plaintiff': plaintiff,
                        'property_type': property_type,
                        'county': source.county,
                        'state': source.state,
                        'services_needed': EVICTION_SERVICES,
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
                logger.error(f'[eviction_filing] Error processing filing at {address}: {e}')
                stats['errors'] += 1

        # Update source last_scraped
        source.last_scraped = timezone.now()
        source.save(update_fields=['last_scraped'])

    logger.info(f'Eviction filing monitor complete: {stats}')
    return stats
