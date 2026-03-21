"""Backfill event_date on existing leads by parsing raw_data JSON."""
import datetime
from datetime import timezone as tz_utc

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Lead

UTC = tz_utc.utc


def parse_date(value):
    """Try multiple date formats, return timezone-aware datetime or None."""
    if not value:
        return None

    if isinstance(value, (int, float)):
        value = str(int(value))

    value = str(value).strip()
    if not value:
        return None

    formats = [
        '%Y%m%d',
        '%m/%d/%Y',
        '%Y-%m-%d',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y %I:%M:%S %p',
    ]

    for fmt in formats:
        try:
            parsed = datetime.datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue

    # Try ISO parse as last resort
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except (ValueError, TypeError):
        pass

    return None


# Map of source_type -> list of raw_data keys to try (in priority order)
DATE_FIELD_MAP = {
    'violations': ['issue_date', 'violation_date', 'issuance_date', 'date'],
    'permits': ['filing_date', 'issued_date', 'date', 'filing_date_display'],
    'permits_now': ['filing_date', 'issued_date', 'date'],
    'health_inspections': ['inspection_date', 'date', 'inspdate'],
    'property_sales': ['document_date', 'recorded_date', 'date', 'doc_date'],
    'business_filings': ['filing_date', 'process_date', 'date', 'initial_dos_filing_date'],
    'liquor_licenses': ['effective_date', 'issue_date', 'date'],
}

# Also try by platform for older leads without source_type
PLATFORM_DATE_MAP = {
    'code_violation': ['issue_date', 'violation_date', 'issuance_date'],
    'permit': ['filing_date', 'issued_date'],
    'health_inspection': ['inspection_date', 'inspdate'],
    'property_sale': ['document_date', 'recorded_date', 'doc_date'],
    'business_filing': ['filing_date', 'process_date', 'initial_dos_filing_date'],
}


class Command(BaseCommand):
    help = 'Backfill event_date on existing leads from raw_data'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show what would be updated without saving')
        parser.add_argument('--force', action='store_true', help='Overwrite existing event_date values')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        qs = Lead.objects.all()
        if not force:
            qs = qs.filter(event_date__isnull=True)

        total = qs.count()
        updated = 0
        skipped = 0
        failed = 0

        self.stdout.write(f"Processing {total} leads (dry_run={dry_run}, force={force})...")

        for lead in qs.iterator(chunk_size=500):
            raw = lead.raw_data
            if not raw or not isinstance(raw, dict):
                skipped += 1
                continue

            # Determine which date fields to try
            date_keys = []
            if lead.source_type and lead.source_type in DATE_FIELD_MAP:
                date_keys = DATE_FIELD_MAP[lead.source_type]
            elif lead.platform in PLATFORM_DATE_MAP:
                date_keys = PLATFORM_DATE_MAP[lead.platform]

            # Try each key
            event_dt = None
            matched_key = None
            for key in date_keys:
                val = raw.get(key)
                if val:
                    event_dt = parse_date(val)
                    if event_dt:
                        matched_key = key
                        break

            # If no match from known keys, try any key containing 'date'
            if not event_dt:
                for key, val in raw.items():
                    if 'date' in key.lower() and val:
                        event_dt = parse_date(val)
                        if event_dt:
                            matched_key = key
                            break

            if event_dt:
                if not dry_run:
                    lead.event_date = event_dt
                    lead.save(update_fields=['event_date'])
                updated += 1
                if options['verbosity'] >= 2:
                    self.stdout.write(f"  Lead {lead.pk}: {matched_key}={raw.get(matched_key)} -> {event_dt.date()}")
            else:
                # Fallback: use discovered_at as event_date
                if not dry_run:
                    lead.event_date = lead.discovered_at
                    lead.save(update_fields=['event_date'])
                skipped += 1

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}Done. {updated} leads backfilled from raw_data, "
            f"{skipped} used discovered_at fallback, {failed} failed. "
            f"Total: {total}"
        ))
