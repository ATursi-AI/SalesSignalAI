"""
Management command to run the Trade Forum monitor.
Usage:
    python manage.py monitor_trade_forums
    python manage.py monitor_trade_forums --dry-run
    python manage.py monitor_trade_forums --no-details --max-age 48
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.trade_forums import monitor_trade_forums, DEFAULT_FORUMS


class Command(BaseCommand):
    help = 'Scrape trade forums for homeowner service requests in NY/NJ/CT area'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-per-section', type=int, default=20,
            help='Max threads to process per forum section (default: 20)',
        )
        parser.add_argument(
            '--no-details', action='store_true',
            help='Skip fetching full thread details (faster)',
        )
        parser.add_argument(
            '--max-age', type=int, default=72,
            help='Max post age in hours (default: 72)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Trade Forum monitor...'))
        self.stdout.write(f"  Forums: {len(DEFAULT_FORUMS)}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_trade_forums(
            max_per_section=options['max_per_section'],
            fetch_details=not options['no_details'],
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Trade Forum Monitor Results:'))
        self.stdout.write(f"  Threads scraped:   {stats['scraped']}")
        self.stdout.write(f"  Leads created:     {stats['created']}")
        self.stdout.write(f"  Duplicates:        {stats['duplicates']}")
        self.stdout.write(f"  Assignments:       {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:            {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
