"""
Management command to run the Google Business Q&A monitor.
Usage:
    python manage.py monitor_google_qna
    python manage.py monitor_google_qna --dry-run
    python manage.py monitor_google_qna --max-age 168
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.google_qna import monitor_google_qna


class Command(BaseCommand):
    help = 'Monitor Google Business Q&A on tracked competitor listings for lead signals'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age',
            type=int,
            default=168,
            help='Max review/question age in hours (default: 168 = 7 days)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Google Business Q&A monitor...'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE — no leads will be created'))

        stats = monitor_google_qna(
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Google Q&A Monitor Results:'))
        self.stdout.write(f"  Competitors checked: {stats['checked']}")
        self.stdout.write(f"  Leads created:       {stats['created']}")
        self.stdout.write(f"  Duplicates:          {stats['duplicates']}")
        self.stdout.write(f"  Assignments:         {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:              {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
