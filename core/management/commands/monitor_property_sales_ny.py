"""
Management command to monitor NY property sales records.

Usage:
    python manage.py monitor_property_sales_ny --county nassau --days 30 --dry-run
    python manage.py monitor_property_sales_ny --source nyc --days 30
    python manage.py monitor_property_sales_ny --source suffolk --remote
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.ny_property_sales import monitor_ny_property_sales

SOURCE_CHOICES = ['nyc', 'nassau', 'suffolk']


class Command(BaseCommand):
    help = (
        'Monitor NY property sales records from NYC ACRIS and LI county clerks. '
        'Property transfers signal renovation, new tenants, and service demand.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--source', type=str, default='nyc', choices=SOURCE_CHOICES,
            help='Data source to query (default: nyc)',
        )
        parser.add_argument(
            '--county', type=str, default=None, choices=['nassau', 'suffolk'],
            help='Alias for --source for LI counties',
        )
        parser.add_argument(
            '--days', type=int, default=30,
            help='Look back this many days (default: 30)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )
        parser.add_argument(
            '--remote', action='store_true',
            help='POST leads to remote ingest URL',
        )

    def handle(self, *args, **options):
        # --county overrides --source when provided
        source = options['county'] if options.get('county') else options['source']

        self.stdout.write(self.style.HTTP_INFO('Starting NY Property Sales monitor...'))
        self.stdout.write(f"  Source: {source}")
        self.stdout.write(f"  Days:   {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        stats = monitor_ny_property_sales(
            source=source,
            days=options['days'],
            dry_run=options['dry_run'],
            remote=options['remote'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('NY Property Sales Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
