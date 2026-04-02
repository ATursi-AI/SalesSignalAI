"""
Management command to run the Apify-based Facebook Groups monitor.
Usage:
    python manage.py monitor_facebook_apify
    python manage.py monitor_facebook_apify --dry-run
    python manage.py monitor_facebook_apify --group-id 1 --group-id 3
    python manage.py monitor_facebook_apify --max-posts 30
"""
from django.core.management.base import BaseCommand

from core.models.monitoring import MonitoredFacebookGroup
from core.utils.monitors.apify_facebook import monitor_facebook_apify


class Command(BaseCommand):
    help = (
        'Scrape Facebook Groups for service request leads via Apify cloud. '
        'Requires APIFY_API_TOKEN in settings.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-posts', type=int, default=20,
            help='Max posts per group (default: 20)',
        )
        parser.add_argument(
            '--max-age', type=int, default=48,
            help='Skip posts older than this many hours (default: 48)',
        )
        parser.add_argument(
            '--group-id', type=int, action='append', dest='group_ids',
            help='Only monitor specific group IDs (can repeat)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        groups = MonitoredFacebookGroup.objects.filter(is_active=True)
        if options['group_ids']:
            groups = groups.filter(id__in=options['group_ids'])

        self.stdout.write(self.style.HTTP_INFO('Starting Apify Facebook Groups monitor...'))
        self.stdout.write(f"  Groups: {groups.count()}")
        self.stdout.write(f"  Max posts/group: {options['max_posts']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_facebook_apify(
            group_ids=options.get('group_ids'),
            max_posts_per_group=options['max_posts'],
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
        self.stdout.write(self.style.SUCCESS('Apify Facebook Monitor Results:'))
        self.stdout.write(f"  Groups checked:    {stats['groups_checked']}")
        self.stdout.write(f"  Posts scraped:      {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
