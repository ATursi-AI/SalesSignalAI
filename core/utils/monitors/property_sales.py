"""
Property sales/transfer monitor for SalesSignal AI.

Scrapes county recorder websites or uses Apify Zillow scraper to find
recently sold homes. New homeowners need many services within 90 days.

Each county is configured via a PropertyTransferSource database record —
adding a new county is a database entry, not a code change.

Service category mapping for new homeowners:
- Deep cleaning, locksmith, painter, handyman, landscaper
- HVAC maintenance, pest control, gutter cleaning
- Moving company, junk removal

Works nationwide — no hardcoded regions.
"""
import csv
import io
import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from core.models.monitoring import PropertyTransferSource
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Services new homeowners typically need
NEW_HOMEOWNER_SERVICES = [
    'deep cleaning', 'locksmith', 'painter', 'handyman',
    'landscaper', 'hvac maintenance', 'pest control',
    'gutter cleaning', 'moving company', 'junk removal',
    'carpet cleaning', 'window cleaning', 'home inspector',
]


class PropertySaleScraper(BaseScraper):
    MONITOR_NAME = 'property_sale'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 15
    COOLDOWN_MINUTES = 360  # 6 hours
    RESPECT_ROBOTS = True


def _parse_date(date_str):
    """Try to parse common date formats from property records."""
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


def _format_currency(value_str):
    """Extract and format currency from a string."""
    if not value_str:
        return ''
    cleaned = re.sub(r'[^\d.]', '', str(value_str))
    try:
        amount = float(cleaned)
        return f'${amount:,.0f}'
    except (ValueError, TypeError):
        return value_str


