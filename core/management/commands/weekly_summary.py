"""
Weekly summary email command.
Sends a Monday morning digest to each active business showing:
  - Leads detected / responded to / won
  - Revenue earned
  - Competitor activity
  - Top performing platform & area

Usage:
    python manage.py weekly_summary
    python manage.py weekly_summary --dry-run
    python manage.py weekly_summary --user-id 3
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import Count, Sum, Q
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import BusinessProfile, LeadAssignment, TrackedCompetitor, CompetitorReview


class Command(BaseCommand):
    help = 'Send weekly summary email to each active business'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print email content instead of sending',
        )
        parser.add_argument(
            '--user-id', type=int,
            help='Send only to a specific user ID',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        week_ago = now - timedelta(days=7)

        profiles = BusinessProfile.objects.filter(
            is_active=True, onboarding_complete=True,
        ).select_related('user', 'service_category')

        if options['user_id']:
            profiles = profiles.filter(user_id=options['user_id'])

        sent = 0
        errors = 0

        for profile in profiles:
            try:
                summary = self._build_summary(profile, week_ago, now)
                subject = f"Your Weekly SalesSignal Report - {now.strftime('%b %d, %Y')}"

                html = render_to_string('emails/weekly_summary.html', {
                    'profile': profile,
                    'summary': summary,
                    'week_start': week_ago,
                    'week_end': now,
                })

                plain = self._build_plain_text(profile, summary, week_ago, now)

                if options['dry_run']:
                    self.stdout.write(f"\n{'='*60}")
                    self.stdout.write(f"TO: {profile.email}")
                    self.stdout.write(f"SUBJECT: {subject}")
                    self.stdout.write(f"{'='*60}")
                    self.stdout.write(plain)
                    sent += 1
                    continue

                send_mail(
                    subject=subject,
                    message=plain,
                    html_message=html,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[profile.email],
                    fail_silently=False,
                )
                sent += 1
                self.stdout.write(f"  Sent to {profile.business_name} ({profile.email})")

            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(f"  Failed for {profile.business_name}: {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"\nWeekly summary: {sent} sent, {errors} errors"
        ))

    def _build_summary(self, profile, since, until):
        assignments = LeadAssignment.objects.filter(
            business=profile, created_at__gte=since, created_at__lt=until,
        )

        total = assignments.count()
        by_status = dict(assignments.values_list('status').annotate(c=Count('id')).values_list('status', 'c'))

        responded = sum(by_status.get(s, 0) for s in ['contacted', 'quoted', 'won', 'lost'])
        won = by_status.get('won', 0)
        revenue = assignments.filter(status='won').aggregate(
            total=Sum('revenue'))['total'] or 0

        # Best platform
        top_platform = assignments.values('lead__platform').annotate(
            c=Count('id')
        ).order_by('-c').first()

        # Best area
        top_area = assignments.filter(
            lead__detected_location__gt='',
        ).values('lead__detected_location').annotate(
            c=Count('id')
        ).order_by('-c').first()

        # Hot leads still open
        hot_open = LeadAssignment.objects.filter(
            business=profile, status='new', lead__urgency_level='hot',
        ).count()

        # Competitor activity
        competitors = TrackedCompetitor.objects.filter(
            business=profile, is_active=True,
        )
        competitor_reviews = CompetitorReview.objects.filter(
            competitor__in=competitors, review_date__gte=since,
        )
        neg_reviews = competitor_reviews.filter(is_negative=True).count()
        opp_reviews = competitor_reviews.filter(is_opportunity=True).count()

        return {
            'total_leads': total,
            'responded': responded,
            'won': won,
            'revenue': float(revenue),
            'response_rate': round(responded / total * 100) if total else 0,
            'top_platform': (top_platform['lead__platform'].replace('_', ' ').title()
                             if top_platform else 'N/A'),
            'top_platform_count': top_platform['c'] if top_platform else 0,
            'top_area': top_area['lead__detected_location'] if top_area else 'N/A',
            'top_area_count': top_area['c'] if top_area else 0,
            'hot_open': hot_open,
            'competitor_count': competitors.count(),
            'competitor_neg_reviews': neg_reviews,
            'competitor_opportunities': opp_reviews,
            'new': by_status.get('new', 0),
            'alerted': by_status.get('alerted', 0),
            'viewed': by_status.get('viewed', 0),
        }

    def _build_plain_text(self, profile, s, week_start, week_end):
        lines = [
            f"Weekly SalesSignal Report for {profile.business_name}",
            f"Week of {week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}",
            "",
            "LEAD ACTIVITY",
            f"  Leads detected:    {s['total_leads']}",
            f"  Responded to:      {s['responded']}",
            f"  Won:               {s['won']}",
            f"  Revenue:           ${s['revenue']:,.0f}",
            f"  Response rate:     {s['response_rate']}%",
            "",
            "TOP PERFORMERS",
            f"  Best platform:     {s['top_platform']} ({s['top_platform_count']} leads)",
            f"  Hottest area:      {s['top_area']} ({s['top_area_count']} leads)",
            "",
        ]
        if s['hot_open']:
            lines.append(f"ACTION NEEDED: {s['hot_open']} hot leads still unanswered!")
            lines.append("")

        if s['competitor_count']:
            lines.append("COMPETITOR WATCH")
            lines.append(f"  Tracking:          {s['competitor_count']} competitors")
            lines.append(f"  Their neg reviews: {s['competitor_neg_reviews']}")
            lines.append(f"  Opportunities:     {s['competitor_opportunities']}")
            lines.append("")

        lines.append("Log in to your dashboard for full details.")
        lines.append("-- SalesSignal AI")
        return '\n'.join(lines)
