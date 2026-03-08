"""
Management command to run the Craigslist monitor.
Usage:
    python manage.py monitor_craigslist
    python manage.py monitor_craigslist --regions newyork longisland
    python manage.py monitor_craigslist --no-details --max-age 24
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.craigslist import (
    monitor_craigslist,
    DEFAULT_REGIONS,
    LEAD_SECTIONS,
)


class Command(BaseCommand):
    help = 'Scrape Craigslist for service leads in the tri-state area'

    def add_arguments(self, parser):
        parser.add_argument(
            '--regions',
            nargs='+',
            default=None,
            help=f'Craigslist regions to scan (default: {", ".join(DEFAULT_REGIONS)})',
        )
        parser.add_argument(
            '--sections',
            nargs='+',
            default=None,
            help=f'CL sections to scan (default: {", ".join(LEAD_SECTIONS)})',
        )
        parser.add_argument(
            '--max-per-section',
            type=int,
            default=25,
            help='Max listings to process per section per region (default: 25)',
        )
        parser.add_argument(
            '--no-details',
            action='store_true',
            help='Skip fetching full posting details (faster but less data)',
        )
        parser.add_argument(
            '--max-age',
            type=int,
            default=48,
            help='Max post age in hours (default: 48)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting Craigslist monitor...'))

        regions = options['regions']
        sections = options['sections']

        if regions:
            self.stdout.write(f"  Regions: {', '.join(regions)}")
        else:
            self.stdout.write(f"  Regions: {', '.join(DEFAULT_REGIONS)}")

        if sections:
            self.stdout.write(f"  Sections: {', '.join(sections)}")
        else:
            self.stdout.write(f"  Sections: {', '.join(LEAD_SECTIONS)}")

        stats = monitor_craigslist(
            regions=regions,
            sections=sections,
            max_per_section=options['max_per_section'],
            fetch_details=not options['no_details'],
            max_age_hours=options['max_age'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Craigslist Monitor Results:'))
        self.stdout.write(f"  Listings scraped: {stats['scraped']}")
        self.stdout.write(f"  Leads created:    {stats['created']}")
        self.stdout.write(f"  Duplicates:       {stats['duplicates']}")
        self.stdout.write(f"  Assignments:      {stats['assigned']}")
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f"  Errors:           {stats['errors']}"))
        self.stdout.write(self.style.SUCCESS('Done.'))
