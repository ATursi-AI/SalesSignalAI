"""
Management command to monitor NYC property sales via ACRIS SODA API.

NOTE: ACRIS data has a ~3-4 week recording delay. Use --days 45+ to ensure
you capture recent recordings. --days 14 will likely return 0 results.

Usage:
    python manage.py monitor_property_sales_ny --days 45 --dry-run
    python manage.py monitor_property_sales_ny --borough manhattan
    python manage.py monitor_property_sales_ny --days 60
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.ny_property_sales import monitor_ny_property_sales


class Command(BaseCommand):
    help = (
        'Monitor NYC property sales via ACRIS SODA API. '
        'Property transfers signal renovation, new tenants, and service demand.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--borough', type=str, default=None,
            help='Filter by borough (manhattan/bronx/brooklyn/queens/staten_island)',
        )
        parser.add_argument(
            '--days', type=int, default=45,
            help='Look back this many days (default: 45 — ACRIS has ~3-4 week lag)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting NYC Property Sales monitor...'))
        self.stdout.write(f"  Source: ACRIS SODA API (4 endpoints)")
        self.stdout.write(f"  Borough: {options['borough'] or 'all'}")
        self.stdout.write(f"  Days:   {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_ny_property_sales(
            days=options['days'],
            borough=options['borough'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('NYC Property Sales Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
