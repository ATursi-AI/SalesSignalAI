"""
Flag high-value leads for immediate sales team review.

Scans public record leads for real dollar amounts ($5K+ by default)
and displays them sorted by value. Only uses actual data from the lead.

Usage:
    python manage.py flag_high_value_leads                    # $5K+ leads
    python manage.py flag_high_value_leads --threshold 10000  # $10K+ leads
    python manage.py flag_high_value_leads --limit 1000       # Scan last 1000 leads
    python manage.py flag_high_value_leads --days 7           # Only last 7 days
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models.leads import Lead
from core.utils.reach.lead_value import flag_high_value_leads, extract_lead_value


class Command(BaseCommand):
    help = 'Find leads with real dollar values above a threshold for immediate review'

    def add_arguments(self, parser):
        parser.add_argument('--threshold', type=int, default=5000,
            help='Minimum dollar value to flag (default $5,000)')
        parser.add_argument('--limit', type=int, default=500,
            help='Max leads to scan (default 500)')
        parser.add_argument('--days', type=int, default=0,
            help='Only scan leads from the last N days (0 = all)')
        parser.add_argument('--source-type', type=str, default='',
            help='Filter by source_type: permits, violations, hpd_violations, etc.')

    def handle(self, *args, **options):
        threshold = options['threshold']
        limit = options['limit']
        days = options['days']
        source_type = options.get('source_type', '')

        qs = Lead.objects.filter(
            platform='public_records',
            raw_data__isnull=False,
        ).exclude(raw_data={}).order_by('-discovered_at')

        if days > 0:
            since = timezone.now() - timedelta(days=days)
            qs = qs.filter(discovered_at__gte=since)

        if source_type:
            qs = qs.filter(source_type=source_type)

        qs = qs[:limit]

        self.stdout.write(f'Scanning up to {limit} public record leads for ${threshold:,}+ values...\n')

        results = flag_high_value_leads(queryset=qs, threshold=threshold)

        if not results:
            self.stdout.write('No high-value leads found.')
            return

        self.stdout.write(f'Found {len(results)} high-value leads:\n')
        self.stdout.write(f'{"VALUE":>12}  {"TYPE":<20}  {"LOCATION":<25}  {"LEAD":<50}')
        self.stdout.write(f'{"-" * 12}  {"-" * 20}  {"-" * 25}  {"-" * 50}')

        for lead, value in results:
            location = f'{lead.region or ""}, {lead.state or ""}'.strip(', ')
            content_preview = (lead.source_content or '')[:50].replace('\n', ' ')
            self.stdout.write(
                f'${value:>11,.0f}  {lead.source_type or "unknown":<20}  {location:<25}  {content_preview}'
            )

        total_value = sum(v for _, v in results)
        self.stdout.write(f'\nTotal pipeline value: ${total_value:,.0f}')
        self.stdout.write(f'Average lead value:  ${total_value / len(results):,.0f}')
