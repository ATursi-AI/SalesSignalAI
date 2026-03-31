"""
Monitor Las Vegas / Clark County NV health inspections.

Usage:
    python manage.py monitor_vegas_health --days 7 --dry-run
    python manage.py monitor_vegas_health --days 14
"""
from django.core.management.base import BaseCommand
from core.models.monitoring import MonitorRun
from core.utils.monitors.vegas_health import monitor_vegas_health


class Command(BaseCommand):
    help = (
        'Monitor Southern Nevada Health District restaurant inspections. '
        'Nightly CSV — restaurants, hotels, buffets, food trucks in Las Vegas area.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7,
                            help='Look back this many days (default: 7)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Log matches without creating Lead records')

    def handle(self, *args, **options):
        run = MonitorRun.objects.create(
            monitor_name='vegas_health',
            details={'days': options['days']},
        )

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  LAS VEGAS / CLARK COUNTY HEALTH INSPECTION MONITOR")
        self.stdout.write(f"  Days: {options['days']}")
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
        self.stdout.write(f"{'='*60}\n")

        stats = monitor_vegas_health(
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
        self.stdout.write(self.style.SUCCESS('Done.'))
