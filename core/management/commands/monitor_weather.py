"""
Management command to run the NOAA weather alert monitor.
Usage:
    python manage.py monitor_weather
    python manage.py monitor_weather --dry-run
    python manage.py monitor_weather --state NY --state FL
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.weather import monitor_weather


class Command(BaseCommand):
    help = (
        'Check NOAA National Weather Service for active severe weather alerts. '
        'Dynamically monitors states where active businesses operate. '
        'Free API — no key needed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--state', type=str, action='append', dest='states',
            help='Only check specific state codes (can repeat, e.g. --state NY --state FL)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting NOAA Weather Alert monitor...'))
        if options['states']:
            self.stdout.write(f"  States: {', '.join(options['states'])}")
        else:
            self.stdout.write('  States: auto-detect from active businesses')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_weather(
            states=options.get('states'),
            dry_run=options['dry_run'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Weather Alert Monitor Results:'))
        self.stdout.write(f"  States checked:     {stats['states_checked']}")
        self.stdout.write(f"  Alerts found:       {stats['alerts_found']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats.get('assigned', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
