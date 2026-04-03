"""
One-time fix: Move existing Reddit leads from public_records to social_media.

These leads were ingested via the API before the ingest endpoint passed
source_group/source_type through to process_lead().

Usage:
    python manage.py fix_reddit_source_group --dry-run   # preview
    python manage.py fix_reddit_source_group              # apply
"""
from django.core.management.base import BaseCommand
from core.models.leads import Lead


class Command(BaseCommand):
    help = 'Fix Reddit leads stuck in public_records — moves them to social_media'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would change without saving')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Find Reddit leads that are misclassified
        reddit_leads = Lead.objects.filter(
            platform='reddit',
        ).exclude(
            source_group='social_media',
            source_type='reddit',
        )

        count = reddit_leads.count()
        self.stdout.write(f"\nFound {count} Reddit lead(s) not in social_media/reddit\n")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to fix."))
            return

        if dry_run:
            for lead in reddit_leads[:20]:
                self.stdout.write(
                    f"  [DRY] Lead #{lead.id} — "
                    f"group={lead.source_group!r} type={lead.source_type!r} "
                    f"→ social_media/reddit"
                )
            if count > 20:
                self.stdout.write(f"  ... and {count - 20} more")
            return

        updated = reddit_leads.update(
            source_group='social_media',
            source_type='reddit',
        )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone — updated {updated} Reddit lead(s) to source_group='social_media', source_type='reddit'"
        ))
