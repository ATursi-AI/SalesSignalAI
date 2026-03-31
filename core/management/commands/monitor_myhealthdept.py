"""
Monitor health inspections via myhealthdepartment.com platform.

One command covers multiple jurisdictions:
  - denver         (Colorado)
  - portland       (Multnomah County, OR)
  - colorado_springs (El Paso County, CO)
  - honolulu       (Hawaii DOH)
  - sacramento     (Sacramento County, CA)

Usage:
    python manage.py monitor_myhealthdept --jurisdiction denver --days 7 --dry-run
    python manage.py monitor_myhealthdept --jurisdiction portland --days 7
    python manage.py monitor_myhealthdept --all --days 7
"""
from django.core.management.base import BaseCommand
from core.models.monitoring import MonitorRun
from core.utils.monitors.myhealthdept import monitor_myhealthdept, JURISDICTIONS


class Command(BaseCommand):
    help = (
        'Monitor health inspections across multiple jurisdictions via '
        'myhealthdepartment.com — Denver, Portland, Colorado Springs, '
        'Honolulu, Sacramento. One scraper pattern covers all.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--jurisdiction', type=str, default='denver',
            choices=list(JURISDICTIONS.keys()),
            help='Which jurisdiction to monitor (default: denver)',
        )
        parser.add_argument('--all', action='store_true',
                            help='Monitor all jurisdictions')
        parser.add_argument('--days', type=int, default=7,
                            help='Look back this many days (default: 7)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Log matches without creating Lead records')

    def handle(self, *args, **options):
        jurisdictions = list(JURISDICTIONS.keys()) if options['all'] else [options['jurisdiction']]

        for jur_key in jurisdictions:
            config = JURISDICTIONS[jur_key]

            run = MonitorRun.objects.create(
                monitor_name=f'myhealthdept_{jur_key}',
                details={'days': options['days'], 'jurisdiction': jur_key},
            )

            self.stdout.write(f"\n{'='*60}")
            self.stdout.write(f"  {config['name'].upper()} HEALTH INSPECTION MONITOR")
            self.stdout.write(f"  Platform: myhealthdepartment.com")
            self.stdout.write(f"  Days: {options['days']}")
            if options['dry_run']:
                self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
            self.stdout.write(f"{'='*60}\n")

            stats = monitor_myhealthdept(
                jurisdiction=jur_key,
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
            self.stdout.write(self.style.SUCCESS(f'{config["name"]} — Done.'))
