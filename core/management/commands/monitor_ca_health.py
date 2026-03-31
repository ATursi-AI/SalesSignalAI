"""
Monitor California county health inspections.

Usage:
    python manage.py monitor_ca_health --county sacramento --days 7 --dry-run
    python manage.py monitor_ca_health --county san_diego --days 7
    python manage.py monitor_ca_health --county santa_clara --days 14
    python manage.py monitor_ca_health --county la --days 30
    python manage.py monitor_ca_health --all --days 7
"""
from django.core.management.base import BaseCommand
from core.models.monitoring import MonitorRun
from core.utils.monitors.ca_health_inspections import (
    monitor_sacramento_health,
    monitor_san_diego_health,
    monitor_santa_clara_health,
    monitor_la_county_health,
)

COUNTY_MAP = {
    'sacramento': ('Sacramento County', monitor_sacramento_health),
    'san_diego': ('San Diego County', monitor_san_diego_health),
    'santa_clara': ('Santa Clara County', monitor_santa_clara_health),
    'la': ('Los Angeles County', monitor_la_county_health),
}


class Command(BaseCommand):
    help = (
        'Monitor California county health inspections. '
        'Sacramento (daily), San Diego (95K+ facilities), '
        'Santa Clara (Silicon Valley), LA County (has owner name).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--county', type=str, default='sacramento',
            choices=list(COUNTY_MAP.keys()),
            help='CA county to monitor (default: sacramento)',
        )
        parser.add_argument('--all', action='store_true',
                            help='Monitor all CA counties')
        parser.add_argument('--days', type=int, default=7,
                            help='Look back this many days (default: 7)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Log matches without creating Lead records')

    def handle(self, *args, **options):
        counties = list(COUNTY_MAP.keys()) if options['all'] else [options['county']]

        for county_key in counties:
            county_name, monitor_fn = COUNTY_MAP[county_key]

            run = MonitorRun.objects.create(
                monitor_name=f'ca_health_{county_key}',
                details={'days': options['days'], 'county': county_key},
            )

            self.stdout.write(f"\n{'='*60}")
            self.stdout.write(f"  {county_name.upper()} HEALTH INSPECTION MONITOR")
            self.stdout.write(f"  Days: {options['days']}")
            if options['dry_run']:
                self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
            self.stdout.write(f"{'='*60}\n")

            stats = monitor_fn(
                days=options['days'],
                dry_run=options['dry_run'],
            )

            run.leads_created = stats['created']
            run.duplicates = stats['duplicates']
            run.errors = stats['errors']
            run.items_scraped = stats['items_scraped']
            run.finish(status='success' if not stats['errors'] else 'partial')

            self.stdout.write(f"\n  Items scraped:  {stats['items_scraped']}")
            self.stdout.write(f"  Leads created:  {stats['created']}")
            self.stdout.write(f"  Duplicates:     {stats['duplicates']}")
            self.stdout.write(f"  Assignments:    {stats['assigned']}")
            if stats.get('errors'):
                self.stdout.write(self.style.WARNING(f"  Errors:         {stats['errors']}"))
            self.stdout.write(self.style.SUCCESS(f'{county_name} — Done.'))
