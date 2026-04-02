"""
NY contractor license expiration monitor for SalesSignal AI.

Monitors NYC DOB and NY DOS databases for expired, suspended, or revoked
contractor licenses. When a contractor's license expires or is suspended,
their customers need a new provider — this is an ORPHANED CUSTOMER signal
and direct competitive intelligence.

Data sources:
  1. NYC Open Data — DOB licensed contractor database
     Dataset: w9ak-ipjd (DOB NOW: Active/Expired Licenses) or similar
  2. NY DOS professional license search (HTML scrape)
     appext20.dos.ny.gov

Lead category: detected_category='ORPHANED_CUSTOMER' with the specific
trade stored in raw_data.
"""
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Data source configuration
# -------------------------------------------------------------------
NYC_OPEN_DATA_BASE = 'https://data.cityofnewyork.us/resource'
DOB_LICENSE_DATASET_ID = 'w9ak-ipjd'  # DOB licensed contractors

# NY Department of State professional license search
NY_DOS_SEARCH_URL = 'https://appext20.dos.ny.gov/lcns_public/lic_name_search'

# License type -> service category mapping
LICENSE_SERVICE_MAP = {
    'general contractor': 'General Contractor',
    'general': 'General Contractor',
    'gc': 'General Contractor',
    'plumbing': 'Plumber',
    'plumber': 'Plumber',
    'master plumber': 'Plumber',
    'electrical': 'Electrician',
    'electrician': 'Electrician',
    'master electrician': 'Electrician',
    'special electrician': 'Electrician',
    'hvac': 'HVAC',
    'heating': 'HVAC',
    'air conditioning': 'HVAC',
    'refrigeration': 'HVAC',
    'roofing': 'Roofer',
    'roofer': 'Roofer',
    'painting': 'Painter',
    'painter': 'Painter',
    'landscaping': 'Landscaper',
    'landscaper': 'Landscaper',
    'pest control': 'Pest Control',
    'exterminator': 'Pest Control',
    'tree': 'Tree Service',
    'tree service': 'Tree Service',
    'fencing': 'Fencing',
    'fence': 'Fencing',
    'concrete': 'Concrete/Masonry',
    'masonry': 'Concrete/Masonry',
    'flooring': 'Flooring',
    'tile': 'Flooring',
    'demolition': 'Demolition',
    'insulation': 'Insulation',
    'fire protection': 'Fire Protection',
    'fire suppression': 'Fire Protection',
    'fire sprinkler': 'Fire Protection',
    'elevator': 'Elevator',
    'boiler': 'Boiler',
    'oil burner': 'HVAC',
    'rigger': 'Rigger/Crane',
    'crane': 'Rigger/Crane',
    'scaffolding': 'Scaffolding',
    'sign': 'Signage',
    'welding': 'Welding',
    'asbestos': 'Asbestos Abatement',
    'lead paint': 'Lead Paint Abatement',
    'home improvement': 'Home Improvement',
    'home inspector': 'Home Inspection',
    'real estate': 'Real Estate',
    'apprais': 'Appraiser',
    'engineer': 'Engineer',
    'architect': 'Architect',
    'septic': 'Septic Service',
    'well': 'Well Service',
    'solar': 'Solar Installation',
    'pool': 'Pool Service',
}

# Statuses indicating the license is no longer valid
EXPIRED_STATUSES = {
    'expired', 'suspended', 'revoked', 'cancelled', 'canceled',
    'inactive', 'lapsed', 'delinquent', 'not renewed',
    'surrender', 'surrendered', 'void', 'denied',
}

# How recently the license must have expired to be a useful lead
# (very old expirations are stale)
MAX_EXPIRATION_AGE_DAYS = 180


class NYLicenseExpirationScraper(BaseScraper):
    MONITOR_NAME = 'ny_license_expiration'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 40
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 720  # 12 hours — licenses don't change rapidly
    RESPECT_ROBOTS = True


def _parse_date(date_str):
    """Parse common date formats from DOB/DOS data."""
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


