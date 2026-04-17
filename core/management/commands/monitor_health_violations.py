"""
Management command to monitor NY health violations.

Usage:
    python manage.py monitor_health_violations --source nyc --borough manhattan --days 30
    python manage.py monitor_health_violations --source nyc --days 14 --dry-run
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.ny_health_violations import monitor_ny_health_violations

SOURCE_CHOICES = ['nyc', 'nassau', 'suffolk']
BOROUGH_CHOICES = ['manhattan', 'brooklyn', 'queens', 'bronx', 'staten_island']


class Command(BaseCommand):
    help = (
        'Monitor health department violation records across NYC and LI counties. '
        'Health violations create urgent demand for cleaning, pest control, and plumbing.'
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
            '--borough', type=str, default=None, choices=BOROUGH_CHOICES,
            help='Filter NYC results by borough (nyc source only)',
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
            help='POST leads to remote ingest URL (not yet implemented)',
        )

    def handle(self, *args, **options):
        source = options['county'] if options.get('county') else options['source']
        borough = options.get('borough')

        self.stdout.write(self.style.HTTP_INFO('Starting Health Violations monitor...'))
        self.stdout.write(f"  Source:  {source}")
        if borough:
            self.stdout.write(f"  Borough: {borough}")
        self.stdout.write(f"  Days:    {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            self.stdout.write(self.style.WARNING(
                '  --remote requested but underlying monitor does not yet support it; ignoring.'
            ))

        if source != 'nyc' and borough:
            self.stdout.write(self.style.WARNING(
                '  --borough only applies to --source nyc; ignoring borough filter.'
            ))
            borough = None

        stats = monitor_ny_health_violations(
            days=options['days'],
            borough=borough,
            dry_run=options['dry_run'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Health Violations Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
