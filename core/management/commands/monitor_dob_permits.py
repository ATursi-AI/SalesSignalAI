"""
Management command to monitor DOB NOW: Build – Approved Permits.

Usage:
    python manage.py monitor_dob_permits --borough queens --days 30 --dry-run
    python manage.py monitor_dob_permits --borough brooklyn --min-cost 50000
    python manage.py monitor_dob_permits --remote
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.dob_permits_now import monitor_dob_permits_now


class Command(BaseCommand):
    help = (
        'Monitor DOB NOW: Build – Approved Permits (rbx6-tga4). '
        'New construction permits signal upcoming work needing contractors.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--borough', type=str, default=None,
            help='Borough filter (manhattan/bronx/brooklyn/queens/staten_island)',
        )
        parser.add_argument(
            '--days', type=int, default=30,
            help='Look back this many days (default: 30)',
        )
        parser.add_argument(
            '--min-cost', type=int, default=0,
            help='Minimum estimated job cost to include (default: 0)',
        )
        parser.add_argument(
            '--work-type', type=str, default=None,
            help='Filter by specific work type (e.g. Structural, Plumbing)',
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
        self.stdout.write(self.style.HTTP_INFO(
            'Starting DOB NOW Permits monitor...'
        ))
        self.stdout.write(f"  Borough:  {options['borough'] or 'all'}")
        self.stdout.write(f"  Days:     {options['days']}")
        if options['min_cost']:
            self.stdout.write(f"  Min cost: ${options['min_cost']:,}")
        if options['work_type']:
            self.stdout.write(f"  Work type: {options['work_type']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        stats = monitor_dob_permits_now(
            borough=options['borough'],
            days=options['days'],
            dry_run=options['dry_run'],
            remote=options['remote'],
            min_cost=options['min_cost'],
            work_type=options['work_type'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(
                f"  Skipped: {stats['skipped_reason']}"
            ))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            'DOB NOW Permits Monitor Results:'
        ))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(
                f"  Errors:             {stats['errors']}"
            ))
        self.stdout.write(self.style.SUCCESS('Done.'))