def _map_license_to_service(license_type):
    """Map a license type string to a service category name."""
    if not license_type:
        return 'General Contractor'

    lt_lower = license_type.lower()
    for key, service in LICENSE_SERVICE_MAP.items():
        if key in lt_lower:
            return service

    return 'General Contractor'


def _is_expired_status(status_str):
    """Check if a license status indicates it is no longer valid."""
    if not status_str:
        return False
    return status_str.strip().lower() in EXPIRED_STATUSES


def _build_address(record):
    """Build address from Open Data record fields."""
    parts = []

    for addr_field in ('business_address', 'address', 'street_address'):
        val = record.get(addr_field, '').strip()
        if val:
            parts.append(val)
            break

    city = record.get('city', '') or record.get('business_city', '')
    if city:
        parts.append(city.strip())

    state = record.get('state', '') or record.get('business_state', '') or 'NY'
    parts.append(state.strip())

    zipcode = record.get('zip', '') or record.get('zip_code', '') or record.get('business_zip', '')
    if zipcode:
        parts.append(str(zipcode).strip())

    return ', '.join(parts) if parts else ''


def _fetch_dob_expired_licenses(scraper, days):
    """
    Fetch expired/suspended contractor licenses from NYC Open Data (DOB).
    Returns list of license record dicts.
    """
    cutoff_date = (timezone.now() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')
    max_cutoff = (timezone.now() - timedelta(days=MAX_EXPIRATION_AGE_DAYS)).strftime('%Y-%m-%dT%H:%M:%S')

    # Query for recently expired licenses
    # Look for licenses that expired within the window
    url = (
        f'{NYC_OPEN_DATA_BASE}/{DOB_LICENSE_DATASET_ID}.json'
        f'?$where=expiration_date > \'{max_cutoff}\' AND expiration_date < \'{cutoff_date}\''
        f'&$limit=1000'
        f'&$order=expiration_date DESC'
    )

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_license_expiration] Error fetching DOB Open Data: {e}')
        return []

    if not resp or resp.status_code != 200:
        # Try alternative query without date filter (some datasets use different field names)
        alt_url = (
            f'{NYC_OPEN_DATA_BASE}/{DOB_LICENSE_DATASET_ID}.json'
            f'?$where=license_expiration_date > \'{max_cutoff}\''
            f' AND license_expiration_date < \'{cutoff_date}\''
            f'&$limit=1000'
            f'&$order=license_expiration_date DESC'
        )
        try:
            resp = scraper.get(alt_url)
        except RateLimitHit:
            raise
        except Exception as e:
            logger.error(f'[ny_license_expiration] Alt query also failed: {e}')
            return []

        if not resp or resp.status_code != 200:
            logger.warning(
                f'[ny_license_expiration] DOB Open Data returned '
                f'{resp.status_code if resp else "None"}'
            )
            return []

    try:
        data = resp.json()
    except Exception:
        logger.error('[ny_license_expiration] Failed to parse DOB Open Data JSON')
        return []

    if not isinstance(data, list):
        return []

    licenses = []
    for record in data:
        # Determine expiration date field
        exp_date = (
            record.get('expiration_date', '')
            or record.get('license_expiration_date', '')
            or record.get('exp_date', '')
        )

        # Determine status
        status = (
            record.get('status', '')
            or record.get('license_status', '')
        ).strip()

        # Determine license type
        license_type = (
            record.get('license_type', '')
            or record.get('type', '')
            or record.get('license_category', '')
        ).strip()

        contractor_name = (
            record.get('business_name', '')
            or record.get('licensee_name', '')
            or record.get('name', '')
        ).strip()

        license_number = (
            record.get('license_number', '')
            or record.get('license_nbr', '')
            or record.get('license_no', '')
        ).strip()

        if not contractor_name:
            continue

        licenses.append({
            'contractor_name': contractor_name,
            'license_number': license_number,
            'license_type': license_type,
            'expiration_date': exp_date,
            'status': status,
            'address': _build_address(record),
            'phone': record.get('phone', '') or record.get('business_phone', ''),
            'source': 'nyc_dob_open_data',
        })

    logger.info(f'[ny_license_expiration] Fetched {len(licenses)} license records from DOB Open Data')
    return licenses


