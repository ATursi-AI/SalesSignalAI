"""
Management command to monitor NY business filings via SODA API.

Usage:
    python manage.py monitor_ny_business_filings --days 30 --dry-run
    python manage.py monitor_ny_business_filings --county "NEW YORK,KINGS" --days 14
    python manage.py monitor_ny_business_filings --county all --days 60
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.ny_business_filings import monitor_ny_business_filings


class Command(BaseCommand):
    help = (
        'Monitor NY Department of State business filings via SODA API. '
        'New incorporations, LLCs, and foreign authority applications signal new business openings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--county', type=str, default=None,
            help='County filter — single name, comma-separated list, or "all" (default: NYC + Nassau + Suffolk)',
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
        self.stdout.write(self.style.HTTP_INFO('Starting NY Business Filings monitor...'))
        self.stdout.write(f"  Source: NY DOS SODA API (k4vb-judh)")
        self.stdout.write(f"  County: {options['county'] or 'default (NYC + LI)'}")
        self.stdout.write(f"  Days:   {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_ny_business_filings(
            county=options['county'],
            days=options['days'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('NY Business Filings Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
