"""
Management command to run the Apify-based Nextdoor monitor.
Usage:
    python manage.py monitor_nextdoor
    python manage.py monitor_nextdoor --dry-run
    python manage.py monitor_nextdoor --max-posts 100
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.apify_nextdoor import monitor_nextdoor


class Command(BaseCommand):
    help = (
        'Scrape Nextdoor for service request leads via Apify cloud. '
        'Dynamically uses business locations — works nationwide. '
        'Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-posts', type=int, default=50,
            help='Max posts to fetch (default: 50)',
        )
        parser.add_argument(
            '--max-age', type=int, default=48,
            help='Skip posts older than this many hours (default: 48)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Apify Nextdoor monitor...'))
        self.stdout.write(f"  Max posts: {options['max_posts']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_nextdoor(
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
        self.stdout.write(self.style.SUCCESS('Nextdoor Monitor Results:'))
        self.stdout.write(f"  Posts scraped:      {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
