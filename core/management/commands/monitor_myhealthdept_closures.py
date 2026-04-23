"""
Monitor restaurant CLOSURES via myhealthdepartment.com /genericEndpoint.

These are facilities shut down for imminent health hazards (cockroach/
rodent infestations, sewage, no water, fire, foodborne illness, etc.).
Every closure is a HOT lead by definition — the business is closed and
desperate for whatever service will get them reopened.

Separate from monitor_myhealthdept (which pulls all inspections) — closures
get their own source_type='health_closures' bucket in the Command Center.

Usage:
    python manage.py monitor_myhealthdept_closures --jurisdiction orange_county --dry-run
    python manage.py monitor_myhealthdept_closures --jurisdiction sacramento --days 60
    python manage.py monitor_myhealthdept_closures --all
    python manage.py monitor_myhealthdept_closures --path any-jurisdiction-path-here
"""
from django.core.management.base import BaseCommand
from core.models.monitoring import MonitorRun
from core.utils.monitors.myhealthdept import (
    monitor_myhealthdept_closures,
    JURISDICTIONS,
)


class Command(BaseCommand):
    help = (
        'Scrape restaurant closures from myhealthdepartment.com for one or '
        'all configured jurisdictions. Every closure becomes a HOT lead with '
        'the reason (Cockroach / Rodent / Sewage / etc.) attached.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--jurisdiction', type=str, default='orange_county',
            choices=list(JURISDICTIONS.keys()),
            help='Which configured jurisdiction to monitor (default: orange_county)',
        )
        parser.add_argument(
            '--all', action='store_true',
            help='Loop through every configured jurisdiction. Jurisdictions '
                 'without a closures page will return 0 records and be skipped.',
        )
        parser.add_argument(
            '--days', type=int, default=60,
            help='Rolling day window for closures (default: 60 — the platform '
                 'default). Max ~180 in practice.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        jurisdictions = (
            list(JURISDICTIONS.keys()) if options['all']
            else [options['jurisdiction']]
        )

        grand_totals = {'scraped': 0, 'created': 0, 'dupes': 0, 'errors': 0}

        for jur_key in jurisdictions:
            config = JURISDICTIONS[jur_key]

            run = MonitorRun.objects.create(
                monitor_name=f'myhealthdept_closures_{jur_key}',
                details={'days': options['days'], 'jurisdiction': jur_key},
            )

            self.stdout.write(f"\n{'='*60}")
            self.stdout.write(
                f"  {config['name'].upper()} — RESTAURANT CLOSURES MONITOR"
            )
            self.stdout.write(f"  Platform: myhealthdepartment.com /genericEndpoint")
            self.stdout.write(f"  Days: {options['days']}")
            if options['dry_run']:
                self.stdout.write(self.style.WARNING('  DRY RUN MODE'))
            self.stdout.write(f"{'='*60}\n")

            stats = monitor_myhealthdept_closures(
                jurisdiction=jur_key,
                days=options['days'],
                dry_run=options['dry_run'],
            )

            run.leads_created = stats['created']
            run.duplicates = stats['duplicates']
            run.errors = stats['errors']
            run.items_scraped = stats['items_scraped']
            run.finish(status='success' if not stats['errors'] else 'partial')

            grand_totals['scraped'] += stats['items_scraped']
            grand_totals['created'] += stats['created']
            grand_totals['dupes'] += stats['duplicates']
            grand_totals['errors'] += stats['errors']

            self.stdout.write(f"\n  Closures scraped: {stats['items_scraped']}")
            self.stdout.write(f"  Leads created:    {stats['created']}")
            self.stdout.write(f"  Duplicates:       {stats['duplicates']}")
            if stats.get('errors'):
                self.stdout.write(self.style.WARNING(
                    f"  Errors:           {stats['errors']}"
                ))
            if stats['items_scraped'] == 0:
                self.stdout.write(self.style.WARNING(
                    f"  (No closures for {config['name']} — jurisdiction may "
                    f"not publish closures on this platform.)"
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'  {config["name"]} — Done.'
                ))

        if options['all']:
            self.stdout.write(f"\n{'='*60}")
            self.stdout.write(f"  GRAND TOTALS")
            self.stdout.write(f"{'='*60}")
            self.stdout.write(f"  Scraped: {grand_totals['scraped']}")
            self.stdout.write(f"  Created: {grand_totals['created']}")
            self.stdout.write(f"  Dupes:   {grand_totals['dupes']}")
            if grand_totals['errors']:
                self.stdout.write(self.style.WARNING(
                    f"  Errors:  {grand_totals['errors']}"
                ))
