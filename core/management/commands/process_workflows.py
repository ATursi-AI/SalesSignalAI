"""
Resume paused workflow executions.

Runs every 15 minutes via cron:
  */15 * * * * cd /root/SalesSignalAI && venv/bin/python manage.py process_workflows

Finds WorkflowExecutions with status='waiting' and resume_at <= now,
then continues executing their remaining actions.
"""
from django.core.management.base import BaseCommand

from core.services.workflow_engine import resume_waiting_workflows


class Command(BaseCommand):
    help = 'Resume paused workflow executions that have passed their wait period'

    def handle(self, *args, **options):
        resumed = resume_waiting_workflows()
        if resumed:
            self.stdout.write(self.style.SUCCESS(f'Resumed {resumed} workflow(s)'))
        else:
            self.stdout.write('No workflows to resume.')
