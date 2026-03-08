"""
Management command to run the Apify-based Threads monitor.
Usage:
    python manage.py monitor_threads
    python manage.py monitor_threads --dry-run
    python manage.py monitor_threads --max-posts 100
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.apify_threads import monitor_threads


class Command(BaseCommand):
    help = (
        'Search Threads (Meta) for local service discussions via Apify cloud. '
        '275M+ monthly users. Growing platform for local recommendations. '
        'Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-posts', type=int, default=50,
            help='Max posts to fetch (default: 50)',
        )
        parser.add_argument(
            '--max-age', type=int, default=72,
            help='Skip posts older than this many hours (default: 72)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Apify Threads monitor...'))
        self.stdout.write(f"  Max posts: {options['max_posts']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_threads(
            max_posts=options['max_posts'],
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
        self.stdout.write(self.style.SUCCESS('Threads Monitor Results:'))
        self.stdout.write(f"  Posts scraped:      {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats.get('assigned', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
