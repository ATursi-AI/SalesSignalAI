"""
Management command to run the Apify-based Twitter/X monitor.
Usage:
    python manage.py monitor_twitter_apify
    python manage.py monitor_twitter_apify --dry-run
    python manage.py monitor_twitter_apify --max-tweets 200
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.apify_twitter import monitor_twitter


class Command(BaseCommand):
    help = (
        'Search Twitter/X for service request tweets via Apify cloud. '
        'Replaces $100/month X API. Dynamically uses business locations — '
        'works nationwide. Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-tweets', type=int, default=100,
            help='Max tweets to fetch (default: 100)',
        )
        parser.add_argument(
            '--max-age', type=int, default=48,
            help='Skip tweets older than this many hours (default: 48)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Apify Twitter/X monitor...'))
        self.stdout.write(f"  Max tweets: {options['max_tweets']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_twitter(
            max_tweets=options['max_tweets'],
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        if 'error' in stats:
            self.stdout.write(self.style.ERROR(f"\n  ERROR: {stats['error']}"))
            if stats['error'] == 'api_not_configured':
                self.stdout.write('  Fix: Set APIFY_API_TOKEN in .env')
            return

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Twitter/X Monitor Results:'))
        self.stdout.write(f"  Tweets scraped:     {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats.get('assigned', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
