"""
Management command to run the Apify-based Trustpilot monitor.
Usage:
    python manage.py monitor_trustpilot
    python manage.py monitor_trustpilot --dry-run
    python manage.py monitor_trustpilot --url https://www.trustpilot.com/review/example.com
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.apify_trustpilot import monitor_trustpilot


class Command(BaseCommand):
    help = (
        'Monitor Trustpilot for negative competitor reviews via Apify cloud. '
        'Negative reviews = opportunity signals (customer seeking alternatives). '
        'Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--url', type=str, action='append', dest='urls',
            help='Trustpilot company URL to monitor (can repeat)',
        )
        parser.add_argument(
            '--max-reviews', type=int, default=50,
            help='Max reviews to fetch per company (default: 50)',
        )
        parser.add_argument(
            '--max-age', type=int, default=168,
            help='Skip reviews older than this many hours (default: 168 = 7 days)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Apify Trustpilot monitor...'))
        if options['urls']:
            self.stdout.write(f"  URLs: {len(options['urls'])}")
        else:
            self.stdout.write('  URLs: from business competitor configs')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_trustpilot(
            company_urls=options.get('urls'),
            max_reviews=options['max_reviews'],
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
        self.stdout.write(self.style.SUCCESS('Trustpilot Monitor Results:'))
        self.stdout.write(f"  Reviews scraped:    {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats.get('assigned', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
