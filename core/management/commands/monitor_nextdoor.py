"""
Management command to run the Nextdoor Playwright monitor.

Uses headless Chromium to scrape Nextdoor for service request posts.
Supports cookie persistence, anti-detection, feed scrolling, and keyword search.

Usage:
    python manage.py monitor_nextdoor
    python manage.py monitor_nextdoor --dry-run
    python manage.py monitor_nextdoor --max-posts 10
    python manage.py monitor_nextdoor --remote
    python manage.py monitor_nextdoor --search-terms "need plumber" "recommend electrician"
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.nextdoor_playwright import (
    monitor_nextdoor_playwright,
    DEFAULT_SEARCH_TERMS,
)


class Command(BaseCommand):
    help = 'Monitor Nextdoor for service leads using Playwright headless browser'

    def add_arguments(self, parser):
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
            '--max-posts',
            type=int,
            default=20,
            help='Maximum posts to extract per run (default: 20)',
        )
        parser.add_argument(
            '--search-terms',
            nargs='+',
            default=None,
            help='Custom search terms (default: built-in service keywords)',
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
        dry_run = options['dry_run']
        remote = options['remote']
        max_posts = options['max_posts']
        search_terms = options['search_terms']
        force = options['force']
        headed = options['headed']

        self.stdout.write(self.style.HTTP_INFO('Starting Nextdoor Playwright monitor...'))
        mode = 'HEADED (visible)' if headed else 'headless'
        self.stdout.write(f'  Engine: Playwright {mode} Chromium')
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — no leads will be created'))
        if force:
            self.stdout.write(self.style.WARNING('  FORCE — cooldown bypassed'))
        if remote:
            from django.conf import settings as django_settings
            self.stdout.write(self.style.WARNING(
                f'  REMOTE MODE — posting to {django_settings.REMOTE_INGEST_URL}'
            ))
        self.stdout.write(f'  Max posts: {max_posts}')
        terms = search_terms or DEFAULT_SEARCH_TERMS
        self.stdout.write(f'  Search terms: {len(terms)} ({", ".join(terms[:3])}...)')
        self.stdout.write('')

        stats = monitor_nextdoor_playwright(
            search_terms=search_terms,
            max_posts=max_posts,
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
        self.stdout.write(self.style.SUCCESS('Nextdoor Playwright Monitor Results:'))
        self.stdout.write(f'  Posts found:       {stats["posts_found"]}')
        self.stdout.write(f'  Service matches:   {stats["service_matches"]}')

        if dry_run:
            self.stdout.write(f'  Would create:      {stats["created"]}')
        elif remote:
            self.stdout.write(f'  Remote sent:       {stats["remote_sent"]}')
            self.stdout.write(f'  Duplicates:        {stats["duplicates"]}')
            if stats.get('remote_failed'):
                self.stdout.write(self.style.WARNING(
                    f'  Remote failed:     {stats["remote_failed"]}'
                ))
        else:
            self.stdout.write(f'  Leads created:     {stats["created"]}')
            self.stdout.write(f'  Duplicates:        {stats["duplicates"]}')
            self.stdout.write(f'  Assignments:       {stats["assigned"]}')

        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f'  Errors:            {stats["errors"]}'))

        self.stdout.write(self.style.SUCCESS('Done.'))
