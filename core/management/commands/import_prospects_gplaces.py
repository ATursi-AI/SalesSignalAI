"""
Import prospects from Google Places for sales outreach.

Searches Google Places for businesses by trade and location,
creates SalesProspect records, and optionally enrolls them
in a sales sequence.

Usage:
    # Find plumbers in Austin TX
    python manage.py import_prospects_gplaces --trade plumber --city "Austin" --state TX

    # Find electricians, limit 20, assign to salesperson ID 1
    python manage.py import_prospects_gplaces --trade electrician --city "Phoenix" --state AZ --limit 20 --salesperson 1

    # Import and auto-enroll in sequence #3
    python manage.py import_prospects_gplaces --trade "commercial cleaning" --city "Dallas" --state TX --sequence 3

    # Dry run — see what would be imported
    python manage.py import_prospects_gplaces --trade plumber --city "Austin" --state TX --dry-run

    # Filter by low reviews (easy targets for REP agent pitch)
    python manage.py import_prospects_gplaces --trade plumber --city "Austin" --state TX --max-reviews 50
"""
import logging
from datetime import date

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from core.models.sales import SalesPerson, SalesProspect
from core.models.sales_sequences import SalesSequence, SequenceEnrollment
from core.models.prospect_videos import ProspectVideo

logger = logging.getLogger(__name__)


def search_google_places(trade, city, state, api_key, limit=20, page_token=None):
    """
    Search Google Places (New) for businesses by trade and location.
    Returns list of place results.
    """
    url = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
    query = f'{trade} in {city}, {state}'

    params = {
        'query': query,
        'key': api_key,
        'type': 'establishment',
    }
    if page_token:
        params['pagetoken'] = page_token

    results = []
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f'[GPlaces Import] API error {resp.status_code}: {resp.text[:200]}')
            return results

        data = resp.json()
        places = data.get('results', [])
        results.extend(places[:limit])

        # Paginate if needed and under limit
        next_token = data.get('next_page_token')
        if next_token and len(results) < limit:
            import time
            time.sleep(2)  # Google requires delay between pagination
            more = search_google_places(trade, city, state, api_key,
                                         limit=limit - len(results), page_token=next_token)
            results.extend(more)

    except Exception as e:
        logger.error(f'[GPlaces Import] Error: {e}')

    return results[:limit]


