"""
Management command to run the BiggerPockets forum monitor.
Usage:
    python manage.py monitor_biggerpockets
    python manage.py monitor_biggerpockets --dry-run
    python manage.py monitor_biggerpockets --no-details --max-age 72
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.biggerpockets import monitor_biggerpockets, DEFAULT_FORUMS


class Command(BaseCommand):
    help = 'Scrape BiggerPockets forums for B2B service leads from landlords/property managers'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-per-forum', type=int, default=25,
            help='Max threads to process per forum (default: 25)',
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
        self.stdout.write(self.style.HTTP_INFO('Starting BiggerPockets forum monitor...'))
        self.stdout.write(f"  Forums: {len(DEFAULT_FORUMS)}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_biggerpockets(
            max_per_forum=options['max_per_forum'],
            fetch_details=not options['no_details'],
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('BiggerPockets Monitor Results:'))
        self.stdout.write(f"  Threads scraped:   {stats['scraped']}")
        self.stdout.write(f"  Leads created:     {stats['created']}")
        self.stdout.write(f"  Duplicates:        {stats['duplicates']}")
        self.stdout.write(f"  Assignments:       {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:            {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
