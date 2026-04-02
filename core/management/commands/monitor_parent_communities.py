"""
Management command to run the Parent Community monitor.
Usage:
    python manage.py monitor_parent_communities
    python manage.py monitor_parent_communities --dry-run
    python manage.py monitor_parent_communities --no-details --max-age 48
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.parent_communities import (
    monitor_parent_communities, DEFAULT_COMMUNITIES,
)


class Command(BaseCommand):
    help = 'Scrape parent community forums for service recommendation requests'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-per-community', type=int, default=20,
            help='Max threads to process per community (default: 20)',
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
        self.stdout.write(self.style.HTTP_INFO('Starting Parent Community monitor...'))
        self.stdout.write(f"  Communities: {len(DEFAULT_COMMUNITIES)}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_parent_communities(
            max_per_community=options['max_per_community'],
            fetch_details=not options['no_details'],
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Parent Community Monitor Results:'))
        self.stdout.write(f"  Threads scraped:   {stats['scraped']}")
        self.stdout.write(f"  Leads created:     {stats['created']}")
        self.stdout.write(f"  Duplicates:        {stats['duplicates']}")
        self.stdout.write(f"  Assignments:       {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:            {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
