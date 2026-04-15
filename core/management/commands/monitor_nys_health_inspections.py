"""
Management command to monitor NY State health inspections (outside NYC).

Usage:
    python manage.py monitor_nys_health_inspections --days 30
    python manage.py monitor_nys_health_inspections --county Nassau --dry-run
    python manage.py monitor_nys_health_inspections --county "Westchester,Rockland,Orange" --days 14
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.nys_health_inspections import monitor_nys_health_inspections


class Command(BaseCommand):
    help = (
        'Monitor NY State food service inspections outside NYC via health.data.ny.gov. '
        'Covers Nassau, Westchester, Rockland, Orange, Dutchess, Albany, and more. '
        'Includes operator name and corporation name for contact enrichment.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--county', type=str, default=None,
            help='County name or comma-separated list (default: all target counties)',
        )
        parser.add_argument(
            '--days', type=int, default=30,
            help='Look back this many days (default: 30)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO(
            'Starting NY State Health Inspections monitor...'
        ))
        self.stdout.write(f'  County: {options["county"] or "all defaults"}')
        self.stdout.write(f'  Days:   {options["days"]}')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_nys_health_inspections(
            county=options.get('county'),
            days=options['days'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('NYS Health Inspections Results:'))
        self.stdout.write(f'  Sources checked:    {stats["sources_checked"]}')
        self.stdout.write(f'  Items scraped:      {stats["items_scraped"]}')
        self.stdout.write(f'  Leads created:      {stats["created"]}')
        self.stdout.write(f'  Duplicates:         {stats["duplicates"]}')
        self.stdout.write(f'  Assignments:        {stats["assigned"]}')
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f'  Errors:             {stats["errors"]}'))
        self.stdout.write(self.style.SUCCESS('Done.'))
