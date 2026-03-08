"""
Run all (or selected) monitors with error handling and MonitorRun logging.

Usage:
    python manage.py run_monitors
    python manage.py run_monitors --monitors craigslist reddit
    python manage.py run_monitors --dry-run
    python manage.py run_monitors --list
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.runner import run_all_monitors, _get_monitor_registry


class Command(BaseCommand):
    help = 'Run all platform monitors with error handling and health logging'

    def add_arguments(self, parser):
        parser.add_argument(
            '--monitors', nargs='+',
            help='Only run specific monitors (e.g. craigslist reddit)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )
        parser.add_argument(
            '--list', action='store_true',
            help='List available monitors and exit',
        )

    def handle(self, *args, **options):
        if options['list']:
            registry = _get_monitor_registry()
            self.stdout.write(self.style.HTTP_INFO(f'Available monitors ({len(registry)}):'))
            for name in sorted(registry.keys()):
                self.stdout.write(f'  {name}')
            return

        self.stdout.write(self.style.HTTP_INFO('Starting monitor run...'))
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        runs = run_all_monitors(
            dry_run=options['dry_run'],
            monitors=options.get('monitors'),
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Monitor Run Results:'))
        for run in runs:
            status_style = (
                self.style.SUCCESS if run.status == 'success'
                else self.style.WARNING if run.status == 'partial'
                else self.style.ERROR
            )
            self.stdout.write(
                f'  {run.monitor_name:20s} '
                f'{status_style(run.status):10s} '
                f'scraped={run.items_scraped} '
                f'leads={run.leads_created} '
                f'dupes={run.duplicates} '
                f'errors={run.errors} '
                f'({run.duration_seconds:.1f}s)'
            )
            if run.error_message:
                self.stdout.write(self.style.ERROR(f'    Error: {run.error_message[:120]}'))

        total_leads = sum(r.leads_created for r in runs)
        failed = sum(1 for r in runs if r.status == 'failed')
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done: {len(runs)} monitors, {total_leads} leads created, {failed} failures'
        ))
