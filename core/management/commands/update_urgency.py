from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from core.models import Lead


class Command(BaseCommand):
    help = 'Update lead urgency levels based on age (HOT->WARM->NEW->Stale)'

    def handle(self, *args, **options):
        now = timezone.now()
        one_hour = now - timedelta(hours=1)
        four_hours = now - timedelta(hours=4)
        twenty_four_hours = now - timedelta(hours=24)

        # Stale: older than 24 hours
        stale = Lead.objects.filter(
            discovered_at__lt=twenty_four_hours
        ).exclude(urgency_level='stale').update(urgency_level='stale', urgency_score=10)

        # NEW: 4-24 hours old
        new = Lead.objects.filter(
            discovered_at__lt=four_hours,
            discovered_at__gte=twenty_four_hours,
        ).exclude(urgency_level='new').update(urgency_level='new', urgency_score=40)

        # WARM: 1-4 hours old
        warm = Lead.objects.filter(
            discovered_at__lt=one_hour,
            discovered_at__gte=four_hours,
        ).exclude(urgency_level='warm').update(urgency_level='warm', urgency_score=65)

        # HOT: less than 1 hour old (stays HOT)
        hot = Lead.objects.filter(
            discovered_at__gte=one_hour,
        ).exclude(urgency_level='hot').update(urgency_level='hot', urgency_score=90)

        self.stdout.write(self.style.SUCCESS(
            f'Urgency updated: {hot} HOT, {warm} WARM, {new} NEW, {stale} Stale'
        ))