def _scrape_ny_dos_licenses(scraper, days):
    """
    Scrape NY Department of State professional license search for
    expired/suspended licenses.
    Returns list of license record dicts.
    """
    try:
        resp = scraper.get(NY_DOS_SEARCH_URL)
    except RateLimitHit:
        raise
    except Exception as e:
        logger.error(f'[ny_license_expiration] Error fetching NY DOS: {e}')
        return []

    if not resp or resp.status_code != 200:
        logger.warning(
            f'[ny_license_expiration] NY DOS returned '
            f'{resp.status_code if resp else "None"}'
        )
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Look for search form to submit, or parse results if already showing
    table = soup.select_one('table.results') or soup.select_one('table')
    if not table:
        logger.warning('[ny_license_expiration] No results table found on NY DOS page')
        return []

    rows = table.select('tr')
    licenses = []

    for row in rows[1:]:  # skip header
        cells = row.select('td')
        if len(cells) < 4:
            continue

        try:
            contractor_name = cells[0].get_text(strip=True)
            license_number = cells[1].get_text(strip=True) if len(cells) > 1 else ''
            license_type = cells[2].get_text(strip=True) if len(cells) > 2 else ''
            status = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            expiration_date = cells[4].get_text(strip=True) if len(cells) > 4 else ''
            address = cells[5].get_text(strip=True) if len(cells) > 5 else ''

            if not contractor_name:
                continue

            licenses.append({
                'contractor_name': contractor_name,
                'license_number': license_number,
                'license_type': license_type,
                'expiration_date': expiration_date,
                'status': status,
                'address': address,
                'phone': '',
                'source': 'ny_dos',
            })
        except (IndexError, AttributeError):
            continue

    logger.info(f'[ny_license_expiration] Scraped {len(licenses)} records from NY DOS')
    return licenses


