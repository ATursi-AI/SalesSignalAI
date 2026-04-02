"""
Google Reviews negative sentiment monitor for SalesSignal AI.
Searches for local businesses by category + geography and scrapes
their Google review data. Flags reviews of 3 stars or less as leads.

Primary engine: Apify Google Maps Scraper (handles JS rendering).
Fallback: BeautifulSoup with BaseScraper anti-detection (limited by
Google's JS-only rendering — may return 0 results).

Strategy:
  1. Try Apify's Google Maps actor to search for businesses with reviews.
  2. If Apify is not configured, fall back to raw Google Search scraping
     via BeautifulSoup (JSON-LD, DOM elements, local pack cards).
  3. Flag reviews <= 3 stars as leads with confidence/urgency mapping.
  4. Detect "Permanently closed" / "Temporarily closed" as orphaned
     customer leads with HIGH confidence.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlencode

from bs4 import BeautifulSoup
from django.utils import timezone

from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)


class GoogleMapsScraper(BaseScraper):
    MONITOR_NAME = 'google_reviews_scraper'
    DELAY_MIN = 6.0
    DELAY_MAX = 14.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 25
    COOLDOWN_MINUTES = 120
    RESPECT_ROBOTS = False
    TIMEOUT = 20


# -----------------------------------------------------------------
# Category -> search terms for Google
# -----------------------------------------------------------------
CATEGORY_SEARCH_TERMS = {
    'plumber': ['plumber', 'plumbing company'],
    'electrician': ['electrician', 'electrical contractor'],
    'hvac': ['hvac company', 'heating and cooling'],
    'roofer': ['roofer', 'roofing company'],
    'painter': ['house painter', 'painting contractor'],
    'landscaper': ['landscaper', 'landscaping company'],
    'contractor': ['general contractor', 'home remodeling'],
    'pest-control': ['pest control', 'exterminator'],
    'locksmith': ['locksmith'],
    'moving': ['moving company', 'movers'],
    'cleaning': ['house cleaning service', 'maid service'],
    'tree-service': ['tree service', 'tree removal'],
    'fencing': ['fence company', 'fence installer'],
    'flooring': ['flooring company', 'floor installation'],
    'handyman': ['handyman service'],
    'dentist': ['dentist', 'dental office'],
    'lawyer': ['lawyer', 'attorney'],
    'accountant': ['accountant', 'cpa'],
    'auto-repair': ['auto repair', 'mechanic shop'],
    'insurance': ['insurance agent', 'insurance agency'],
    'real-estate': ['real estate agent', 'realtor'],
    'veterinarian': ['veterinarian', 'vet clinic'],
    'therapist': ['therapist', 'counselor'],
}


# =================================================================
# Apify-powered scraping (primary)
# =================================================================

def _apify_available():
    """Check if Apify is configured and importable."""
    try:
        from core.utils.apify_client import ApifyIntegration, ApifyError
        apify = ApifyIntegration()
        return apify
    except Exception:
        return None


def _search_businesses_apify(apify, category_term, city, max_reviews):
    """
    Use Apify Google Maps actor to find businesses and their reviews.
    Returns list of normalized business dicts.
    """
    from core.utils.apify_client import ApifyError

    query = f'{category_term} in {city}'
    run_input = {
        'searchStringsArray': [query],
        'maxCrawledPlacesPerSearch': 20,
        'language': 'en',
        'maxReviews': max_reviews,
    }

    logger.info(f'[Apify] Searching: "{query}"')

    try:
        items = apify.run_actor(
            'compass/crawler-google-places',
            run_input,
            timeout_secs=600,
            max_items=200,
        )
    except ApifyError as e:
        logger.error(f'[Apify] Actor run failed: {e}')
        return []

    businesses = []
    for item in items:
        name = item.get('title', '') or item.get('name', '')
        if not name:
            continue

        # Determine status
        status = 'open'
        perm_closed = item.get('permanentlyClosed', False)
        temp_closed = item.get('temporarilyClosed', False)
        if perm_closed:
            status = 'permanently_closed'
        elif temp_closed:
            status = 'temporarily_closed'

        # Build address
        address = item.get('address', '') or city

        # Extract reviews
        reviews = []
        raw_reviews = item.get('reviews', [])
        if isinstance(raw_reviews, list):
            for rev in raw_reviews:
                rating = rev.get('stars', 0) or rev.get('rating', 0)
                try:
                    rating = int(float(rating))
                except (ValueError, TypeError):
                    rating = 0
                text = rev.get('text', '') or rev.get('review', '')
                author = rev.get('name', '') or rev.get('author', '')
                date_str = rev.get('publishedAtDate', '') or rev.get('publishAt', '') or rev.get('date', '')
                reviews.append({
                    'author': author or 'Anonymous',
                    'rating': rating,
                    'text': text,
                    'date': date_str,
                })

        biz = {
            'name': name,
            'address': address,
            'rating': item.get('totalScore', 0) or item.get('rating', 0),
            'review_count': item.get('reviewsCount', 0) or 0,
            'reviews': reviews,
            'status': status,
            'url': item.get('url', ''),
        }
        businesses.append(biz)

    return businesses


# =================================================================
# BeautifulSoup fallback scraping
# =================================================================

def _search_google_businesses(scraper, category_term, city):
    """
    Search Google for businesses and extract listing data from results.
    Uses two queries: a reviews-focused query and a local results query.
    Returns list of business dicts.
    """
    businesses = []

    # Query 1: Reviews-focused search for JSON-LD structured data
    query = f'{category_term} {city} reviews'
    params = {'q': query, 'hl': 'en', 'gl': 'us', 'num': '20'}
    url = f'https://www.google.com/search?{urlencode(params)}'

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise

    if resp and resp.status_code == 200:
        soup = BeautifulSoup(resp.text, 'html.parser')
        _extract_from_search_html(soup, businesses, city)

    # Query 2: Local search (tbm=lcl)
    query2 = f'{category_term} near {city}'
    params2 = {'q': query2, 'hl': 'en', 'gl': 'us', 'tbm': 'lcl'}
    url2 = f'https://www.google.com/search?{urlencode(params2)}'

    try:
        resp2 = scraper.get(url2)
    except RateLimitHit:
        raise

    if resp2 and resp2.status_code == 200:
        soup2 = BeautifulSoup(resp2.text, 'html.parser')
        _extract_from_search_html(soup2, businesses, city)

    # Deduplicate by name
    seen = set()
    unique = []
    for biz in businesses:
        key = biz.get('name', '').lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(biz)

    return unique


def _extract_from_search_html(soup, businesses, city):
    """Extract business data from a Google Search results page."""
    page_text = soup.get_text(' ', strip=True)

    # -- JSON-LD structured data --
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                biz_type = item.get('@type', '')
                if biz_type in ('LocalBusiness', 'Organization', 'Place',
                                'Dentist', 'Attorney', 'Plumber',
                                'AutoRepair', 'InsuranceAgency',
                                'Physician', 'Veterinarian'):
                    biz = _parse_jsonld_business(item, city)
                    if biz:
                        businesses.append(biz)

                # Handle lists of businesses
                if biz_type == 'ItemList':
                    for elem in item.get('itemListElement', []):
                        inner = elem.get('item', elem)
                        b = _parse_jsonld_business(inner, city)
                        if b:
                            businesses.append(b)
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

    # -- Visible DOM elements with rating data --
    for el in soup.select('[data-attrid*="rating"], [data-attrid*="review"]'):
        text = el.get_text(' ', strip=True)
        parts = re.split(r'\s*[·|]\s*', text)
        if len(parts) >= 2:
            name = parts[0].strip()
            rating, review_count = _extract_rating_count(text)
            if name and len(name) > 3:
                businesses.append({
                    'name': name,
                    'rating': rating,
                    'review_count': review_count,
                    'reviews': [],
                    'status': 'open',
                    'address': city,
                    'url': '',
                })

    # -- Local pack cards --
    for card in soup.select('[data-cid], .rllt__details'):
        text = card.get_text(' ', strip=True)
        if len(text) < 10:
            continue
        name_match = re.match(r'^([A-Z][^·|\n]{3,60})', text)
        if name_match:
            name = name_match.group(1).strip()
            rating, review_count = _extract_rating_count(text)
            if rating > 0 or review_count > 0:
                businesses.append({
                    'name': name,
                    'rating': rating,
                    'review_count': review_count,
                    'reviews': [],
                    'status': 'open',
                    'address': city,
                    'url': '',
                })

    # -- Detect closed businesses --
    _detect_closed_status(page_text, businesses)


def _parse_jsonld_business(item, city):
    """Parse a JSON-LD business item into our business dict format."""
    name = item.get('name', '')
    if not name:
        return None

    biz = {
        'name': name,
        'address': city,
        'rating': 0,
        'review_count': 0,
        'reviews': [],
        'status': 'open',
        'url': item.get('url', ''),
    }

    # Address
    addr = item.get('address', {})
    if isinstance(addr, dict):
        parts = [
            addr.get('streetAddress', ''),
            addr.get('addressLocality', ''),
            addr.get('addressRegion', ''),
        ]
        full_addr = ' '.join(p for p in parts if p).strip()
        if full_addr:
            biz['address'] = full_addr

    # Aggregate rating
    agg = item.get('aggregateRating', {})
    if agg:
        try:
            biz['rating'] = float(agg.get('ratingValue', 0))
        except (ValueError, TypeError):
            pass
        try:
            biz['review_count'] = int(agg.get('reviewCount', agg.get('ratingCount', 0)))
        except (ValueError, TypeError):
            pass

    # Individual reviews
    for rev in item.get('review', []):
        review_rating = 0
        rr = rev.get('reviewRating', {})
        if rr:
            try:
                review_rating = int(float(rr.get('ratingValue', 0)))
            except (ValueError, TypeError):
                pass
        author_data = rev.get('author', {})
        author = author_data.get('name', '') if isinstance(author_data, dict) else str(author_data)
        text = rev.get('reviewBody', rev.get('description', ''))
        if text:
            biz['reviews'].append({
                'author': author,
                'rating': review_rating,
                'text': text,
                'date': rev.get('datePublished', ''),
            })

    return biz


def _extract_rating_count(text):
    """Extract rating and review count from a text string."""
    rating = 0
    review_count = 0
    rating_match = re.search(r'([\d.]+)\s*(?:stars?|/\s*5)', text, re.I)
    if rating_match:
        try:
            rating = float(rating_match.group(1))
        except ValueError:
            pass
    count_match = re.search(r'[\(]?\s*([\d,]+)\s*[\)]?\s*(?:reviews?|ratings?|Google)', text, re.I)
    if count_match:
        try:
            review_count = int(count_match.group(1).replace(',', ''))
        except ValueError:
            pass
    return rating, review_count


def _detect_closed_status(page_text, businesses):
    """Check full page text for closed business indicators."""
    lower_text = page_text.lower()
    for status_text, status_key in [
        ('permanently closed', 'permanently_closed'),
        ('temporarily closed', 'temporarily_closed'),
    ]:
        if status_text in lower_text:
            for biz in businesses:
                if biz.get('status') == 'open':
                    name_idx = lower_text.find(biz['name'].lower())
                    closed_idx = lower_text.find(status_text)
                    if name_idx >= 0 and closed_idx >= 0 and abs(name_idx - closed_idx) < 500:
                        biz['status'] = status_key


def _scrape_business_reviews(scraper, business_name, city):
    """
    Fetch review data for a specific business via a dedicated Google Search.
    Returns list of review dicts.
    """
    reviews = []

    query = f'"{business_name}" {city} reviews'
    params = {'q': query, 'hl': 'en', 'gl': 'us'}
    url = f'https://www.google.com/search?{urlencode(params)}'

    try:
        resp = scraper.get(url)
    except RateLimitHit:
        raise

    if not resp or resp.status_code != 200:
        return reviews

    soup = BeautifulSoup(resp.text, 'html.parser')

    # JSON-LD reviews
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                for rev in item.get('review', []):
                    review_rating = 0
                    rr = rev.get('reviewRating', {})
                    if rr:
                        try:
                            review_rating = int(float(rr.get('ratingValue', 0)))
                        except (ValueError, TypeError):
                            pass
                    text = rev.get('reviewBody', rev.get('description', ''))
                    if text:
                        author_data = rev.get('author', {})
                        author = author_data.get('name', '') if isinstance(author_data, dict) else str(author_data)
                        reviews.append({
                            'author': author,
                            'rating': review_rating,
                            'text': text,
                            'date': rev.get('datePublished', ''),
                        })
        except (json.JSONDecodeError, TypeError):
            continue

    # Review snippets in visible elements
    for el in soup.select('[data-attrid*="review"], .review-snippet'):
        text = el.get_text(' ', strip=True)
        if len(text) > 30:
            rating = 0
            rating_match = re.search(r'(\d)\s*(?:/\s*5|star)', text, re.I)
            if rating_match:
                rating = int(rating_match.group(1))
            author = ''
            author_match = re.search(r'(?:by|from|[—-])\s*([A-Z][a-z]+ [A-Z][a-z]+)', text)
            if author_match:
                author = author_match.group(1)
            reviews.append({
                'author': author,
                'rating': rating,
                'text': text[:500],
                'date': '',
            })

    return reviews


# -----------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------

def _parse_review_date(date_str):
    """Parse date string to timezone-aware datetime."""
    if not date_str:
        return None

    text = date_str.strip().lower()
    now = timezone.now()

    # Relative: "X units ago"
    match = re.search(r'(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago', text)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        deltas = {
            'minute': timedelta(minutes=num),
            'hour': timedelta(hours=num),
            'day': timedelta(days=num),
            'week': timedelta(weeks=num),
            'month': timedelta(days=num * 30),
            'year': timedelta(days=num * 365),
        }
        return now - deltas.get(unit, timedelta())

    # "a week/month/year ago"
    unit_match = re.search(r'an?\s+(week|month|year)\s+ago', text)
    if unit_match:
        deltas = {'week': timedelta(weeks=1), 'month': timedelta(days=30), 'year': timedelta(days=365)}
        return now - deltas.get(unit_match.group(1), timedelta())

    # ISO format from Apify: "2025-12-15T10:30:00.000Z"
    iso_match = re.match(r'(\d{4}-\d{2}-\d{2})t?', text)
    if iso_match:
        try:
            dt = datetime.strptime(iso_match.group(1), '%Y-%m-%d')
            return timezone.make_aware(dt)
        except ValueError:
            pass

    # Standard formats
    for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%b %d, %Y', '%B %d, %Y']:
        try:
            dt = datetime.strptime(text, fmt)
            return timezone.make_aware(dt)
        except ValueError:
            continue

    return None


def _review_confidence(rating):
    """Map star rating to confidence level."""
    if rating <= 1:
        return 'high'
    elif rating <= 2:
        return 'medium'
    return 'low'


def _review_urgency(posted_at):
    """Map review age to urgency level and score."""
    if not posted_at:
        return 'new', 50
    age = timezone.now() - posted_at
    if age < timedelta(days=7):
        return 'hot', 90
    elif age < timedelta(days=30):
        return 'warm', 70
    return 'new', 50


# -----------------------------------------------------------------
# Business processing (shared by both engines)
# -----------------------------------------------------------------

def _process_businesses(businesses, category, search_term, city, max_reviews,
                        dry_run, stats, scraper=None):
    """
    Process a list of businesses: handle closed status, filter negative
    reviews, create leads or collect dry-run matches.

    Args:
        businesses: list of normalized business dicts
        category: category key string
        search_term: original search term
        city: target city
        max_reviews: max reviews per business
        dry_run: bool
        stats: stats dict to update
        scraper: optional BaseScraper for BS4 fallback review fetching
    """
    for biz in businesses:
        if scraper and scraper.is_stopped:
            break

        biz_name = biz.get('name', 'Unknown')
        biz_address = biz.get('address', city)
        biz_status = biz.get('status', 'open')
        biz_url = biz.get('url', '')
        source_url = biz_url or f'https://www.google.com/search?q={quote_plus(biz_name + " " + city)}'

        # -- Handle closed businesses --
        if biz_status in ('permanently_closed', 'temporarily_closed'):
            stats['orphaned_customers'] += 1
            content = (
                f'ORPHANED CUSTOMERS: {biz_name} is {biz_status.replace("_", " ")}.\n\n'
                f'Business: {biz_name}\n'
                f'Category: {category}\n'
                f'Location: {biz_address}\n'
                f'Status: {biz_status.replace("_", " ").title()}\n\n'
                f'Customers of this business may be looking for a new {search_term}.'
            )

            if dry_run:
                stats['dry_run_matches'].append({
                    'type': 'ORPHANED',
                    'business_name': biz_name,
                    'category': category,
                    'status': biz_status,
                    'location': biz_address,
                    'url': source_url,
                })
            else:
                content_hash = hashlib.sha256(
                    f'google_reviews|{biz_name}|orphaned'.encode()
                ).hexdigest()
                from core.models.leads import Lead
                if not Lead.objects.filter(content_hash=content_hash).exists():
                    Lead.objects.create(
                        platform='google_reviews',
                        source_url=source_url,
                        source_content=content,
                        source_author=biz_name,
                        detected_location=biz_address,
                        urgency_score=90,
                        urgency_level='hot',
                        confidence='high',
                        content_hash=content_hash,
                        raw_data={
                            'business_name': biz_name,
                            'category': category,
                            'business_status': biz_status,
                            'type': 'orphaned_customer',
                        },
                    )
                    stats['created'] += 1
                else:
                    stats['duplicates'] += 1

        # -- Collect reviews --
        reviews = list(biz.get('reviews', []))

        # BS4 fallback: fetch more reviews if we have few
        if scraper and len(reviews) < 3 and not scraper.is_stopped:
            try:
                extra = _scrape_business_reviews(scraper, biz_name, city)
                reviews.extend(extra)
            except RateLimitHit:
                logger.warning(f'Rate limit hit fetching reviews for {biz_name}')
                break

        stats['reviews_scraped'] += len(reviews)

        # Process negative reviews
        review_count = 0
        for review in reviews:
            if review_count >= max_reviews:
                break

            rating = review.get('rating', 0)
            if rating > 3 or rating < 1:
                continue

            review_text = review.get('text', '')
            if not review_text or len(review_text) < 15:
                continue

            review_count += 1
            stats['negative_reviews'] += 1

            posted_at = _parse_review_date(review.get('date', ''))
            confidence = _review_confidence(rating)
            urgency_level, urgency_score = _review_urgency(posted_at)
            author = review.get('author', 'Anonymous')

            content = (
                f'{rating}-star Google review on {biz_name}\n\n'
                f'Reviewer: {author}\n'
                f'Rating: {"*" * rating} ({rating}/5)\n\n'
                f'"{review_text}"\n\n'
                f'Business: {biz_name}\n'
                f'Category: {category}\n'
                f'Location: {biz_address}'
            )

            if dry_run:
                stats['dry_run_matches'].append({
                    'type': 'NEGATIVE_REVIEW',
                    'business_name': biz_name,
                    'category': category,
                    'rating': rating,
                    'confidence': confidence,
                    'urgency': urgency_level,
                    'author': author,
                    'text': review_text[:100],
                    'location': biz_address,
                    'url': source_url,
                })
                continue

            lead, created, num_assigned = process_lead(
                platform='google_reviews',
                source_url=source_url,
                content=content,
                author=author,
                posted_at=posted_at,
                raw_data={
                    'business_name': biz_name,
                    'category': category,
                    'star_rating': rating,
                    'reviewer_name': author,
                    'review_date': review.get('date', ''),
                    'business_status': biz_status,
                    'type': 'negative_review',
                },
                source_group='reviews',
                source_type='google_reviews',
            )

            if created:
                stats['created'] += 1
                stats['assigned'] += num_assigned
            else:
                stats['duplicates'] += 1


# -----------------------------------------------------------------
# Main monitoring function
# -----------------------------------------------------------------

def monitor_google_reviews_scraper(
    categories=None,
    city='Long Island, NY',
    max_reviews=20,
    dry_run=False,
):
    """
    Main monitoring function. Searches for businesses in target
    categories/city, scrapes review data, and flags negative reviews
    (<=3 stars) as leads.

    Tries Apify first (handles JS rendering). Falls back to
    BeautifulSoup scraping if Apify is not configured.

    Args:
        categories: list of category keys (default: all)
        city: target city/area
        max_reviews: max reviews per business
        dry_run: if True, log matches without creating leads

    Returns:
        dict with counts
    """
    if categories is None:
        categories = list(CATEGORY_SEARCH_TERMS.keys())

    stats = {
        'businesses_found': 0,
        'reviews_scraped': 0,
        'negative_reviews': 0,
        'orphaned_customers': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
        'categories_searched': 0,
        'dry_run_matches': [],
        'engine': 'unknown',
    }

    # Try Apify first
    apify = _apify_available()

    if apify:
        stats['engine'] = 'apify'
        logger.info('[Google Reviews] Using Apify engine')

        for category in categories:
            search_terms = CATEGORY_SEARCH_TERMS.get(category, [category])
            search_term = search_terms[0]
            stats['categories_searched'] += 1

            logger.info(f'[Apify] Searching: "{search_term}" in {city}')

            try:
                businesses = _search_businesses_apify(apify, search_term, city, max_reviews)
            except Exception as e:
                logger.error(f'[Apify] Error searching "{search_term}": {e}')
                stats['errors'] += 1
                continue

            logger.info(f'[Apify] Found {len(businesses)} businesses for "{search_term}"')
            stats['businesses_found'] += len(businesses)

            if businesses:
                _process_businesses(
                    businesses, category, search_term, city,
                    max_reviews, dry_run, stats,
                )
    else:
        # Fallback to BS4 scraping
        stats['engine'] = 'beautifulsoup'
        logger.info('[Google Reviews] Apify not available, falling back to BeautifulSoup')

        scraper = GoogleMapsScraper()

        for category in categories:
            if scraper.is_stopped:
                break

            search_terms = CATEGORY_SEARCH_TERMS.get(category, [category])
            search_term = search_terms[0]
            stats['categories_searched'] += 1

            logger.info(f'Searching Google: "{search_term}" in {city}')

            try:
                businesses = _search_google_businesses(scraper, search_term, city)
            except RateLimitHit:
                logger.warning('Rate limit hit during business search')
                break

            logger.info(f'Found {len(businesses)} businesses for "{search_term}" in {city}')
            stats['businesses_found'] += len(businesses)

            if not businesses:
                continue

            businesses = scraper.shuffle(businesses)
            _process_businesses(
                businesses, category, search_term, city,
                max_reviews, dry_run, stats, scraper=scraper,
            )

    logger.info(f'Google Reviews scraper complete: {stats}')
    return stats
