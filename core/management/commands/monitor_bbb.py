"""
Management command to run the BBB complaint monitor.
Usage:
    python manage.py monitor_bbb
    python manage.py monitor_bbb --dry-run
    python manage.py monitor_bbb --url https://www.bbb.org/us/ny/new-york/profile/plumber/example-0121-12345
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.bbb import monitor_bbb


class Command(BaseCommand):
    help = (
        'Monitor BBB.org for competitor complaints and negative reviews. '
        'Negative complaints = opportunity signals (customer seeking alternatives). '
        'Configure competitor BBB URLs via BusinessProfile.raw_data.bbb_competitors.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--url', type=str, action='append', dest='urls',
            help='BBB profile URL to monitor (can repeat)',
        )
        parser.add_argument(
            '--max-age', type=int, default=30,
            help='Skip complaints older than this many days (default: 30)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting BBB Complaint monitor...'))
        if options['urls']:
            self.stdout.write(f"  URLs: {len(options['urls'])}")
        else:
            self.stdout.write('  URLs: from business competitor configs')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_bbb(
            bbb_urls=options.get('urls'),
            max_age_days=options['max_age'],
            dry_run=options['dry_run'],
        )

        if 'skipped_reason' in stats:
            self.stdout.write(self.style.WARNING(f"  Skipped: {stats['skipped_reason']}"))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('BBB Monitor Results:'))
        self.stdout.write(f"  Complaints scraped: {stats['items_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats.get('assigned', 0)}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
