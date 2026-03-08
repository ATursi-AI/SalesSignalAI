"""
Follow-up sequence logic and reply detection for outreach campaigns.
Handles automatic follow-up scheduling and inbound reply processing.
"""
import logging
from datetime import timedelta

from django.utils import timezone
from django.conf import settings

from core.models import OutreachCampaign, OutreachEmail, ProspectBusiness
from .ai_writer import generate_outreach_email

logger = logging.getLogger(__name__)


def schedule_followups(campaign_id):
    """
    Check for prospects due for follow-up and create follow-up OutreachEmail records.
    A follow-up is due when:
    - The previous email was sent >= followup_delay_days ago
    - The prospect hasn't replied
    - We haven't exceeded max_followups

    Returns:
        dict with counts: checked, scheduled
    """
    try:
        campaign = OutreachCampaign.objects.get(id=campaign_id)
    except OutreachCampaign.DoesNotExist:
        return {'checked': 0, 'scheduled': 0}

    if campaign.status != 'active':
        return {'checked': 0, 'scheduled': 0}

    stats = {'checked': 0, 'scheduled': 0}
    cutoff = timezone.now() - timedelta(days=campaign.followup_delay_days)

    # Find prospects with sent emails but no reply
    prospect_ids = (
        campaign.emails
        .filter(status__in=['sent', 'delivered', 'opened'])
        .values_list('prospect_id', flat=True)
        .distinct()
    )

    # Exclude prospects who replied
    replied_ids = set(
        campaign.emails
        .filter(status='replied')
        .values_list('prospect_id', flat=True)
    )

    for prospect_id in prospect_ids:
        if prospect_id in replied_ids:
            continue

        stats['checked'] += 1

        # Get the most recent email for this prospect
        last_email = (
            campaign.emails
            .filter(prospect_id=prospect_id)
            .order_by('-sequence_number')
            .first()
        )

        if not last_email:
            continue

        # Check if follow-up is due
        if not last_email.sent_at or last_email.sent_at > cutoff:
            continue  # Too recent

        # Check if max followups reached
        current_sequence = last_email.sequence_number
        if current_sequence > campaign.max_followups:
            continue

        # Check if a follow-up is already queued
        existing = campaign.emails.filter(
            prospect_id=prospect_id,
            sequence_number=current_sequence + 1,
        ).exists()
        if existing:
            continue

        # Generate follow-up email
        prospect = ProspectBusiness.objects.get(id=prospect_id)
        next_seq = current_sequence + 1

        if campaign.use_ai_personalization:
            email_content = generate_outreach_email(prospect, campaign, next_seq)
        else:
            from .ai_writer import _template_fallback
            email_content = _template_fallback(prospect, campaign, next_seq)

        if email_content:
            OutreachEmail.objects.create(
                campaign=campaign,
                prospect=prospect,
                sequence_number=next_seq,
                subject=email_content['subject'],
                body=email_content['body'],
                status='queued',
            )
            stats['scheduled'] += 1
            logger.info(f'Scheduled follow-up #{next_seq} for {prospect.name}')

    logger.info(f'Follow-up scheduling for {campaign.name}: {stats}')
    return stats


def process_reply(campaign_id, from_email, reply_body=''):
    """
    Process an inbound reply to an outreach email.
    Marks the email as replied and updates campaign metrics.

    Args:
        campaign_id: ID of the campaign
        from_email: email address of the replier
        reply_body: body of the reply (for logging)

    Returns:
        True if reply was matched and processed, False otherwise
    """
    try:
        campaign = OutreachCampaign.objects.get(id=campaign_id)
    except OutreachCampaign.DoesNotExist:
        return False

    from_email = from_email.lower().strip()

    # Find the prospect by email
    prospect = ProspectBusiness.objects.filter(
        email__iexact=from_email,
    ).first() or ProspectBusiness.objects.filter(
        owner_email__iexact=from_email,
    ).first()

    if not prospect:
        logger.warning(f'Reply from unknown email: {from_email}')
        return False

    # Find the most recent sent email for this prospect in this campaign
    outreach_email = (
        campaign.emails
        .filter(prospect=prospect, status__in=['sent', 'delivered', 'opened'])
        .order_by('-sequence_number')
        .first()
    )

    if not outreach_email:
        logger.warning(f'No matching outreach email for reply from {from_email}')
        return False

    outreach_email.status = 'replied'
    outreach_email.replied_at = timezone.now()
    outreach_email.save(update_fields=['status', 'replied_at'])

    # Update campaign metrics
    campaign.emails_replied = campaign.emails.filter(status='replied').count()
    campaign.save(update_fields=['emails_replied'])

    logger.info(f'Reply processed: {from_email} -> campaign {campaign.name}')
    return True


def check_sendgrid_inbound(campaign_id):
    """
    Check for replies via SendGrid Inbound Parse webhook data.
    This is called by a webhook endpoint or management command.

    In production, SendGrid Inbound Parse would POST to a webhook.
    For dev, this provides the processing logic that the webhook would call.
    """
    # In production: SendGrid Inbound Parse posts to a webhook URL.
    # The webhook view would call process_reply() with the parsed email data.
    # For now, this is a placeholder showing the integration pattern.
    logger.info(f'SendGrid inbound check for campaign {campaign_id} (webhook-based in production)')
    return {'checked': 0, 'replies_found': 0}
