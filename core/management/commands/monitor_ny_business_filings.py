"""
Management command to monitor NY business filings.

Usage:
    python manage.py monitor_ny_business_filings --county nassau --days 7 --dry-run
    python manage.py monitor_ny_business_filings --county suffolk --days 14
    python manage.py monitor_ny_business_filings --remote
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.ny_business_filings import monitor_ny_business_filings


class Command(BaseCommand):
    help = (
        'Monitor NY Secretary of State business filings for new incorporations, '
        'LLCs, and DBAs that signal new business openings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--county', type=str, default='nassau',
            help='County to search (default: nassau)',
        )
        parser.add_argument(
            '--days', type=int, default=7,
            help='Look back this many days (default: 7)',
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
        self.stdout.write(self.style.HTTP_INFO('Starting NY Business Filings monitor...'))
        self.stdout.write(f"  County: {options['county']}")
        self.stdout.write(f"  Days:   {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        stats = monitor_ny_business_filings(
            county=options['county'],
            days=options['days'],
            dry_run=options['dry_run'],
            remote=options['remote'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

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
