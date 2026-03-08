"""
Management command to run the commercial eviction filings monitor.
Usage:
    python manage.py monitor_evictions
    python manage.py monitor_evictions --dry-run
    python manage.py monitor_evictions --max-age 14
    python manage.py monitor_evictions --source-id 1
"""
from django.core.management.base import BaseCommand

from core.models import CourtRecordSource
from core.utils.monitors.eviction_filings import monitor_evictions


class Command(BaseCommand):
    help = (
        'Scrape county court records for commercial eviction filings. '
        'Only commercial — residential evictions are excluded for ethical reasons. '
        'Configure sources via CourtRecordSource model in admin.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age', type=int, default=30,
            help='Skip filings older than this many days (default: 30)',
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
        sources = CourtRecordSource.objects.filter(is_active=True)
        if options['source_ids']:
            sources = sources.filter(id__in=options['source_ids'])

        self.stdout.write(self.style.HTTP_INFO('Starting Commercial Eviction Filings monitor...'))
        self.stdout.write(f"  Active sources: {sources.count()}")
        self.stdout.write(f"  Max age: {options['max_age']} days")
        self.stdout.write(self.style.WARNING('  NOTE: Only commercial evictions — residential excluded'))
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_evictions(
            source_ids=options.get('source_ids'),
            max_age_days=options['max_age'],
            dry_run=options['dry_run'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Eviction Filings Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Filings scraped:    {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        self.stdout.write(f"  Residential skipped:{stats.get('residential_skipped', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
