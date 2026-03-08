"""
Management command to run the Apify-based TikTok monitor.
Usage:
    python manage.py monitor_tiktok
    python manage.py monitor_tiktok --dry-run
    python manage.py monitor_tiktok --max-videos 100
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.apify_tiktok import monitor_tiktok


class Command(BaseCommand):
    help = (
        'Search TikTok for home service content via Apify cloud. '
        'Finds disaster videos, renovation content, and service requests. '
        'Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-videos', type=int, default=50,
            help='Max videos to fetch (default: 50)',
        )
        parser.add_argument(
            '--max-age', type=int, default=72,
            help='Skip content older than this many hours (default: 72)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Apify TikTok monitor...'))
        self.stdout.write(f"  Max videos: {options['max_videos']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_tiktok(
            max_videos=options['max_videos'],
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
        self.stdout.write(self.style.SUCCESS('TikTok Monitor Results:'))
        self.stdout.write(f"  Videos scraped:     {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats.get('assigned', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
