"""
Process email drip sequences for all active campaigns.

Designed to run every 15 minutes via cron:
  */15 * * * * cd /root/SalesSignalAI && /root/SalesSignalAI/venv/bin/python manage.py process_email_drips

For each active campaign:
  1. Find prospects due for their next email
  2. Generate the email via AI (multi-model engine)
  3. Send via configured backend (SES, SMTP, or Gmail)
  4. Update tracking fields and campaign metrics
  5. Respect daily send limits and warming schedule
  6. Use campaign.followup_delay_days for timing between emails

Usage:
    python manage.py process_email_drips
    python manage.py process_email_drips --campaign-id 5
    python manage.py process_email_drips --dry-run
    python manage.py process_email_drips --max-sends 10
    python manage.py process_email_drips --no-enrich
"""
import time
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models.outreach import (
    OutreachCampaign, OutreachProspect, GeneratedEmail,
)
from core.utils.email_engine.ai_engine import (
    enrich_prospect, generate_email,
)
from core.utils.email_engine.backends import get_email_sender
from core.utils.email_engine.sender import is_unsubscribed, _append_unsubscribe_footer
from core.utils.email_engine.warming import can_send_today, record_send


class Command(BaseCommand):
    help = 'Process email drip sequences — runs every 15 minutes via cron'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview without sending')
        parser.add_argument('--campaign-id', type=int, default=None, help='Process single campaign')
        parser.add_argument('--max-sends', type=int, default=None, help='Cap total emails sent this run')
        parser.add_argument('--no-enrich', action='store_true', help='Skip prospect enrichment step')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        campaign_id = options['campaign_id']
        max_sends = options['max_sends']
        no_enrich = options['no_enrich']

        now = timezone.now()
        self.stdout.write(f'[{now:%Y-%m-%d %H:%M}] Email Drip Processor starting...')
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN — no emails will be sent'))

        campaigns = OutreachCampaign.objects.filter(status='active')
        if campaign_id:
            campaigns = campaigns.filter(id=campaign_id)

        if not campaigns.exists():
            self.stdout.write('  No active campaigns.')
            return

        grand_total = {'e1': 0, 'e2': 0, 'e3': 0, 'enriched': 0, 'skipped': 0, 'errors': 0}
        sends_left = max_sends

        for campaign in campaigns:
            stats = self._process_campaign(campaign, now, dry_run, no_enrich, sends_left)

            grand_total['enriched'] += stats['enriched']
            grand_total['e1'] += stats['e1']
            grand_total['e2'] += stats['e2']
            grand_total['e3'] += stats['e3']
            grand_total['skipped'] += stats['skipped']
            grand_total['errors'] += stats['errors']

            sent_this = stats['e1'] + stats['e2'] + stats['e3']
            if sends_left is not None:
                sends_left = max(0, sends_left - sent_this)
                if sends_left == 0:
                    self.stdout.write(self.style.WARNING('  Global send limit reached.'))
                    break

        total_sent = grand_total['e1'] + grand_total['e2'] + grand_total['e3']
        self.stdout.write(self.style.SUCCESS(
            f'Done: {total_sent} sent (E1:{grand_total["e1"]} E2:{grand_total["e2"]} E3:{grand_total["e3"]}) '
            f'| enriched:{grand_total["enriched"]} | skipped:{grand_total["skipped"]} | errors:{grand_total["errors"]}'
        ))

    def _process_campaign(self, campaign, now, dry_run, no_enrich, max_sends):
        stats = {'enriched': 0, 'e1': 0, 'e2': 0, 'e3': 0, 'skipped': 0, 'errors': 0}

        self.stdout.write(f'\n  Campaign: {campaign.name} (ID:{campaign.id})')

        # Check how many sent today for this campaign
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = GeneratedEmail.objects.filter(
            prospect__campaign=campaign,
            sent_at__gte=today_start,
            status__in=['sent', 'opened', 'replied'],
        ).count()
        daily_left = max(0, campaign.daily_send_limit - sent_today)
        self.stdout.write(f'    Sent today: {sent_today}/{campaign.daily_send_limit} (room for {daily_left})')

        if daily_left == 0:
            self.stdout.write(self.style.WARNING('    Daily limit reached, skipping.'))
            return stats

        sender = get_email_sender(campaign)
        delay_days = campaign.followup_delay_days or 3

        # Step 1: Enrich unenriched prospects (up to 10 per run)
        if not no_enrich:
            unenriched = campaign.prospects.filter(
                enrichment_data={}, website_url__gt='',
            ).exclude(status='bounced')[:10]

            for p in unenriched:
                data = enrich_prospect(p)
                if data:
                    p.enrichment_data = data
                    p.save(update_fields=['enrichment_data', 'updated_at'])
                    stats['enriched'] += 1

        # Step 2: Email 1 — new prospects (never emailed)
        new_prospects = campaign.prospects.filter(status='new').order_by('created_at')[:daily_left]
        for prospect in new_prospects:
            if max_sends is not None and (stats['e1'] + stats['e2'] + stats['e3']) >= max_sends:
                break
            result = self._send_sequence_email(
                prospect, campaign, 1, sender, now, dry_run,
            )
            if result == 'sent':
                stats['e1'] += 1
                daily_left -= 1
            elif result == 'skipped':
                stats['skipped'] += 1
            elif result == 'error':
                stats['errors'] += 1
            if daily_left <= 0:
                break

        # Step 3: Email 2 — follow-up (delay_days after Email 1)
        e2_cutoff = now - timedelta(days=delay_days)
        e2_prospects = campaign.prospects.filter(
            status='email1_sent',
            email1_sent_at__lte=e2_cutoff,
        ).exclude(replied_at__isnull=False)

        for prospect in e2_prospects:
            if max_sends is not None and (stats['e1'] + stats['e2'] + stats['e3']) >= max_sends:
                break
            if GeneratedEmail.objects.filter(prospect=prospect, sequence_number=2).exists():
                continue
            result = self._send_sequence_email(
                prospect, campaign, 2, sender, now, dry_run,
            )
            if result == 'sent':
                stats['e2'] += 1
                daily_left -= 1
            elif result == 'error':
                stats['errors'] += 1
            if daily_left <= 0:
                break

        # Step 4: Email 3 — final touch (delay_days after Email 2, if sequence_count >= 3)
        if campaign.email_sequence_count >= 3:
            e3_cutoff = now - timedelta(days=delay_days + 1)
            e3_prospects = campaign.prospects.filter(
                status='email2_sent',
                email2_sent_at__lte=e3_cutoff,
            ).exclude(replied_at__isnull=False)

            for prospect in e3_prospects:
                if max_sends is not None and (stats['e1'] + stats['e2'] + stats['e3']) >= max_sends:
                    break
                if GeneratedEmail.objects.filter(prospect=prospect, sequence_number=3).exists():
                    continue
                result = self._send_sequence_email(
                    prospect, campaign, 3, sender, now, dry_run,
                )
                if result == 'sent':
                    stats['e3'] += 1
                    daily_left -= 1
                elif result == 'error':
                    stats['errors'] += 1
                if daily_left <= 0:
                    break

        # Update campaign metrics
        if not dry_run:
            self._update_metrics(campaign)

        self.stdout.write(
            f'    Result: E1:{stats["e1"]} E2:{stats["e2"]} E3:{stats["e3"]} '
            f'skipped:{stats["skipped"]} errors:{stats["errors"]}'
        )
        return stats

    def _send_sequence_email(self, prospect, campaign, seq_num, sender, now, dry_run):
        """Send a single sequence email. Returns 'sent', 'skipped', or 'error'."""
        if is_unsubscribed(prospect.contact_email):
            return 'skipped'

        allowed, remaining, reason = can_send_today(campaign.max_emails_per_day)
        if not allowed:
            self.stdout.write(f'    [LIMIT] {reason}')
            return 'skipped'

        # Generate email via AI
        email_content = generate_email(prospect, campaign, seq_num)
        if not email_content:
            self.stdout.write(self.style.WARNING(
                f'    [FAIL] AI gen failed: {prospect.business_name} (E{seq_num})'
            ))
            return 'error'

        tracking_id = uuid.uuid4().hex[:16]
        gen_email = GeneratedEmail(
            prospect=prospect,
            sequence_number=seq_num,
            subject=email_content['subject'],
            body=email_content['body'],
            ai_model_used=email_content.get('model_used', ''),
            status='draft',
            tracking_id=tracking_id,
        )

        if dry_run:
            self.stdout.write(
                f'    [DRY] E{seq_num} -> {prospect.business_name} ({prospect.contact_email}) | '
                f'{email_content["subject"][:50]}'
            )
            return 'sent'  # Count as sent for dry-run reporting

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

            status_field = f'email{seq_num}_sent'
            sent_at_field = f'email{seq_num}_sent_at'
            prospect.status = f'email{seq_num}_sent'
            setattr(prospect, sent_at_field, now)
            prospect.save(update_fields=['status', sent_at_field, 'updated_at'])

            record_send()
            self.stdout.write(f'    [SENT] E{seq_num} -> {prospect.business_name}')

            # Throttle between sends
            time.sleep(2)
            return 'sent'
        else:
            gen_email.status = 'bounced' if 'bounce' in result.get('error', '') else 'draft'
            gen_email.save()
            if 'bounce' in result.get('error', ''):
                prospect.status = 'bounced'
                prospect.save(update_fields=['status', 'updated_at'])
            self.stdout.write(self.style.WARNING(
                f'    [FAIL] E{seq_num} -> {prospect.business_name}: {result.get("error", "unknown")}'
            ))
            return 'error'

    def _update_metrics(self, campaign):
        """Refresh campaign aggregate metrics from GeneratedEmail records."""
        base_qs = GeneratedEmail.objects.filter(prospect__campaign=campaign)
        campaign.emails_sent = base_qs.filter(status__in=['sent', 'opened', 'replied']).count()
        campaign.emails_opened = base_qs.filter(status__in=['opened', 'replied']).count()
        campaign.emails_replied = base_qs.filter(status='replied').count()
        campaign.emails_bounced = base_qs.filter(status='bounced').count()
        campaign.total_prospects = campaign.prospects.count()
        campaign.save(update_fields=[
            'emails_sent', 'emails_opened', 'emails_replied',
            'emails_bounced', 'total_prospects',
        ])
