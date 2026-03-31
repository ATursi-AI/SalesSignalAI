"""
Management command to run the Reddit monitor.
Uses Reddit's public JSON endpoints — no API key required.

Usage:
    python manage.py monitor_reddit
    python manage.py monitor_reddit --state CA --dry-run
    python manage.py monitor_reddit --state NY --remote
    python manage.py monitor_reddit --subreddits AskNYC HomeImprovement
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.reddit_json import monitor_reddit, DEFAULT_SUBREDDITS


class Command(BaseCommand):
    help = 'Monitor Reddit for service leads using public JSON endpoints'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show matches without creating leads')
        parser.add_argument('--subreddits', nargs='+', default=None,
                            help=f'Subreddits to scan (default: auto based on --state)')
        parser.add_argument('--max-age-hours', type=int, default=48, help='Max post age in hours (default: 48)')
        parser.add_argument('--remote', action='store_true', help='POST leads to REMOTE_INGEST_URL')
        parser.add_argument('--state', type=str, choices=['NY', 'CA', 'ALL'], default='ALL',
                            help='Filter by state: NY, CA, or ALL (default: ALL)')
        parser.add_argument('--use-apify', action='store_true', help='Use Apify as fallback when Reddit rate limits')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        subreddits = options['subreddits']
        max_age = options['max_age_hours']
        remote = options['remote']
        state = options['state']
        use_apify = options['use_apify']

        self.stdout.write(self.style.HTTP_INFO('Starting Reddit monitor...'))
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN'))
        if remote:
            from django.conf import settings as s
            self.stdout.write(self.style.WARNING(f'  REMOTE MODE -> {s.REMOTE_INGEST_URL}'))
        self.stdout.write(f'  State: {state}')
        if subreddits:
            self.stdout.write(f'  Subreddits: {", ".join(subreddits)}')
        self.stdout.write(f'  Max age: {max_age}h')
        self.stdout.write('')

        stats = monitor_reddit(
            subreddits=subreddits,
            max_age_hours=max_age,
            dry_run=dry_run,
            remote=remote,
            state=state,
            use_apify=use_apify,
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Reddit Monitor Results:'))
        self.stdout.write(f'  Posts scanned:   {stats["scraped"]}')
        self.stdout.write(f'  Geo-filtered:    {stats.get("geo_filtered", 0)}')
        self.stdout.write(f'  Intent-filtered: {stats.get("intent_filtered", 0)}')
        self.stdout.write(f'  Keyword matches: {stats["matched"]}')

        if dry_run:
            matches = stats.get('dry_run_matches', [])
            if matches:
                self.stdout.write('')
                self.stdout.write(self.style.HTTP_INFO(f'  === {len(matches)} MATCHES ==='))
                self.stdout.write('')
                for m in matches:
                    safe_title = m['title'].encode('ascii', 'replace').decode('ascii')
                    self.stdout.write(f'  [{m["subreddit"]}] {safe_title}')
                    self.stdout.write(
                        f'    {m["category"]} | {", ".join(m["keywords"])} | '
                        f'{m.get("confidence", "?").upper()} | '
                        f'State: {m.get("state", "?")} | '
                        f'Urgency: {m.get("urgency", "?")} | '
                        f'{m["age_hours"]}h | u/{m["author"]}'
                    )
                    self.stdout.write(f'    {m["url"]}')
                    self.stdout.write('')
            else:
                self.stdout.write(self.style.WARNING('  No matches found.'))
        elif remote:
            self.stdout.write(f'  Remote sent:     {stats.get("remote_sent", 0)}')
            self.stdout.write(f'  Duplicates:      {stats["duplicates"]}')
            if stats.get('remote_failed'):
                self.stdout.write(self.style.WARNING(f'  Remote failed:   {stats["remote_failed"]}'))
        else:
            self.stdout.write(f'  Leads created:   {stats["created"]}')
            self.stdout.write(f'  Duplicates:      {stats["duplicates"]}')
            self.stdout.write(f'  Assignments:     {stats["assigned"]}')

        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f'  Errors:          {stats["errors"]}'))

        self.stdout.write(self.style.SUCCESS('Done.'))
