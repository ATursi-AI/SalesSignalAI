"""
Management command to run the Google Places API monitor.
Detects negative reviews, closed businesses, new businesses, and Q&A leads.

Usage:
    python manage.py monitor_google_places --dry-run
    python manage.py monitor_google_places --city "Long Island, NY" --category plumber
    python manage.py monitor_google_places --category plumber,electrician,dentist --radius 15000
    python manage.py monitor_google_places --dry-run --category plumber --radius 10000
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.google_places import (
    monitor_google_places,
    CATEGORY_PLACE_TYPES,
)


class Command(BaseCommand):
    help = 'Monitor Google Places API for negative reviews, closed businesses, new businesses, Q&A, and no-website prospects'

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
            type=str,
            default=None,
            help=(
                'Comma-separated service categories to search '
                f'(default: all). Choices: {", ".join(sorted(CATEGORY_PLACE_TYPES.keys()))}'
            ),
        )
        parser.add_argument(
            '--radius',
            type=int,
            default=10000,
            help='Search radius in meters (default: 10000)',
        )
        parser.add_argument(
            '--max-reviews',
            type=int,
            default=5,
            help='Max negative reviews to process per business (default: 5)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        city = options['city']
        radius = options['radius']
        max_reviews = options['max_reviews']

        # Parse comma-separated categories
        categories = None
        if options['category']:
            categories = [c.strip() for c in options['category'].split(',') if c.strip()]

        self.stdout.write(self.style.HTTP_INFO('Starting Google Places API Monitor...'))
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — no leads will be created'))
        self.stdout.write(f'  City: {city}')
        self.stdout.write(f'  Radius: {radius}m')
        self.stdout.write(f'  Categories: {", ".join(categories or CATEGORY_PLACE_TYPES.keys())}')
        self.stdout.write(f'  Max reviews/business: {max_reviews}')
        self.stdout.write('')

        stats = monitor_google_places(
            categories=categories,
            city=city,
            radius=radius,
            max_reviews=max_reviews,
            dry_run=dry_run,
        )

        # Check for config errors
        if stats.get('error'):
            self.stdout.write(self.style.ERROR(f'  ERROR: {stats["error"]}'))
            return

        # Results
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Google Places Monitor Results:'))
        self.stdout.write(f'  Categories searched:  {stats["categories_searched"]}')
        self.stdout.write(f'  Businesses found:     {stats["businesses_found"]}')
        self.stdout.write(f'  Negative reviews:     {stats["negative_reviews"]}')
        self.stdout.write(f'  Closed businesses:    {stats["closed_businesses"]}')
        self.stdout.write(f'  New businesses:       {stats["new_businesses"]}')
        self.stdout.write(f'  Q&A questions:        {stats["qna_questions"]}')
        self.stdout.write(f'  No-website prospects: {stats["no_website"]}')

        # API usage
        api = stats.get('api_usage', {})
        if api:
            self.stdout.write('')
            self.stdout.write(self.style.HTTP_INFO('  API Usage:'))
            self.stdout.write(f'    Nearby Search calls: {api.get("nearby_search_calls", 0)}')
            self.stdout.write(f'    Place Details calls: {api.get("place_details_calls", 0)}')
            self.stdout.write(f'    Total API calls:     {api.get("total_calls", 0)}')
            self.stdout.write(f'    Estimated cost:      ${api.get("estimated_cost_usd", 0):.4f}')

        if dry_run:
            matches = stats.get('dry_run_matches', [])
            if matches:
                self.stdout.write('')
                self.stdout.write(self.style.HTTP_INFO(f'  === {len(matches)} MATCHES FOUND ==='))
                self.stdout.write('')
                for m in matches:
                    match_type = m['type']
                    biz_name = m.get('business_name', '?')

                    if match_type == 'NEGATIVE_REVIEW':
                        safe_text = m.get('text', '').encode('ascii', 'replace').decode('ascii')
                        self.stdout.write(
                            f'  [{m["rating"]}-STAR] {biz_name}'
                        )
                        self.stdout.write(
                            f'    Category: {m["category"]} | '
                            f'Confidence: {m["confidence"].upper()} | '
                            f'Urgency: {m["urgency"].upper()} | '
                            f'Author: {m["author"]}'
                        )
                        self.stdout.write(f'    "{safe_text}..."')

                    elif match_type == 'CLOSED_BUSINESS':
                        self.stdout.write(self.style.ERROR(
                            f'  [CLOSED] {biz_name} — '
                            f'{m["status"].replace("_", " ").title()}'
                        ))
                        self.stdout.write(
                            f'    Category: {m["category"]} | '
                            f'Location: {m["location"]}'
                        )

                    elif match_type == 'NEW_BUSINESS':
                        self.stdout.write(self.style.WARNING(
                            f'  [NEW] {biz_name}'
                        ))
                        self.stdout.write(
                            f'    Category: {m["category"]} | '
                            f'Rating: {m.get("rating", "?")} | '
                            f'Reviews: {m.get("reviews", 0)} | '
                            f'Location: {m["location"]}'
                        )

                    elif match_type == 'QNA_QUESTION':
                        safe_q = m.get('question', '').encode('ascii', 'replace').decode('ascii')
                        self.stdout.write(
                            f'  [Q&A] {biz_name}'
                        )
                        self.stdout.write(
                            f'    Category: {m["category"]} | '
                            f'Author: {m.get("author", "?")} | '
                            f'Q: "{safe_q}..."'
                        )

                    elif match_type == 'NO_WEBSITE':
                        self.stdout.write(self.style.NOTICE(
                            f'  [NO WEBSITE] {biz_name}'
                        ))
                        self.stdout.write(
                            f'    Category: {m["category"]} | '
                            f'Rating: {m.get("rating", "?")} ({m.get("reviews", 0)} reviews) | '
                            f'Phone: {m.get("phone") or "N/A"}'
                        )
                        self.stdout.write(
                            f'    Location: {m.get("location", "")}'
                        )

                    self.stdout.write(f'    {m.get("url", "")}')
                    self.stdout.write('')
            else:
                self.stdout.write(self.style.WARNING(
                    '  No matches found.'
                ))
                if not stats.get('businesses_found'):
                    self.stdout.write(self.style.WARNING(
                        '  No businesses found. Check GOOGLE_PLACES_API_KEY and city/radius.'
                    ))
        else:
            self.stdout.write(f'  Leads created:        {stats["created"]}')
            self.stdout.write(f'  Duplicates:           {stats["duplicates"]}')
            self.stdout.write(f'  Assignments:          {stats["assigned"]}')

        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f'  Errors:               {stats["errors"]}'))

        self.stdout.write(self.style.SUCCESS('Done.'))
