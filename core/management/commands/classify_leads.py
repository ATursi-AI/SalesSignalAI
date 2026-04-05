"""
Management command to classify unclassified social media leads using AI.

Usage:
    python manage.py classify_leads                    # Classify up to 100 unclassified
    python manage.py classify_leads --limit 500        # Classify up to 500
    python manage.py classify_leads --reclassify       # Re-classify ALL social leads
    python manage.py classify_leads --platform reddit  # Only Reddit leads
    python manage.py classify_leads --dry-run          # Show what would be classified
"""
from django.core.management.base import BaseCommand

from core.models.leads import Lead
from core.utils.reach.intent_classifier import (
    classify_leads_bulk, SOCIAL_PLATFORMS,
)


class Command(BaseCommand):
    help = 'Classify social media leads for intent using AI (Gemini Flash-Lite)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=100,
            help='Max leads to classify (default: 100)',
        )
        parser.add_argument(
            '--reclassify', action='store_true',
            help='Re-classify ALL social leads, not just unclassified ones',
        )
        parser.add_argument(
            '--platform', type=str, default='',
            help='Only classify leads from this platform (e.g. reddit, nextdoor)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show leads that would be classified without actually classifying',
        )

    def handle(self, *args, **options):
        limit = options['limit']
        reclassify = options['reclassify']
        platform = options['platform']
        dry_run = options['dry_run']

        # Build queryset
        qs = Lead.objects.all()

        if platform:
            if platform not in SOCIAL_PLATFORMS:
                self.stderr.write(
                    f'Platform "{platform}" is not a social platform. '
                    f'Valid: {", ".join(sorted(SOCIAL_PLATFORMS))}'
                )
                return
            qs = qs.filter(platform=platform)
        else:
            qs = qs.filter(platform__in=SOCIAL_PLATFORMS)

        if not reclassify:
            qs = qs.filter(intent_classification='not_classified')

        qs = qs.order_by('-discovered_at')[:limit]
        total = qs.count()

        if total == 0:
            self.stdout.write(self.style.WARNING('No leads to classify.'))
            return

        if dry_run:
            self.stdout.write(f'\nWould classify {total} leads:\n')
            for lead in qs[:20]:
                content_preview = lead.source_content[:80].replace('\n', ' ')
                self.stdout.write(
                    f'  #{lead.id} [{lead.platform}] '
                    f'{lead.intent_classification} — {content_preview}'
                )
            if total > 20:
                self.stdout.write(f'  ... and {total - 20} more')
            return

        self.stdout.write(f'\nClassifying {total} social media leads...\n')

        stats = classify_leads_bulk(queryset=qs, limit=limit)

        self.stdout.write(self.style.SUCCESS(
            f'\nDone! Results:\n'
            f'  Classified:      {stats["classified"]}\n'
            f'  Real leads:      {stats["real_leads"]}\n'
            f'  False positives: {stats["false_positives"]}\n'
            f'  Mention only:    {stats["mention_only"]}\n'
            f'  Job postings:    {stats["job_posting"]}\n'
            f'  Advice/discuss:  {stats["advice_giving"]}\n'
            f'  Errors:          {stats["errors"]}'
        ))
