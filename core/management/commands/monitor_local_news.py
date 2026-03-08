"""
Management command to run the Local News monitor.
Usage:
    python manage.py monitor_local_news
    python manage.py monitor_local_news --dry-run
    python manage.py monitor_local_news --no-comments --max-age 48
    python manage.py monitor_local_news --site-id 1 --site-id 3
"""
from django.core.management.base import BaseCommand

from core.models.monitoring import MonitoredLocalSite
from core.utils.monitors.local_news import monitor_local_news


class Command(BaseCommand):
    help = 'Scrape MonitoredLocalSite entries for service request leads in articles and comments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-per-site', type=int, default=15,
            help='Max articles to process per site (default: 15)',
        )
        parser.add_argument(
            '--no-comments', action='store_true',
            help='Skip fetching article comments (faster)',
        )
        parser.add_argument(
            '--max-age', type=int, default=72,
            help='Max article age in hours (default: 72)',
        )
        parser.add_argument(
            '--site-id', type=int, action='append', dest='site_ids',
            help='Only monitor specific site IDs (can repeat)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        sites = MonitoredLocalSite.objects.filter(is_active=True)
        if options['site_ids']:
            sites = sites.filter(id__in=options['site_ids'])

        self.stdout.write(self.style.HTTP_INFO('Starting Local News monitor...'))
        self.stdout.write(f"  Sites: {sites.count()}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_local_news(
            site_ids=options.get('site_ids'),
            max_per_site=options['max_per_site'],
            fetch_comments=not options['no_comments'],
            max_age_hours=options['max_age'],
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Local News Monitor Results:'))
        self.stdout.write(f"  Sites checked:     {stats['sites_checked']}")
        self.stdout.write(f"  Articles scraped:  {stats['articles_scraped']}")
        self.stdout.write(f"  Leads created:     {stats['created']}")
        self.stdout.write(f"  Duplicates:        {stats['duplicates']}")
        self.stdout.write(f"  Assignments:       {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:            {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
