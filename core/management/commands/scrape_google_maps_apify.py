"""
Management command to scrape Google Maps via Apify.
Usage:
    python manage.py scrape_google_maps_apify
    python manage.py scrape_google_maps_apify --query "plumber in Miami, FL"
    python manage.py scrape_google_maps_apify --reviews --max-reviews 20
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.apify_google_maps import scrape_google_maps, scrape_google_reviews


class Command(BaseCommand):
    help = (
        'Scrape Google Maps business listings via Apify cloud. '
        'Bypasses Google Places API daily quota (10K units). '
        'Richer data: popular times, review highlights, photos. '
        'Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--query', type=str, action='append', dest='queries',
            help='Search query (can repeat, e.g. --query "plumber in Miami, FL")',
        )
        parser.add_argument(
            '--max-results', type=int, default=50,
            help='Max results per query (default: 50)',
        )
        parser.add_argument(
            '--reviews', action='store_true',
            help='Also scrape reviews for each place',
        )
        parser.add_argument(
            '--max-reviews', type=int, default=10,
            help='Max reviews per place when --reviews is used (default: 10)',
        )
        parser.add_argument(
            '--review-urls', type=str, action='append', dest='review_urls',
            help='Google Maps URL to scrape reviews from (can repeat)',
        )

    def handle(self, *args, **options):
        # If review-urls provided, do review scraping instead
        if options.get('review_urls'):
            self.stdout.write(self.style.HTTP_INFO('Scraping Google Maps Reviews via Apify...'))
            self.stdout.write(f"  Places: {len(options['review_urls'])}")
            self.stdout.write(f"  Max reviews per place: {options['max_reviews']}")

            result = scrape_google_reviews(
                place_urls=options['review_urls'],
                max_reviews=options['max_reviews'],
            )

            if 'error' in result:
                self.stdout.write(self.style.ERROR(f"\n  ERROR: {result['error']}"))
                return

            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('Google Maps Reviews Results:'))
            self.stdout.write(f"  Reviews scraped:    {result['items_scraped']}")
            self.stdout.write(f"  Places checked:     {result.get('places_checked', 0)}")
            self.stdout.write(self.style.SUCCESS('Done.'))
            return

        # Standard Google Maps scraping
        self.stdout.write(self.style.HTTP_INFO('Scraping Google Maps via Apify...'))
        if options['queries']:
            self.stdout.write(f"  Queries: {len(options['queries'])}")
        else:
            self.stdout.write('  Queries: auto-generated from active business locations')
        self.stdout.write(f"  Max results per query: {options['max_results']}")

        result = scrape_google_maps(
            search_queries=options.get('queries'),
            max_results_per_query=options['max_results'],
            include_reviews=options['reviews'],
            max_reviews=options['max_reviews'] if options['reviews'] else 0,
        )

        if 'error' in result:
            self.stdout.write(self.style.ERROR(f"\n  ERROR: {result['error']}"))
            if result['error'] == 'api_not_configured':
                self.stdout.write('  Fix: Set APIFY_API_TOKEN in .env')
            return

        if 'skipped_reason' in result:
            self.stdout.write(self.style.WARNING(f"  Skipped: {result['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Google Maps Scraping Results:'))
        self.stdout.write(f"  Places scraped:     {result['items_scraped']}")
        self.stdout.write(f"  Queries searched:   {result.get('queries_searched', 0)}")

        # Show sample results
        items = result.get('items', [])
        if items:
            self.stdout.write('')
            self.stdout.write('  Sample results:')
            for place in items[:5]:
                rating = place.get('rating', 0)
                reviews = place.get('review_count', 0)
                self.stdout.write(
                    f"    {place['name']} — {place.get('address', 'N/A')} "
                    f"({rating}/5, {reviews} reviews)"
                )

        self.stdout.write(self.style.SUCCESS('Done.'))
