"""
Management command to run the Porch.com review monitor.
Usage:
    python manage.py monitor_porch
    python manage.py monitor_porch --dry-run
    python manage.py monitor_porch --max-age 168
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.porch_reviews import monitor_porch_reviews


class Command(BaseCommand):
    help = 'Monitor Porch.com reviews on tracked competitors for opportunity leads'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age', type=int, default=168,
            help='Max review age in hours (default: 168 = 7 days)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Porch review monitor...'))
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_porch_reviews(
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Porch Review Monitor Results:'))
        self.stdout.write(f"  Competitors checked: {stats['checked']}")
        self.stdout.write(f"  Reviews found:       {stats['reviews_found']}")
        self.stdout.write(f"  Opportunities:       {stats['opportunities']}")
        self.stdout.write(f"  Leads created:       {stats['created']}")
        self.stdout.write(f"  Duplicates:          {stats['duplicates']}")
        self.stdout.write(f"  Assignments:         {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:              {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