def monitor_ny_license_expirations(days=30, dry_run=False, remote=False):
    """
    Monitor NY contractor license databases for expired/suspended licenses.

    When a contractor's license expires or is suspended, their customers
    become orphaned — they need a new provider. This is direct competitive
    intelligence for businesses in the same trade.

    Args:
        days: How recently the license must have expired to be relevant.
              Searches for licenses that expired within the last N days.
              (default: 30)
        dry_run: If True, log matches without creating Lead records
        remote: If True, skip HTML scraping and use only API sources

    Returns:
        dict with counts: sources_checked, items_scraped, created,
                         duplicates, assigned, errors
    """
    scraper = NYLicenseExpirationScraper()

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

    all_licenses = []
    now = timezone.now()
    cutoff = now - timedelta(days=days)
    max_cutoff = now - timedelta(days=MAX_EXPIRATION_AGE_DAYS)

    # Source 1: NYC DOB Open Data (primary)
    stats['sources_checked'] += 1
    try:
        dob_licenses = _fetch_dob_expired_licenses(scraper, days)
        all_licenses.extend(dob_licenses)
    except RateLimitHit:
        logger.warning('[ny_license_expiration] Rate limited on DOB Open Data')
    except Exception as e:
        logger.error(f'[ny_license_expiration] Error with DOB Open Data: {e}')
        stats['errors'] += 1

    # Source 2: NY DOS professional license search (HTML scrape)
    if not remote and not scraper.is_stopped:
        stats['sources_checked'] += 1
        try:
            dos_licenses = _scrape_ny_dos_licenses(scraper, days)
            all_licenses.extend(dos_licenses)
        except RateLimitHit:
            logger.warning('[ny_license_expiration] Rate limited on NY DOS')
        except Exception as e:
            logger.error(f'[ny_license_expiration] Error with NY DOS: {e}')
            stats['errors'] += 1

    stats['items_scraped'] = len(all_licenses)

    # Process each license record
    seen = set()
    for record in all_licenses:
        try:
            contractor_name = record.get('contractor_name', '').strip()
            license_number = record.get('license_number', '').strip()
            license_type = record.get('license_type', '').strip()
            exp_date_str = record.get('expiration_date', '')
            status = record.get('status', '').strip()
            address = record.get('address', '').strip()
            phone = record.get('phone', '').strip()

            if not contractor_name:
                continue

            # Dedup by contractor name + license number
            dedup_key = f'{contractor_name.lower()}|{license_number.lower()}'
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Parse expiration date
            exp_date = _parse_date(exp_date_str)

            # Determine if license is expired
            is_expired = _is_expired_status(status)
            if not is_expired and exp_date:
                is_expired = exp_date < now

            if not is_expired:
                continue

            # Skip very old expirations
            if exp_date and exp_date < max_cutoff:
                continue

            # Skip if not within the requested window
            if exp_date and exp_date < cutoff:
                # Still include if status explicitly says suspended/revoked
                # (these are more actionable regardless of date)
                if status.lower() not in ('suspended', 'revoked'):
                    continue

            # Map license type to service category
            service = _map_license_to_service(license_type)

            # Calculate days since expiration
            days_expired = (now - exp_date).days if exp_date else 0

            # Determine urgency based on recency and status
            if status.lower() in ('suspended', 'revoked'):
                urgency_note = (
                    f'LICENSE {status.upper()} — customers of {contractor_name} '
                    f'need a new {service} provider immediately'
                )
            elif days_expired <= 30:
                urgency_note = (
                    f'License expired {days_expired} days ago — '
                    f'customers likely still looking for a replacement'
                )
            else:
                urgency_note = (
                    f'License expired {days_expired} days ago — '
                    f'orphaned customers may still need a new provider'
                )

            # Build lead content
            content_parts = [
                f'ORPHANED CUSTOMERS: {contractor_name} license {status.lower() or "expired"}',
            ]
            if license_number:
                content_parts.append(f'License #: {license_number}')
            content_parts.append(f'License Type: {license_type or "Unknown"}')
            content_parts.append(f'Trade: {service}')
            if status:
                content_parts.append(f'Status: {status}')
            if exp_date:
                content_parts.append(f'Expired: {days_expired} days ago')
            if address:
                content_parts.append(f'Business Address: {address}')
            if phone:
                content_parts.append(f'Phone: {phone}')

            content_parts.append(f'\n{urgency_note}')
            content_parts.append(
                f'Customers of this {service.lower()} provider may be looking '
                f'for a replacement. Contact their recent customers to offer services.'
            )

            content = '\n'.join(content_parts)

            source_url = (
                f'{NYC_OPEN_DATA_BASE}/{DOB_LICENSE_DATASET_ID}'
                if record.get('source') == 'nyc_dob_open_data'
                else NY_DOS_SEARCH_URL
            )

            if dry_run:
                logger.info(
                    f'[DRY RUN] Would create license expiration lead: '
                    f'{contractor_name} ({license_type}) — {status or "expired"}'
                )
                stats['created'] += 1
                continue

            lead, created, num_assigned = process_lead(
                platform='public_records',
                source_url=source_url,
                content=content,
                author='',
                posted_at=exp_date,
                raw_data={
                    'source_type': 'license_expiration',
                    'contractor_name': contractor_name,
                    'license_number': license_number,
                    'license_type': license_type,
                    'status': status,
                    'expiration_date': exp_date_str,
                    'days_expired': days_expired,
                    'address': address,
                    'phone': phone,
                    'trade': service,
                    'detected_category': 'ORPHANED_CUSTOMER',
                    'data_source': record.get('source', ''),
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
            logger.error(
                f'[ny_license_expiration] Error processing license for '
                f'{record.get("contractor_name", "unknown")}: {e}'
            )
            stats['errors'] += 1

    logger.info(f'NY license expiration monitor complete: {stats}')
    return stats
