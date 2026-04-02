"""
Management command to monitor town-level building permits.

Usage:
    python manage.py monitor_town_permits --town hempstead --days 7 --dry-run
    python manage.py monitor_town_permits --all --days 7
    python manage.py monitor_town_permits --town oyster_bay --remote
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.town_permits import monitor_town_permits

TOWN_CHOICES = [
    'hempstead', 'oyster_bay', 'babylon', 'islip',
    'huntington', 'smithtown', 'brookhaven',
]


class Command(BaseCommand):
    help = (
        'Monitor town-level building permit portals for new filings. '
        'Supports individual Long Island towns or all at once.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--town', type=str, default=None, choices=TOWN_CHOICES,
            help='Specific town to monitor',
        )
        parser.add_argument(
            '--all', action='store_true',
            help='Run monitor for all supported towns',
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
        if not options.get('town') and not options['all']:
            self.stderr.write(self.style.ERROR('Specify --town or --all'))
            return

        self.stdout.write(self.style.HTTP_INFO('Starting Town Permits monitor...'))
        if options['all']:
            self.stdout.write(f"  Towns: ALL ({', '.join(TOWN_CHOICES)})")
        else:
            self.stdout.write(f"  Town:  {options['town']}")
        self.stdout.write(f"  Days:  {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        stats = monitor_town_permits(
            town=options.get('town'),
            all_towns=options['all'],
            days=options['days'],
            dry_run=options['dry_run'],
            remote=options['remote'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Town Permits Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
