"""
Management command to run the license expirations monitor.
Usage:
    python manage.py monitor_license_expirations
    python manage.py monitor_license_expirations --dry-run
    python manage.py monitor_license_expirations --max-age 60
    python manage.py monitor_license_expirations --source-id 1
"""
from django.core.management.base import BaseCommand

from core.models import LicensingBoardSource
from core.utils.monitors.license_expirations import monitor_license_expirations


class Command(BaseCommand):
    help = (
        'Scrape state licensing board databases for expired/suspended contractor licenses. '
        'Competitive intelligence — expired competitor = their customers need a new provider. '
        'Configure sources via LicensingBoardSource model in admin.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age', type=int, default=90,
            help='Skip expirations older than this many days (default: 90)',
        )
        parser.add_argument(
            '--source-id', type=int, action='append', dest='source_ids',
            help='Only scrape specific source IDs (can repeat)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        sources = LicensingBoardSource.objects.filter(is_active=True)
        if options['source_ids']:
            sources = sources.filter(id__in=options['source_ids'])

        self.stdout.write(self.style.HTTP_INFO('Starting License Expirations monitor...'))
        self.stdout.write(f"  Active sources: {sources.count()}")
        self.stdout.write(f"  Max age: {options['max_age']} days")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_license_expirations(
            source_ids=options.get('source_ids'),
            max_age_days=options['max_age'],
            dry_run=options['dry_run'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('License Expirations Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Licenses scraped:   {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
