"""
Management command to run the Google Reviews negative sentiment monitor.
Scrapes Google Maps for businesses and flags negative reviews as leads.

Usage:
    python manage.py monitor_google_reviews_scraper --dry-run
    python manage.py monitor_google_reviews_scraper --city "Long Island, NY" --category plumber
    python manage.py monitor_google_reviews_scraper --category plumber electrician hvac --dry-run
    python manage.py monitor_google_reviews_scraper --max-reviews 10
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.google_reviews_scraper import (
    monitor_google_reviews_scraper,
    CATEGORY_SEARCH_TERMS,
)


class Command(BaseCommand):
    help = 'Scrape Google Maps for negative reviews and closed businesses'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show matches without creating leads',
        )
        parser.add_argument(
            '--city',
            type=str,
            default='Long Island, NY',
            help='Target city/area (default: "Long Island, NY")',
        )
        parser.add_argument(
            '--category',
            nargs='+',
            default=None,
            help=f'Service categories to search (default: all). '
                 f'Choices: {", ".join(sorted(CATEGORY_SEARCH_TERMS.keys()))}',
        )
        parser.add_argument(
            '--max-reviews',
            type=int,
            default=20,
            help='Max reviews to process per business (default: 20)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        city = options['city']
        categories = options['category']
        max_reviews = options['max_reviews']

        self.stdout.write(self.style.HTTP_INFO('Starting Google Reviews Scraper...'))
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — no leads will be created'))
        self.stdout.write(f'  City: {city}')
        self.stdout.write(f'  Categories: {", ".join(categories or CATEGORY_SEARCH_TERMS.keys())}')
        self.stdout.write(f'  Max reviews/business: {max_reviews}')
        self.stdout.write('')

        stats = monitor_google_reviews_scraper(
            categories=categories,
            city=city,
            max_reviews=max_reviews,
            dry_run=dry_run,
        )

        engine = stats.get('engine', 'unknown')
        engine_label = 'Apify (JS-rendered)' if engine == 'apify' else 'BeautifulSoup (fallback)'

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Google Reviews Scraper Results:'))
        self.stdout.write(f'  Engine:              {engine_label}')
        self.stdout.write(f'  Categories searched: {stats["categories_searched"]}')
        self.stdout.write(f'  Businesses found:    {stats["businesses_found"]}')
        self.stdout.write(f'  Reviews scraped:     {stats["reviews_scraped"]}')
        self.stdout.write(f'  Negative reviews:    {stats["negative_reviews"]}')
        self.stdout.write(f'  Orphaned customers:  {stats["orphaned_customers"]}')

        if dry_run:
            matches = stats.get('dry_run_matches', [])
            if matches:
                self.stdout.write('')
                self.stdout.write(self.style.HTTP_INFO(f'  === {len(matches)} MATCHES FOUND ==='))
                self.stdout.write('')
                for m in matches:
                    if m['type'] == 'ORPHANED':
                        self.stdout.write(
                            self.style.ERROR(
                                f'  [ORPHANED] {m["business_name"]} — '
                                f'{m["status"].replace("_", " ").title()}'
                            )
                        )
                        self.stdout.write(
                            f'    Category: {m["category"]} | '
                            f'Location: {m["location"]}'
                        )
                        self.stdout.write(f'    {m["url"]}')
                        self.stdout.write('')
                    else:
                        safe_text = m.get("text", "").encode('ascii', 'replace').decode('ascii')
                        self.stdout.write(
                            f'  [{m["rating"]}-STAR] {m["business_name"]}'
                        )
                        self.stdout.write(
                            f'    Category: {m["category"]} | '
                            f'Confidence: {m["confidence"].upper()} | '
                            f'Urgency: {m["urgency"].upper()} | '
                            f'Author: {m["author"]}'
                        )
                        self.stdout.write(f'    "{safe_text}..."')
                        self.stdout.write(f'    {m["url"]}')
                        self.stdout.write('')
            else:
                self.stdout.write(self.style.WARNING(
                    '  No negative reviews or orphaned customers found.'
                ))
                if engine == 'beautifulsoup':
                    self.stdout.write(self.style.WARNING(
                        '  Note: Using BeautifulSoup fallback. Google Maps is '
                        'JS-rendered. Configure APIFY_API_TOKEN for full results.'
                    ))
        else:
            self.stdout.write(f'  Leads created:       {stats["created"]}')
            self.stdout.write(f'  Duplicates:          {stats["duplicates"]}')
            self.stdout.write(f'  Assignments:         {stats["assigned"]}')

        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f'  Errors:              {stats["errors"]}'))

        self.stdout.write(self.style.SUCCESS('Done.'))
