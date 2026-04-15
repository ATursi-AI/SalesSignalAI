"""
Contractor license expiration monitor for SalesSignal AI.

Scrapes state licensing board databases for expired/suspended contractor licenses.
When a competitor's license expires or gets suspended, their customers need
a new provider — this is direct competitive intelligence.

Each state/license type is configured via a LicensingBoardSource database record —
adding a new state or license type is a database entry, not a code change.

Nationwide — no hardcoded regions.
"""
import csv
import io
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import LicensingBoardSource
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# License type → service category mapping
LICENSE_SERVICE_MAP = {
    'plumbing': 'plumber',
    'plumber': 'plumber',
    'electrical': 'electrician',
    'electrician': 'electrician',
    'general contractor': 'general contractor',
    'general': 'general contractor',
    'hvac': 'HVAC',
    'heating': 'HVAC',
    'air conditioning': 'HVAC',
    'roofing': 'roofing',
    'roofer': 'roofing',
    'painting': 'painter',
    'painter': 'painter',
    'landscaping': 'landscaping',
    'landscaper': 'landscaping',
    'pest control': 'pest control',
    'exterminator': 'pest control',
    'tree': 'tree service',
    'tree service': 'tree service',
    'fencing': 'fencing',
    'concrete': 'concrete',
    'masonry': 'masonry',
    'flooring': 'flooring',
    'carpet': 'flooring',
    'tile': 'flooring',
    'demolition': 'demolition',
    'insulation': 'insulation',
    'fire protection': 'fire safety',
    'fire sprinkler': 'fire safety',
    'elevator': 'elevator repair',
    'septic': 'septic service',
    'well': 'well service',
    'solar': 'solar installation',
    'pool': 'pool service',
    'asbestos': 'asbestos removal',
    'lead paint': 'lead paint removal',
}

# Statuses that indicate the license is no longer valid
EXPIRED_STATUSES = {
    'expired', 'suspended', 'revoked', 'cancelled',
    'inactive', 'lapsed', 'delinquent', 'not renewed',
}


class LicenseExpirationScraper(BaseScraper):
    MONITOR_NAME = 'license_expiry'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 720  # 12 hours — licenses don't change rapidly
    RESPECT_ROBOTS = True


def _map_license_to_service(license_type):
    """Map a license type string to a service category name."""
    if not license_type:
        return 'general contractor'

    lt_lower = license_type.lower()
    for key, service in LICENSE_SERVICE_MAP.items():
        if key in lt_lower:
            return service

    return 'general contractor'


def _parse_date(date_str):
    """Parse common date formats from licensing board portals."""
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


def _is_expired_status(status_str):
    """Check if license status indicates it's no longer valid."""
    if not status_str:
        return False
    return status_str.strip().lower() in EXPIRED_STATUSES


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
    """Scrape license records from an HTML table page."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = source.css_selectors or {}

    table_sel = selectors.get('table_selector', 'table')
    table = soup.select_one(table_sel)
    if not table:
        logger.warning(f'[license_expiry] No table found at {source.source_url}')
        return []

    row_sel = selectors.get('row_selector', 'tr')
    rows = table.select(row_sel)

    licenses = []
    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if not cells:
            continue

        try:
            record = {
                'contractor_name': _extract_cell(cells, selectors.get('contractor_name', '0')),
                'license_number': _extract_cell(cells, selectors.get('license_number', '1')),
                'license_type': _extract_cell(cells, selectors.get('license_type', '2')),
                'expiration_date': _extract_cell(cells, selectors.get('expiration_date', '3')),
                'status': _extract_cell(cells, selectors.get('status', '4')),
                'business_address': _extract_cell(cells, selectors.get('business_address', '')),
            }
            if record['contractor_name']:
                licenses.append(record)
        except (IndexError, AttributeError):
            continue

    return licenses


def _scrape_api(scraper, source):
    """Fetch license records from an API endpoint."""
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
    licenses = []
    for item in items:
        record = {
            'contractor_name': item.get(selectors.get('contractor_name', 'name'), ''),
            'license_number': item.get(selectors.get('license_number', 'license_number'), ''),
            'license_type': item.get(selectors.get('license_type', 'type'), ''),
            'expiration_date': item.get(selectors.get('expiration_date', 'expiration_date'), ''),
            'status': item.get(selectors.get('status', 'status'), ''),
            'business_address': item.get(selectors.get('business_address', 'address'), ''),
        }
        if record['contractor_name']:
            licenses.append(record)

    return licenses


def _scrape_csv(scraper, source):
    """Download and parse a CSV of license records."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    selectors = source.css_selectors or {}
    reader = csv.DictReader(io.StringIO(resp.text))

    licenses = []
    for row in reader:
        record = {
            'contractor_name': row.get(selectors.get('contractor_name', 'name'), ''),
            'license_number': row.get(selectors.get('license_number', 'license_number'), ''),
            'license_type': row.get(selectors.get('license_type', 'type'), ''),
            'expiration_date': row.get(selectors.get('expiration_date', 'expiration_date'), ''),
            'status': row.get(selectors.get('status', 'status'), ''),
            'business_address': row.get(selectors.get('business_address', 'address'), ''),
        }
        if record['contractor_name']:
            licenses.append(record)

    return licenses


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
        logger.warning(f'[license_expiry] Unknown scrape method: {method}')
        return []