def _extract_cell(cells, selector):
    """Extract text from a table cell by index or selector."""
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
    """Scrape property transfers from an HTML table page."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    selectors = source.css_selectors or {}

    table_sel = selectors.get('table_selector', 'table')
    table = soup.select_one(table_sel)
    if not table:
        logger.warning(f'[property_sale] No table found at {source.source_url}')
        return []

    row_sel = selectors.get('row_selector', 'tr')
    rows = table.select(row_sel)

    sales = []
    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if not cells:
            continue

        try:
            sale = {
                'address': _extract_cell(cells, selectors.get('address', '0')),
                'sale_date': _extract_cell(cells, selectors.get('sale_date', '1')),
                'sale_price': _extract_cell(cells, selectors.get('sale_price', '2')),
                'buyer_name': _extract_cell(cells, selectors.get('buyer_name', '')),
                'property_type': _extract_cell(cells, selectors.get('property_type', '')),
                'square_footage': _extract_cell(cells, selectors.get('square_footage', '')),
            }
            if sale['address']:
                sales.append(sale)
        except (IndexError, AttributeError):
            continue

    return sales


def _scrape_csv(scraper, source):
    """Download and parse a CSV of property transfers."""
    resp = scraper.get(source.source_url)
    if not resp or resp.status_code != 200:
        return []

    selectors = source.css_selectors or {}
    reader = csv.DictReader(io.StringIO(resp.text))

    sales = []
    for row in reader:
        sale = {
            'address': row.get(selectors.get('address', 'address'), ''),
            'sale_date': row.get(selectors.get('sale_date', 'date'), ''),
            'sale_price': row.get(selectors.get('sale_price', 'price'), ''),
            'buyer_name': row.get(selectors.get('buyer_name', 'buyer'), ''),
            'property_type': row.get(selectors.get('property_type', 'type'), ''),
            'square_footage': row.get(selectors.get('square_footage', 'sqft'), ''),
        }
        if sale['address']:
            sales.append(sale)

    return sales


def _scrape_api(scraper, source):
    """Fetch property transfers from an API endpoint."""
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
    sales = []
    for item in items:
        sale = {
            'address': item.get(selectors.get('address', 'address'), ''),
            'sale_date': item.get(selectors.get('sale_date', 'date'), ''),
            'sale_price': item.get(selectors.get('sale_price', 'price'), ''),
            'buyer_name': item.get(selectors.get('buyer_name', 'buyer'), ''),
            'property_type': item.get(selectors.get('property_type', 'type'), ''),
            'square_footage': item.get(selectors.get('square_footage', 'sqft'), ''),
        }
        if sale['address']:
            sales.append(sale)

    return sales


def _scrape_apify_zillow(source):
    """Use Apify Zillow scraper to find recently sold homes."""
    try:
        from core.utils.apify_client import ApifyIntegration, ApifyError
    except ImportError:
        logger.error('[property_sale] ApifyIntegration not available')
        return []

    config = source.api_config or {}
    search_area = config.get('search_area', f'{source.county}, {source.state}')

    try:
        apify = ApifyIntegration()
        items = apify.run_actor(
            'petr_cermak/zillow-api-scraper',
            run_input={
                'searchType': 'sold',
                'location': search_area,
                'maxItems': config.get('max_items', 50),
                'daysOnZillow': config.get('days', 30),
            },
            timeout_secs=300,
        )
    except Exception as e:
        logger.error(f'[property_sale] Apify Zillow scraper failed: {e}')
        return []

    sales = []
    for item in items:
        address = item.get('address', '') or item.get('streetAddress', '')
        city = item.get('city', '')
        state = item.get('state', '')
        zipcode = item.get('zipcode', '')

        if city and state:
            full_address = f'{address}, {city}, {state} {zipcode}'.strip()
        else:
            full_address = address

        sale = {
            'address': full_address,
            'sale_date': item.get('dateSold', '') or item.get('datePosted', ''),
            'sale_price': str(item.get('price', '')) or str(item.get('soldPrice', '')),
            'buyer_name': '',  # Zillow doesn't expose buyer names
            'property_type': item.get('homeType', ''),
            'square_footage': str(item.get('livingArea', '')),
        }
        if sale['address']:
            sales.append(sale)

    return sales


def _scrape_source(scraper, source):
    """Dispatch to the correct scraper based on source.scrape_method."""
    method = source.scrape_method
    if method == 'html_table':
        return _scrape_html_table(scraper, source)
    elif method == 'csv_download':
        return _scrape_csv(scraper, source)
    elif method == 'api':
        return _scrape_api(scraper, source)
    elif method == 'apify_zillow':
        return _scrape_apify_zillow(source)
    else:
        logger.warning(f'[property_sale] Unknown scrape method: {method}')
        return []


def monitor_property_sales(source_ids=None, max_age_days=30, dry_run=False):
    """
    Monitor property transfer records for recently sold homes.

    Reads active PropertyTransferSource records and scrapes each portal.
    New homeowners need services — creates Lead records with platform='property_sale'.

    Args:
        source_ids: list of PropertyTransferSource IDs (default: all active)
        max_age_days: skip sales older than this many days
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = PropertySaleScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {'sources_checked': 0, 'items_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0,
                'skipped_reason': reason}

    sources = PropertyTransferSource.objects.filter(is_active=True)
    if source_ids:
        sources = sources.filter(id__in=source_ids)

    if not sources.exists():
        logger.info('No active PropertyTransferSource records configured')
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
        logger.info(f'[property_sale] Scraping: {source.name} ({source.county}, {source.state})')

        try:
            sales = _scrape_source(scraper, source)
        except RateLimitHit:
            break
        except Exception as e:
            logger.error(f'[property_sale] Error scraping {source.name}: {e}')
            stats['errors'] += 1
            continue

        stats['items_scraped'] += len(sales)

        for sale in sales:
            try:
                address = sale.get('address', '')
                sale_date = _parse_date(sale.get('sale_date', ''))
                sale_price = _format_currency(sale.get('sale_price', ''))
                buyer = sale.get('buyer_name', '')
                prop_type = sale.get('property_type', '')

                if not address:
                    continue

                # Skip old sales
                if sale_date and sale_date < cutoff:
                    continue

                # Skip non-residential if property type is available
                if prop_type:
                    prop_lower = prop_type.lower()
                    if any(t in prop_lower for t in ['commercial', 'industrial', 'land', 'vacant lot']):
                        continue

                # Build lead content
                content_parts = [
                    f'Property Sold: {address}',
                ]
                if sale_price:
                    content_parts.append(f'Sale Price: {sale_price}')
                if sale_date:
                    days_ago = (timezone.now() - sale_date).days
                    content_parts.append(f'Closed: {days_ago} days ago')
                if buyer:
                    content_parts.append(f'Buyer: {buyer}')
                if prop_type:
                    content_parts.append(f'Property Type: {prop_type}')
                content_parts.append(f'County: {source.county}, {source.state}')
                content_parts.append(
                    f'New homeowner likely needs: {", ".join(NEW_HOMEOWNER_SERVICES[:6])}'
                )

                content = '\n'.join(content_parts)
                source_url = source.source_url

                if dry_run:
                    logger.info(f'[DRY RUN] Would create property sale lead: {address}')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='property_sale',
                    source_url=source_url,
                    content=content,
                    author=buyer,
                    posted_at=sale_date,
                    raw_data={
                        'address': address,
                        'sale_price': sale_price,
                        'buyer_name': buyer,
                        'property_type': prop_type,
                        'square_footage': sale.get('square_footage', ''),
                        'county': source.county,
                        'state': source.state,
                    },
                    state=source.state or '',
                    region=source.county or '',
                    source_group='public_records',
                    source_type='property_sales',
                    contact_name=buyer,
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
                logger.error(f'[property_sale] Error processing sale at {address}: {e}')
                stats['errors'] += 1

        # Update source last_scraped
        source.last_scraped = timezone.now()
        source.save(update_fields=['last_scraped'])

    logger.info(f'Property sales monitor complete: {stats}')
    return stats
