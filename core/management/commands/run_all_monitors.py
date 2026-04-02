"""
Master orchestrator — runs all lead monitors on schedule.
One cron job replaces everything:
  */30 * * * * cd /root/SalesSignalAI && venv/bin/python manage.py run_all_monitors
"""
import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.utils import timezone

from core.models.monitoring import MonitorRun
from core.utils.monitors.schedule import MONITOR_SCHEDULE

logger = logging.getLogger('monitors')


def _make_key(cmd, kwargs):
    return f"{cmd}_{'_'.join(str(v) for v in kwargs.values())}"


class Command(BaseCommand):
    help = 'Run all lead monitors on schedule'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Run all regardless of schedule')
        parser.add_argument('--dry-run', action='store_true', help='Show what would run')
        parser.add_argument('--list', action='store_true', help='List monitors and status')

    def handle(self, *args, **options):
        if options['list']:
            return self._list_monitors()

        total_run = 0
        total_skipped = 0
        total_errors = 0

        for entry in MONITOR_SCHEDULE:
            cmd_name, kwargs, freq_hours, description = entry[0], entry[1], entry[2], entry[3]
            key = _make_key(cmd_name, kwargs)

            # Check if due
            if not options['force']:
                last = MonitorRun.objects.filter(
                    monitor_name=key, status='success',
                ).order_by('-finished_at').first()

                if last and last.finished_at and last.finished_at > timezone.now() - timedelta(hours=freq_hours):
                    if options['dry_run']:
                        self.stdout.write(f"  SKIP  {description}  (ran {last.finished_at:%H:%M})")
                    total_skipped += 1
                    continue

            if options['dry_run']:
                self.stdout.write(f"  RUN   {description}")
                total_run += 1
                continue

            # Execute
            run = MonitorRun.objects.create(
                monitor_name=key,
                details={'description': description, 'command': cmd_name, 'kwargs': kwargs},
            )

            try:
                self.stdout.write(f"  Running: {description} ...")
                call_command(cmd_name, **kwargs)
                run.finish(status='success')
                total_run += 1
                self.stdout.write(self.style.SUCCESS(f"  [OK] {description}"))
            except Exception as e:
                run.finish(status='failed', error_message=str(e))
                total_errors += 1
                logger.error(f"Monitor failed: {description}: {e}")
                self.stdout.write(self.style.ERROR(f"  [FAIL] {description}: {e}"))

        self.stdout.write(f"\nDone — {total_run} run, {total_skipped} skipped, {total_errors} errors")

    def _list_monitors(self):
        self.stdout.write("\n=== Monitor Status ===\n")
        for entry in MONITOR_SCHEDULE:
            cmd_name, kwargs, freq_hours, description = entry[0], entry[1], entry[2], entry[3]
            key = _make_key(cmd_name, kwargs)
            last = MonitorRun.objects.filter(monitor_name=key).order_by('-started_at').first()
            if last and last.finished_at:
                age = timezone.now() - last.finished_at
                h, m = divmod(int(age.total_seconds()) // 60, 60)
                sym = '[OK]' if last.status == 'success' else '[FAIL]'
                self.stdout.write(f"  {sym} {description}: {last.status} ({h}h {m}m ago)")
            else:
                self.stdout.write(f"  [--] {description}: never run")