def monitor_license_expirations(source_ids=None, max_age_days=90, dry_run=False):
    """
    Monitor state licensing board databases for expired/suspended licenses.

    Reads active LicensingBoardSource records and scrapes each portal.
    Focuses on expired, suspended, or revoked licenses.
    Creates Lead records with platform='license_expiry'.

    Args:
        source_ids: list of LicensingBoardSource IDs (default: all active)
        max_age_days: skip expirations older than this many days (default: 90)
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = LicenseExpirationScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    sources = LicensingBoardSource.objects.filter(is_active=True)
    if source_ids:
        sources = sources.filter(id__in=source_ids)

    if not sources.exists():
        logger.info('No active LicensingBoardSource records configured')
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
        logger.info(f'[license_expiry] Scraping: {source.state} — {source.license_type}')

        try:
            licenses = _scrape_source(scraper, source)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[license_expiry] Error scraping {source.state} {source.license_type}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(licenses)

        for record in licenses:
            try:
                name = record.get('contractor_name', '')
                license_num = record.get('license_number', '')
                license_type = record.get('license_type', '') or source.license_type
                exp_date = _parse_date(record.get('expiration_date', ''))
                status = record.get('status', '')
                address = record.get('business_address', '')

                if not name:
                    continue

                # Only process expired/suspended licenses
                is_expired = _is_expired_status(status)
                if not is_expired and exp_date:
                    # Also catch licenses expired by date even if status not marked
                    is_expired = exp_date < timezone.now()

                if not is_expired:
                    continue

                # Skip very old expirations
                if exp_date and exp_date < cutoff:
                    continue

                # Map license to service
                service = _map_license_to_service(license_type)

                # Calculate days since expiration
                days_expired = (timezone.now() - exp_date).days if exp_date else 0

                # Build lead content
                content_parts = [
                    f'COMPETITOR LICENSE EXPIRED: {name}',
                    f'License: #{license_num}' if license_num else '',
                    f'Type: {license_type}',
                    f'Status: {status}' if status else '',
                ]
                if exp_date:
                    content_parts.append(f'Expired: {days_expired} days ago')
                if address:
                    content_parts.append(f'Business Address: {address}')
                content_parts.append(f'State: {source.state}')
                content_parts.append(f'Service Category: {service}')
                content_parts.append(
                    f'Their customers may need a new {service} provider.'
                )

                content = '\n'.join(p for p in content_parts if p)

                if dry_run:
                    logger.info(f'[DRY RUN] Would create license lead: {name} ({license_type})')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='license_expiry',
                    source_url=source.source_url,
                    content=content,
                    author='',
                    posted_at=exp_date,
                    raw_data={
                        'contractor_name': name,
                        'license_number': license_num,
                        'license_type': license_type,
                        'status': status,
                        'days_expired': days_expired,
                        'business_address': address,
                        'state': source.state,
                        'service_category': service,
                    },
                    contact_name=name,
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
                logger.error(f'[license_expiry] Error processing license {name}: {e}')
                stats['errors'] += 1

        # Update source last_scraped
        source.last_scraped = timezone.now()
        source.save(update_fields=['last_scraped'])

    logger.info(f'License expiration monitor complete: {stats}')
    return stats
