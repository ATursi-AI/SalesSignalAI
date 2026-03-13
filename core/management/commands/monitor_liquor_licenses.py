"""
Management command to monitor NY liquor license applications.

Usage:
    python manage.py monitor_liquor_licenses --county nassau --days 30 --dry-run
    python manage.py monitor_liquor_licenses --county suffolk --remote
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.ny_liquor_licenses import monitor_ny_liquor_licenses


class Command(BaseCommand):
    help = (
        'Monitor NY State Liquor Authority for new license applications. '
        'New liquor licenses signal bar/restaurant openings needing buildout services.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--county', type=str, default='nassau',
            help='County to search (default: nassau)',
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
        self.stdout.write(self.style.HTTP_INFO('Starting Liquor Licenses monitor...'))
        self.stdout.write(f"  County: {options['county']}")
        self.stdout.write(f"  Days:   {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        stats = monitor_ny_liquor_licenses(
            county=options['county'],
            days=options['days'],
            dry_run=options['dry_run'],
            remote=options['remote'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Liquor Licenses Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
