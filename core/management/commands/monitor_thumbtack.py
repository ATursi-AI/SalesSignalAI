"""
Management command to run the Thumbtack monitor.
Usage:
    python manage.py monitor_thumbtack
    python manage.py monitor_thumbtack --dry-run
    python manage.py monitor_thumbtack --max-per-combo 10
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.thumbtack import (
    monitor_thumbtack, DEFAULT_SERVICES, DEFAULT_LOCATIONS,
)


class Command(BaseCommand):
    help = 'Scrape Thumbtack for ultra-high-intent service project leads'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-per-combo', type=int, default=15,
            help='Max listings per service+location combo (default: 15)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Thumbtack monitor...'))
        self.stdout.write(f"  Services: {len(DEFAULT_SERVICES)}")
        self.stdout.write(f"  Locations: {len(DEFAULT_LOCATIONS)}")
        self.stdout.write(f"  Combinations: {len(DEFAULT_SERVICES) * len(DEFAULT_LOCATIONS)}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_thumbtack(
            max_per_combo=options['max_per_combo'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Thumbtack Monitor Results:'))
        self.stdout.write(f"  Listings scraped:  {stats['scraped']}")
        self.stdout.write(f"  Leads created:     {stats['created']}")
        self.stdout.write(f"  Duplicates:        {stats['duplicates']}")
        self.stdout.write(f"  Assignments:       {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:            {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
