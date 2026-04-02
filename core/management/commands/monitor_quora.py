"""
Management command to run the Apify-based Quora monitor.
Usage:
    python manage.py monitor_quora
    python manage.py monitor_quora --dry-run
    python manage.py monitor_quora --max-questions 100
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.apify_quora import monitor_quora


class Command(BaseCommand):
    help = (
        'Search Quora for service recommendation questions via Apify cloud. '
        'Very high intent — people asking "best plumber in [city]." '
        'Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-questions', type=int, default=50,
            help='Max questions to fetch (default: 50)',
        )
        parser.add_argument(
            '--max-age', type=int, default=168,
            help='Skip questions older than this many hours (default: 168 = 7 days)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Apify Quora monitor...'))
        self.stdout.write(f"  Max questions: {options['max_questions']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_quora(
            max_questions=options['max_questions'],
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        if 'error' in stats:
            self.stdout.write(self.style.ERROR(f"\n  ERROR: {stats['error']}"))
            if stats['error'] == 'api_not_configured':
                self.stdout.write('  Fix: Set APIFY_API_TOKEN in .env')
            return

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Quora Monitor Results:'))
        self.stdout.write(f"  Questions scraped:  {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats.get('assigned', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
