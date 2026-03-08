"""
Management command to run the Facebook Groups monitor.
Usage:
    python manage.py monitor_facebook
    python manage.py monitor_facebook --dry-run
    python manage.py monitor_facebook --max-total 30 --max-per-group 10
    python manage.py monitor_facebook --group-id 1 --group-id 3

Requirements:
    pip install playwright
    python -m playwright install chromium

Environment variables:
    FACEBOOK_EMAIL    — login email for the dedicated FB account
    FACEBOOK_PASSWORD — login password
"""
from django.core.management.base import BaseCommand

from core.models.monitoring import MonitoredFacebookGroup
from core.utils.monitors.facebook_groups import monitor_facebook_groups


class Command(BaseCommand):
    help = (
        'Scrape Facebook Groups for service request leads using Playwright. '
        'Requires FACEBOOK_EMAIL and FACEBOOK_PASSWORD env vars.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-per-group', type=int, default=15,
            help='Max posts to scrape per group (default: 15)',
        )
        parser.add_argument(
            '--max-total', type=int, default=50,
            help='Max posts across entire session (default: 50)',
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

        self.stdout.write(self.style.HTTP_INFO('Starting Facebook Groups monitor...'))
        self.stdout.write(f"  Groups: {groups.count()}")
        self.stdout.write(f"  Max/group: {options['max_per_group']}")
        self.stdout.write(f"  Max total: {options['max_total']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_facebook_groups(
            group_ids=options.get('group_ids'),
            max_per_group=options['max_per_group'],
            max_total=options['max_total'],
            dry_run=options['dry_run'],
        )

        if 'error' in stats:
            self.stdout.write(self.style.ERROR(f"\n  ERROR: {stats['error']}"))
            if stats['error'] == 'playwright_not_installed':
                self.stdout.write('  Fix: pip install playwright && python -m playwright install chromium')
            elif stats['error'] == 'credentials_not_configured':
                self.stdout.write('  Fix: Set FACEBOOK_EMAIL and FACEBOOK_PASSWORD environment variables')
            elif stats['error'] == 'login_failed':
                self.stdout.write('  Fix: Check credentials; delete .fb_cookies.json and retry')
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Facebook Groups Monitor Results:'))
        self.stdout.write(f"  Groups checked:    {stats['groups_checked']}")
        self.stdout.write(f"  Posts scraped:      {stats['posts_scraped']}")
        self.stdout.write(f"  Leads created:      {stats['created']}")
        self.stdout.write(f"  Duplicates:         {stats['duplicates']}")
        self.stdout.write(f"  Assignments:        {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:             {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
