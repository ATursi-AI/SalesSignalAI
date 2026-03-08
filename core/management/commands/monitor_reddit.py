"""
Management command to run the Reddit local subreddit monitor.
Usage:
    python manage.py monitor_reddit
    python manage.py monitor_reddit --subreddits longisland asknyc
    python manage.py monitor_reddit --sort hot --limit 100
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.reddit_local import (
    monitor_reddit,
    DEFAULT_SUBREDDITS,
)


class Command(BaseCommand):
    help = 'Monitor local Reddit subreddits for service request leads'

    def add_arguments(self, parser):
        parser.add_argument(
            '--subreddits',
            nargs='+',
            default=None,
            help=f'Subreddits to scan (default: {len(DEFAULT_SUBREDDITS)} local subs)',
        )
        parser.add_argument(
            '--sort',
            choices=['new', 'hot'],
            default='new',
            help='Sort order (default: new)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Max posts to scan per subreddit (default: 50)',
        )
        parser.add_argument(
            '--max-age',
            type=int,
            default=48,
            help='Max post age in hours (default: 48)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Reddit monitor...'))

        subreddits = options['subreddits']
        if subreddits:
            self.stdout.write(f"  Subreddits: {', '.join(subreddits)}")
        else:
            self.stdout.write(f"  Subreddits: {len(DEFAULT_SUBREDDITS)} default local subs")

        self.stdout.write(f"  Sort: {options['sort']}, Limit: {options['limit']}/sub, Max age: {options['max_age']}h")

        stats = monitor_reddit(
            subreddits=subreddits,
            sort=options['sort'],
            limit=options['limit'],
            max_age_hours=options['max_age'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Reddit Monitor Results:'))
        self.stdout.write(f"  Posts scanned:    {stats['scanned']}")
        self.stdout.write(f"  Leads created:    {stats['created']}")
        self.stdout.write(f"  Duplicates:       {stats['duplicates']}")
        self.stdout.write(f"  Assignments:      {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:           {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
