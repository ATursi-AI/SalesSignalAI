"""
Management command to monitor NYC Department of Buildings data.

Usage:
    python manage.py monitor_nyc_dob --type permits --borough manhattan --days 7 --dry-run
    python manage.py monitor_nyc_dob --type violations --borough brooklyn --days 7
    python manage.py monitor_nyc_dob --type certificates --days 30
    python manage.py monitor_nyc_dob --type permits --remote
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.nyc_dob import monitor_nyc_dob

TYPE_CHOICES = ['permits', 'violations', 'certificates']
BOROUGH_CHOICES = ['manhattan', 'brooklyn', 'queens', 'bronx', 'staten_island']


class Command(BaseCommand):
    help = (
        'Monitor NYC Department of Buildings for permits, violations, '
        'and certificates of occupancy.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--type', type=str, required=True, choices=TYPE_CHOICES,
            help='Type of DOB record to monitor (permits, violations, certificates)',
        )
        parser.add_argument(
            '--borough', type=str, default=None, choices=BOROUGH_CHOICES,
            help='Filter by borough (optional)',
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
        self.stdout.write(self.style.HTTP_INFO('Starting NYC DOB monitor...'))
        self.stdout.write(f"  Type:    {options['type']}")
        if options.get('borough'):
            self.stdout.write(f"  Borough: {options['borough']}")
        self.stdout.write(f"  Days:    {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        stats = monitor_nyc_dob(
            monitor_type=options['type'],
            borough=options.get('borough'),
            days=options['days'],
            dry_run=options['dry_run'],
            remote=options['remote'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('NYC DOB Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
