"""
Management command to monitor NYC facade inspections (Local Law 11/FISP).

Usage:
    python manage.py monitor_facade_inspections --borough manhattan --dry-run
    python manage.py monitor_facade_inspections --borough brooklyn --remote
    python manage.py monitor_facade_inspections --dry-run
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.nyc_facade_inspections import monitor_nyc_facade_inspections

BOROUGH_CHOICES = ['manhattan', 'brooklyn', 'queens', 'bronx', 'staten_island']


class Command(BaseCommand):
    help = (
        'Monitor NYC facade inspection (FISP/Local Law 11) filings. '
        'Buildings with unsafe facades need scaffolding, masonry, and restoration work.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--borough', type=str, default=None, choices=BOROUGH_CHOICES,
            help='Filter by borough (optional)',
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
        self.stdout.write(self.style.HTTP_INFO('Starting Facade Inspections monitor...'))
        if options.get('borough'):
            self.stdout.write(f"  Borough: {options['borough']}")
        else:
            self.stdout.write('  Borough: ALL')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        if options['remote']:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        stats = monitor_nyc_facade_inspections(
            borough=options.get('borough'),
            dry_run=options['dry_run'],
            remote=options['remote'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Facade Inspections Monitor Results:'))
        self.stdout.write(f"  Sources checked:    {stats['sources_checked']}")
        self.stdout.write(f"  Items scraped:      {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
