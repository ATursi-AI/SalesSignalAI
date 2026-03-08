"""
Management command to run the Patch.com community board monitor.
Usage:
    python manage.py monitor_patch
    python manage.py monitor_patch --dry-run
    python manage.py monitor_patch --no-details --max-age 24
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.patch import monitor_patch, DEFAULT_PATCH_TOWNS, PATCH_SECTIONS


class Command(BaseCommand):
    help = 'Scrape Patch.com community boards for service leads in the tri-state area'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-per-section',
            type=int,
            default=20,
            help='Max posts to process per section per town (default: 20)',
        )
        parser.add_argument(
            '--no-details',
            action='store_true',
            help='Skip fetching full post details (faster but less data)',
        )
        parser.add_argument(
            '--max-age',
            type=int,
            default=48,
            help='Max post age in hours (default: 48)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Patch.com monitor...'))
        self.stdout.write(f"  Towns: {len(DEFAULT_PATCH_TOWNS)}")
        self.stdout.write(f"  Sections: {', '.join(PATCH_SECTIONS)}")

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE — no leads will be created'))

        stats = monitor_patch(
            max_per_section=options['max_per_section'],
            fetch_details=not options['no_details'],
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Patch.com Monitor Results:'))
        self.stdout.write(f"  Posts scraped:     {stats['scraped']}")
        self.stdout.write(f"  Leads created:     {stats['created']}")
        self.stdout.write(f"  Duplicates:        {stats['duplicates']}")
        self.stdout.write(f"  Assignments:       {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:            {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
