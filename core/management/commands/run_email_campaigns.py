"""
Management command to run outreach email campaigns.
Designed to run daily via cron.

For each active campaign:
1. Enrich new prospects (scrape website, extract data via Gemini)
2. Generate and send Email 1 to new prospects (up to daily_send_limit)
3. Send Email 2 to prospects where email1 was sent 3+ days ago
4. Send Email 3 to prospects where email2 was sent 4+ days ago
5. Log all activity

Usage:
    python manage.py run_email_campaigns
    python manage.py run_email_campaigns --dry-run
    python manage.py run_email_campaigns --campaign-id 5
    python manage.py run_email_campaigns --enrich-only
"""
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models.outreach import (
    OutreachCampaign, OutreachProspect, GeneratedEmail,
)
from core.utils.email_engine.ai_engine import (
    enrich_prospect, generate_email, classify_reply,
)
from core.utils.email_engine.backends import get_email_sender
from core.utils.email_engine.sender import is_unsubscribed, _append_unsubscribe_footer
from core.utils.email_engine.warming import can_send_today, record_send


class Command(BaseCommand):
    help = 'Run outreach email campaigns — enrich prospects, generate AI emails, send sequences'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be done without sending emails',
        )
        parser.add_argument(
            '--campaign-id', type=int, default=None,
            help='Run only a specific campaign',
        )
        parser.add_argument(
            '--enrich-only', action='store_true',
            help='Only run prospect enrichment, skip sending',
        )
        parser.add_argument(
            '--max-sends', type=int, default=None,
            help='Override max emails to send across all campaigns',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        campaign_id = options['campaign_id']
        enrich_only = options['enrich_only']
        max_sends = options['max_sends']

        self.stdout.write(self.style.HTTP_INFO('Starting Email Campaign Runner...'))
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — no emails will be sent'))

        # Get active campaigns
        campaigns = OutreachCampaign.objects.filter(status='active')
        if campaign_id:
            campaigns = campaigns.filter(id=campaign_id)

        if not campaigns.exists():
            self.stdout.write(self.style.WARNING('  No active campaigns found.'))
            return

        self.stdout.write(f'  Active campaigns: {campaigns.count()}')

        total_stats = {
            'enriched': 0,
            'email1_generated': 0,
            'email1_sent': 0,
            'email2_sent': 0,
            'email3_sent': 0,
            'bounced': 0,
            'errors': 0,
            'skipped_unsub': 0,
        }
        sends_remaining = max_sends

        for campaign in campaigns:
            self.stdout.write('')
            self.stdout.write(self.style.HTTP_INFO(
                f'  Campaign: {campaign.name} (ID: {campaign.id})'
            ))
            self.stdout.write(f'    Business: {campaign.business.business_name}')
            self.stdout.write(f'    Prospects: {campaign.prospects.count()}')
            self.stdout.write(f'    Daily limit: {campaign.daily_send_limit} new + follow-ups')

            stats = self._run_campaign(
                campaign, dry_run, enrich_only, sends_remaining,
            )

            for key in total_stats:
                total_stats[key] += stats.get(key, 0)

            if sends_remaining is not None:
                sent_this = stats.get('email1_sent', 0) + stats.get('email2_sent', 0) + stats.get('email3_sent', 0)
                sends_remaining = max(0, sends_remaining - sent_this)
                if sends_remaining == 0:
                    self.stdout.write(self.style.WARNING('  Max sends reached, stopping.'))
                    break

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Campaign Runner Complete:'))
        self.stdout.write(f'  Prospects enriched:   {total_stats["enriched"]}')
        self.stdout.write(f'  Email 1 generated:    {total_stats["email1_generated"]}')
        self.stdout.write(f'  Email 1 sent:         {total_stats["email1_sent"]}')
        self.stdout.write(f'  Email 2 sent:         {total_stats["email2_sent"]}')
        self.stdout.write(f'  Email 3 sent:         {total_stats["email3_sent"]}')
        if total_stats['bounced']:
            self.stdout.write(f'  Bounced:              {total_stats["bounced"]}')
        if total_stats['skipped_unsub']:
            self.stdout.write(f'  Skipped (unsub):      {total_stats["skipped_unsub"]}')
        if total_stats['errors']:
            self.stdout.write(self.style.WARNING(
                f'  Errors:               {total_stats["errors"]}'
            ))
        self.stdout.write(self.style.SUCCESS('Done.'))

    def _run_campaign(self, campaign, dry_run, enrich_only, max_sends):
        stats = {
            'enriched': 0,
            'email1_generated': 0,
            'email1_sent': 0,
            'email2_sent': 0,
            'email3_sent': 0,
            'bounced': 0,
            'errors': 0,
            'skipped_unsub': 0,
        }

        now = timezone.now()

        # Step 1: Enrich new prospects that haven't been enriched yet
        unenriched = campaign.prospects.filter(
            enrichment_data={},
            website_url__gt='',
        ).exclude(status='bounced')[:20]

        for prospect in unenriched:
            data = enrich_prospect(prospect)
            if data:
                prospect.enrichment_data = data
                prospect.save(update_fields=['enrichment_data', 'updated_at'])
                stats['enriched'] += 1
                self.stdout.write(f'    [ENRICH] {prospect.business_name}')

        if enrich_only:
            return stats

        sender = get_email_sender(campaign)

        # Step 2: Email 1 — new prospects
        new_prospects = campaign.prospects.filter(status='new')[:campaign.daily_send_limit]
        for prospect in new_prospects:
            if max_sends is not None and max_sends <= 0:
                break

            if is_unsubscribed(prospect.contact_email):
                stats['skipped_unsub'] += 1
                continue

            # Check warming limits
            allowed, remaining, reason = can_send_today(campaign.max_emails_per_day)
            if not allowed:
                self.stdout.write(f'    [LIMIT] {reason}')
                break

            # Generate Email 1
            email_content = generate_email(prospect, campaign, 1)
            if not email_content:
                stats['errors'] += 1
                continue

            stats['email1_generated'] += 1

            # Create GeneratedEmail record
            tracking_id = uuid.uuid4().hex[:16]
            gen_email = GeneratedEmail(
                prospect=prospect,
                sequence_number=1,
                subject=email_content['subject'],
                body=email_content['body'],
                ai_model_used=email_content.get('model_used', ''),
                status='draft',
                tracking_id=tracking_id,
            )

            if dry_run:
                self.stdout.write(
                    f'    [DRY] Email 1 -> {prospect.business_name} '
                    f'({prospect.contact_email}) | '
                    f'Subject: {email_content["subject"][:50]}'
                )
                continue

            # Send
            body_with_footer = _append_unsubscribe_footer(
                email_content['body'], prospect.contact_email,
            )
            result = sender.send_email(
                to_email=prospect.contact_email,
                subject=email_content['subject'],
                body=body_with_footer,
                from_email=campaign.sending_email or None,
                reply_to=campaign.reply_to_email or campaign.business.email,
            )

            if result['success']:
                gen_email.status = 'sent'
                gen_email.sent_at = now
                gen_email.save()
                prospect.status = 'email1_sent'
                prospect.email1_sent_at = now
                prospect.save(update_fields=['status', 'email1_sent_at', 'updated_at'])
                record_send()
                stats['email1_sent'] += 1
                self.stdout.write(f'    [SENT] Email 1 -> {prospect.business_name}')

                if max_sends is not None:
                    max_sends -= 1
            else:
                gen_email.status = 'bounced' if 'bounce' in result.get('error', '') else 'draft'
                gen_email.save()
                if 'bounce' in result.get('error', ''):
                    prospect.status = 'bounced'
                    prospect.save(update_fields=['status', 'updated_at'])
                    stats['bounced'] += 1
                else:
                    stats['errors'] += 1
                self.stdout.write(
                    self.style.WARNING(f'    [FAIL] Email 1 -> {prospect.business_name}: {result["error"]}')
                )

        # Step 3: Email 2 — follow-up (3+ days after Email 1)
        email2_cutoff = now - timedelta(days=3)
        email2_prospects = campaign.prospects.filter(
            status='email1_sent',
            email1_sent_at__lte=email2_cutoff,
        ).exclude(
            replied_at__isnull=False,
        )

        for prospect in email2_prospects:
            if max_sends is not None and max_sends <= 0:
                break

            allowed, remaining, reason = can_send_today(campaign.max_emails_per_day)
            if not allowed:
                break

            # Check if Email 2 already exists
            if GeneratedEmail.objects.filter(prospect=prospect, sequence_number=2).exists():
                continue

            email_content = generate_email(prospect, campaign, 2)
            if not email_content:
                stats['errors'] += 1
                continue

            tracking_id = uuid.uuid4().hex[:16]
            gen_email = GeneratedEmail(
                prospect=prospect,
                sequence_number=2,
                subject=email_content['subject'],
                body=email_content['body'],
                ai_model_used=email_content.get('model_used', ''),
                tracking_id=tracking_id,
            )

            if dry_run:
                self.stdout.write(
                    f'    [DRY] Email 2 -> {prospect.business_name} | '
                    f'Subject: {email_content["subject"][:50]}'
                )
                continue

            body_with_footer = _append_unsubscribe_footer(
                email_content['body'], prospect.contact_email,
            )
            result = sender.send_email(
                to_email=prospect.contact_email,
                subject=email_content['subject'],
                body=body_with_footer,
                from_email=campaign.sending_email or None,
                reply_to=campaign.reply_to_email or campaign.business.email,
            )

            if result['success']:
                gen_email.status = 'sent'
                gen_email.sent_at = now
                gen_email.save()
                prospect.status = 'email2_sent'
                prospect.email2_sent_at = now
                prospect.save(update_fields=['status', 'email2_sent_at', 'updated_at'])
                record_send()
                stats['email2_sent'] += 1
                self.stdout.write(f'    [SENT] Email 2 -> {prospect.business_name}')
                if max_sends is not None:
                    max_sends -= 1
            else:
                gen_email.status = 'bounced' if 'bounce' in result.get('error', '') else 'draft'
                gen_email.save()
                stats['errors'] += 1

        # Step 4: Email 3 — final touch (4+ days after Email 2)
        email3_cutoff = now - timedelta(days=4)
        email3_prospects = campaign.prospects.filter(
            status='email2_sent',
            email2_sent_at__lte=email3_cutoff,
        ).exclude(
            replied_at__isnull=False,
        )

        for prospect in email3_prospects:
            if max_sends is not None and max_sends <= 0:
                break

            if campaign.email_sequence_count < 3:
                continue

            allowed, remaining, reason = can_send_today(campaign.max_emails_per_day)
            if not allowed:
                break

            if GeneratedEmail.objects.filter(prospect=prospect, sequence_number=3).exists():
                continue

            email_content = generate_email(prospect, campaign, 3)
            if not email_content:
                stats['errors'] += 1
                continue

            tracking_id = uuid.uuid4().hex[:16]
            gen_email = GeneratedEmail(
                prospect=prospect,
                sequence_number=3,
                subject=email_content['subject'],
                body=email_content['body'],
                ai_model_used=email_content.get('model_used', ''),
                tracking_id=tracking_id,
            )

            if dry_run:
                self.stdout.write(
                    f'    [DRY] Email 3 -> {prospect.business_name} | '
                    f'Subject: {email_content["subject"][:50]}'
                )
                continue

            body_with_footer = _append_unsubscribe_footer(
                email_content['body'], prospect.contact_email,
            )
            result = sender.send_email(
                to_email=prospect.contact_email,
                subject=email_content['subject'],
                body=body_with_footer,
                from_email=campaign.sending_email or None,
                reply_to=campaign.reply_to_email or campaign.business.email,
            )

            if result['success']:
                gen_email.status = 'sent'
                gen_email.sent_at = now
                gen_email.save()
                prospect.status = 'email3_sent'
                prospect.email3_sent_at = now
                prospect.save(update_fields=['status', 'email3_sent_at', 'updated_at'])
                record_send()
                stats['email3_sent'] += 1
                self.stdout.write(f'    [SENT] Email 3 -> {prospect.business_name}')
                if max_sends is not None:
                    max_sends -= 1
            else:
                gen_email.status = 'bounced' if 'bounce' in result.get('error', '') else 'draft'
                gen_email.save()
                stats['errors'] += 1

        # Update campaign metrics
        if not dry_run:
            campaign.emails_sent = GeneratedEmail.objects.filter(
                prospect__campaign=campaign,
                status__in=['sent', 'opened', 'replied'],
            ).count()
            campaign.emails_opened = GeneratedEmail.objects.filter(
                prospect__campaign=campaign,
                status__in=['opened', 'replied'],
            ).count()
            campaign.emails_replied = GeneratedEmail.objects.filter(
                prospect__campaign=campaign,
                status='replied',
            ).count()
            campaign.emails_bounced = GeneratedEmail.objects.filter(
                prospect__campaign=campaign,
                status='bounced',
            ).count()
            campaign.total_prospects = campaign.prospects.count()
            campaign.save(update_fields=[
                'emails_sent', 'emails_opened', 'emails_replied',
                'emails_bounced', 'total_prospects',
            ])

        return stats