def get_place_details(place_id, api_key):
    """Fetch phone, website, and owner details for a specific place."""
    url = 'https://maps.googleapis.com/maps/api/place/details/json'
    params = {
        'place_id': place_id,
        'key': api_key,
        'fields': 'name,formatted_phone_number,website,url,formatted_address,address_components,rating,user_ratings_total,business_status,opening_hours',
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get('result', {})
    except Exception as e:
        logger.error(f'[GPlaces Import] Detail fetch error: {e}')

    return {}


class Command(BaseCommand):
    help = 'Import prospects from Google Places for sales outreach'

    def add_arguments(self, parser):
        parser.add_argument('--trade', required=True, help='Trade to search: plumber, electrician, etc.')
        parser.add_argument('--city', required=True, help='City name')
        parser.add_argument('--state', required=True, help='State abbreviation (TX, NY, etc.)')
        parser.add_argument('--limit', type=int, default=20, help='Max prospects to import (default 20)')
        parser.add_argument('--salesperson', type=int, help='SalesPerson ID to assign prospects to')
        parser.add_argument('--sequence', type=int, help='SalesSequence ID to auto-enroll prospects in')
        parser.add_argument('--batch-tag', type=str, help='Batch tag for enrollment grouping')
        parser.add_argument('--max-reviews', type=int, help='Only import businesses with <= this many reviews')
        parser.add_argument('--min-rating', type=float, help='Only import businesses with >= this rating')
        parser.add_argument('--create-video-pages', action='store_true',
            help='Auto-create ProspectVideo landing pages for each import')
        parser.add_argument('--dry-run', action='store_true', help='Preview without saving')
        parser.add_argument('--fetch-details', action='store_true',
            help='Fetch phone/website via Place Details API (uses more quota)')

    def handle(self, *args, **options):
        trade = options['trade']
        city = options['city']
        state = options['state'].upper()
        limit = options['limit']
        dry_run = options.get('dry_run', False)
        fetch_details = options.get('fetch_details', False)
        max_reviews = options.get('max_reviews')
        min_rating = options.get('min_rating')
        create_video = options.get('create_video_pages', False)

        api_key = getattr(settings, 'GOOGLE_PLACES_API_KEY', '') or getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
        if not api_key:
            self.stderr.write('Error: GOOGLE_PLACES_API_KEY or GOOGLE_MAPS_API_KEY not in settings')
            return

        # Get salesperson
        salesperson = None
        if options.get('salesperson'):
            try:
                salesperson = SalesPerson.objects.get(pk=options['salesperson'])
            except SalesPerson.DoesNotExist:
                self.stderr.write(f'SalesPerson #{options["salesperson"]} not found')
                return
        else:
            salesperson = SalesPerson.objects.filter(status='active').first()
            if not salesperson:
                self.stderr.write('No active salespeople found. Create one first.')
                return

        # Get sequence if specified
        sequence = None
        if options.get('sequence'):
            try:
                sequence = SalesSequence.objects.get(pk=options['sequence'])
            except SalesSequence.DoesNotExist:
                self.stderr.write(f'SalesSequence #{options["sequence"]} not found')
                return

        batch_tag = options.get('batch_tag') or f'{slugify(city)}-{slugify(trade)}-{date.today().isoformat()}'

        self.stdout.write(f'{"[DRY RUN] " if dry_run else ""}Searching Google Places for "{trade}" in {city}, {state}...\n')

        # Search
        places = search_google_places(trade, city, state, api_key, limit=limit)

        if not places:
            self.stdout.write('No results found.')
            return

        self.stdout.write(f'Found {len(places)} results. Processing...\n')

        stats = {'imported': 0, 'skipped_duplicate': 0, 'skipped_filter': 0, 'enrolled': 0, 'video_pages': 0}

        for place in places:
            name = place.get('name', '')
            rating = place.get('rating', 0)
            review_count = place.get('user_ratings_total', 0)
            address = place.get('formatted_address', '')
            place_id = place.get('place_id', '')

            # Apply filters
            if max_reviews and review_count > max_reviews:
                stats['skipped_filter'] += 1
                continue
            if min_rating and rating < min_rating:
                stats['skipped_filter'] += 1
                continue

            # Check for duplicate
            exists = SalesProspect.objects.filter(
                business_name=name, city=city, state=state
            ).exists()
            if exists:
                stats['skipped_duplicate'] += 1
                self.stdout.write(f'  SKIP (duplicate): {name}')
                continue

            # Fetch details if requested
            phone = ''
            website = ''
            if fetch_details and place_id:
                details = get_place_details(place_id, api_key)
                phone = details.get('formatted_phone_number', '')
                website = details.get('website', '')

            if dry_run:
                self.stdout.write(
                    f'  [DRY RUN] Would import: {name} | {rating}★ ({review_count} reviews) | {address}'
                    f'{f" | {phone}" if phone else ""}{f" | {website}" if website else ""}'
                )
                stats['imported'] += 1
                continue

            # Create SalesProspect
            prospect = SalesProspect.objects.create(
                salesperson=salesperson,
                business_name=name,
                phone=phone,
                website=website,
                address=address,
                city=city,
                state=state,
                service_category=trade,
                source='google_maps_scan',
                google_rating=rating if rating else None,
                google_review_count=review_count if review_count else None,
                has_website=bool(website),
                pipeline_stage='new',
                notes=f'Imported from Google Places. Place ID: {place_id}',
            )
            stats['imported'] += 1
            self.stdout.write(f'  + {name} | {rating}★ ({review_count} reviews) | {phone or "no phone"}')

            # Auto-create video page if requested
            video_page = None
            if create_video:
                slug = slugify(f'{name}-{city}')[:200]
                # Ensure unique slug
                base_slug = slug
                counter = 1
                while ProspectVideo.objects.filter(slug=slug).exists():
                    slug = f'{base_slug}-{counter}'
                    counter += 1

                video_page = ProspectVideo.objects.create(
                    slug=slug,
                    prospect_business_name=name,
                    prospect_owner_name='',
                    prospect_phone=phone,
                    prospect_email='',
                    prospect_trade=trade,
                    prospect_city=city,
                    prospect_state=state,
                    trigger_type='custom',
                    trigger_detail=f'Found via Google Places search — {rating}★ with {review_count} reviews',
                    status='draft',
                )
                stats['video_pages'] += 1

            # Auto-enroll in sequence if specified
            if sequence:
                first_step = sequence.steps.order_by('step_number').first()
                next_date = date.today()
                current_step = 0
                if first_step:
                    current_step = first_step.step_number
                    from datetime import timedelta
                    next_date = date.today() + timedelta(days=first_step.delay_days)

                SequenceEnrollment.objects.create(
                    sequence=sequence,
                    prospect=prospect,
                    video_page=video_page,
                    status='active',
                    current_step=current_step,
                    next_action_date=next_date,
                    batch_tag=batch_tag,
                    enrolled_by=salesperson.user,
                )
                stats['enrolled'] += 1

        self.stdout.write(f'\n{"[DRY RUN] " if dry_run else ""}Done!')
        self.stdout.write(f'  Imported: {stats["imported"]}')
        self.stdout.write(f'  Skipped (duplicate): {stats["skipped_duplicate"]}')
        self.stdout.write(f'  Skipped (filter): {stats["skipped_filter"]}')
        if sequence:
            self.stdout.write(f'  Enrolled in sequence: {stats["enrolled"]}')
        if create_video:
            self.stdout.write(f'  Video pages created: {stats["video_pages"]}')
