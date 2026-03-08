from django.core.management.base import BaseCommand
from core.utils.alerts.dispatcher import dispatch_pending_alerts


class Command(BaseCommand):
    help = 'Dispatch alerts for new lead assignments'

    def handle(self, *args, **options):
        count = dispatch_pending_alerts()
        self.stdout.write(self.style.SUCCESS(f'Dispatched {count} alerts'))
