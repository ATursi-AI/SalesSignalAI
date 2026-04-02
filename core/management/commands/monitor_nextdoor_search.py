"""
Management command to run the Nextdoor SEARCH-based monitor.

Searches Nextdoor for service-request keywords instead of scrolling the feed.
Filters for request posts only, scores confidence, deduplicates by poster+date.

Usage:
    python manage.py monitor_nextdoor_search --days 7 --dry-run
    python manage.py monitor_nextdoor_search --keywords "plumber,electrician"
    python manage.py monitor_nextdoor_search --remote
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.nextdoor_search import (
    monitor_nextdoor_search,
    DEFAULT_KEYWORDS,
)


class Command(BaseCommand):
    help = 'Search Nextdoor for service-request posts using Playwright'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Only keep posts from the last N days (default: 7)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show matches without creating leads',
        )
        parser.add_argument(
            '--remote',
            action='store_true',
            help='POST leads to REMOTE_INGEST_URL instead of saving locally',
        )
        parser.add_argument(
            '--keywords',
            type=str,
            default=None,
            help='Comma-separated keywords to search (default: all 24 built-in)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Bypass the cooldown timer (for testing)',
        )
        parser.add_argument(
            '--headed',
            action='store_true',
            help='Launch visible browser for debugging (not headless)',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        remote = options['remote']
        force = options['force']
        headed = options['headed']

        # Parse comma-separated keywords
        keywords = None
        if options['keywords']:
            keywords = [k.strip() for k in options['keywords'].split(',') if k.strip()]

        self.stdout.write(self.style.HTTP_INFO('Starting Nextdoor Search monitor...'))
        mode = 'HEADED (visible)' if headed else 'headless'
        self.stdout.write(f'  Engine: Playwright {mode} Chromium')
        self.stdout.write(f'  Days: {days}')
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — no leads will be created'))
        if force:
            self.stdout.write(self.style.WARNING('  FORCE — cooldown bypassed'))
        if remote:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))

        kw_list = keywords or DEFAULT_KEYWORDS
        self.stdout.write(f'  Keywords: {len(kw_list)} ({", ".join(kw_list[:4])}...)')
        self.stdout.write('')

        stats = monitor_nextdoor_search(
            keywords=keywords,
            days=days,
            dry_run=dry_run,
            remote=remote,
            force=force,
            headed=headed,
        )

        # Check for early exit
        if stats.get('skipped_reason'):
            self.stdout.write(self.style.WARNING(f'  Skipped: {stats["skipped_reason"]}'))
            self.stdout.write(self.style.SUCCESS('Done.'))
            return

        if stats.get('error'):
            self.stdout.write(self.style.ERROR(f'  Error: {stats["error"]}'))
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Nextdoor Search Monitor Results:'))
        self.stdout.write(f'  Keywords searched:  {stats.get("keywords_searched", 0)}')
        self.stdout.write(f'  Posts found:        {stats.get("posts_found", 0)}')
        self.stdout.write(f'  Request posts:      {stats.get("requests_found", 0)}')

        if dry_run:
            self.stdout.write(f'  Would create:       {stats.get("created", 0)}')
        elif remote:
            self.stdout.write(f'  Remote sent:        {stats.get("remote_sent", 0)}')
            self.stdout.write(f'  Duplicates:         {stats.get("duplicates", 0)}')
            if stats.get('remote_failed'):
                self.stdout.write(self.style.WARNING(
                    f'  Remote failed:      {stats["remote_failed"]}'
                ))
        else:
            self.stdout.write(f'  Leads created:      {stats.get("created", 0)}')
            self.stdout.write(f'  Duplicates:         {stats.get("duplicates", 0)}')
            self.stdout.write(f'  Assignments:        {stats.get("assigned", 0)}')

        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f'  Errors:             {stats["errors"]}'))

        self.stdout.write(self.style.SUCCESS('Done.'))
