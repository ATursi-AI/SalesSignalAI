"""
Email sending via SendGrid API.
Handles individual and batch email sending for outreach campaigns.
Integrates domain warming, bounce handling, unsubscribe checks, and CAN-SPAM compliance.
"""
import logging
import uuid

from django.conf import settings
from django.utils import timezone

from core.models import OutreachEmail, OutreachCampaign
from core.models.monitoring import Unsubscribe

logger = logging.getLogger(__name__)

# CAN-SPAM required physical address
PHYSICAL_ADDRESS = getattr(settings, 'COMPANY_PHYSICAL_ADDRESS',
    'SalesSignal AI, 123 Main St, New York, NY 10001')


def _append_unsubscribe_footer(body, to_email):
    """Append CAN-SPAM compliant unsubscribe footer to email body."""
    unsub_id = uuid.uuid4().hex[:16]
    footer = (
        f"\n\n---\n"
        f"You're receiving this because we thought our services might be relevant "
        f"to your business. If you'd prefer not to hear from us, reply with "
        f"\"unsubscribe\" or click: {{{{unsubscribe_url}}}}\n"
        f"{PHYSICAL_ADDRESS}\n"
        f"Ref: {unsub_id}"
    )
    return body + footer


def is_unsubscribed(email):
    """Check if an email is on the unsubscribe list."""
    return Unsubscribe.objects.filter(email__iexact=email.strip()).exists()


def add_unsubscribe(email, reason=''):
    """Add an email to the unsubscribe list."""
    email = email.lower().strip()
    _, created = Unsubscribe.objects.get_or_create(
        email=email,
        defaults={'reason': reason},
    )
    if created:
        logger.info(f'Added to unsubscribe list: {email}')
    return created


def send_email(to_email, subject, body, from_email=None, reply_to=None,
               skip_unsub_check=False):
    """
    Send a single email via SendGrid.
    Checks unsubscribe list and warming limits before sending.

    Returns:
        dict with 'success' (bool), 'message_id' (str), 'error' (str)
    """
    # Check unsubscribe list
    if not skip_unsub_check and is_unsubscribed(to_email):
        logger.info(f'Skipped {to_email} — on unsubscribe list')
        return {'success': False, 'message_id': '', 'error': 'unsubscribed'}

    api_key = getattr(settings, 'SENDGRID_API_KEY', '')
    if not api_key:
        logger.warning(f'SENDGRID_API_KEY not configured — would send to {to_email}')
        return {'success': False, 'message_id': '', 'error': 'api_not_configured'}

    from_email = from_email or getattr(settings, 'ALERT_FROM_EMAIL', 'noreply@salessignal.ai')

    # Append CAN-SPAM footer
    body = _append_unsubscribe_footer(body, to_email)

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content

        sg = sendgrid.SendGridAPIClient(api_key=api_key)

        message = Mail(
            from_email=Email(from_email),
            to_emails=To(to_email),
            subject=subject,
            plain_text_content=Content('text/plain', body),
        )

        if reply_to:
            message.reply_to = Email(reply_to)

        response = sg.client.mail.send.post(request_body=message.get())

        if response.status_code in (200, 201, 202):
            message_id = response.headers.get('X-Message-Id', '')
            logger.info(f'Email sent to {to_email} (ID: {message_id})')

            # Record send in warming tracker
            from .warming import record_send
            record_send()

            return {'success': True, 'message_id': message_id, 'error': ''}
        else:
            logger.error(f'SendGrid error: {response.status_code} - {response.body}')
            return {'success': False, 'message_id': '', 'error': f'status_{response.status_code}'}

    except ImportError:
        logger.warning('sendgrid package not installed')
        return {'success': False, 'message_id': '', 'error': 'package_not_installed'}
    except Exception as e:
        logger.error(f'SendGrid send failed: {e}')
        return {'success': False, 'message_id': '', 'error': str(e)}


def handle_bounce(to_email, bounce_type='hard'):
    """
    Handle a bounced email. Hard bounces add to unsubscribe list.
    Called by SendGrid webhook or management command.
    """
    from .warming import record_bounce
    record_bounce()

    logger.warning(f'Email bounced ({bounce_type}): {to_email}')

    if bounce_type == 'hard':
        add_unsubscribe(to_email, reason=f'hard_bounce')

    # Mark any queued emails to this address as failed
    OutreachEmail.objects.filter(
        prospect__email__iexact=to_email,
        status='queued',
    ).update(status='failed')

    OutreachEmail.objects.filter(
        prospect__owner_email__iexact=to_email,
        status='queued',
    ).update(status='failed')


def handle_complaint(to_email):
    """
    Handle a spam complaint. Always adds to unsubscribe list.
    Called by SendGrid webhook.
    """
    from .warming import record_complaint
    record_complaint()

    add_unsubscribe(to_email, reason='spam_complaint')
    logger.warning(f'Spam complaint from {to_email} — added to unsubscribe list')


def send_outreach_email(outreach_email_id):
    """
    Send a specific OutreachEmail record and update its status.
    Respects warming limits and unsubscribe list.
    """
    try:
        oe = OutreachEmail.objects.select_related('prospect', 'campaign', 'campaign__business').get(
            id=outreach_email_id,
        )
    except OutreachEmail.DoesNotExist:
        return False

    if oe.status != 'queued':
        logger.info(f'OutreachEmail {oe.id} already has status {oe.status}')
        return False

    to_email = oe.prospect.email or oe.prospect.owner_email
    if not to_email:
        oe.status = 'failed'
        oe.save(update_fields=['status'])
        return False

    # Check warming limits
    from .warming import can_send_today
    allowed, remaining, reason = can_send_today(oe.campaign.max_emails_per_day)
    if not allowed:
        logger.info(f'Send deferred for OutreachEmail {oe.id}: {reason}')
        return False

    reply_to = oe.campaign.business.email or None

    result = send_email(
        to_email=to_email,
        subject=oe.subject,
        body=oe.body,
        reply_to=reply_to,
    )

    if result['error'] == 'unsubscribed':
        oe.status = 'failed'
        oe.save(update_fields=['status'])
        return False

    if result['success']:
        oe.status = 'sent'
        oe.sent_at = timezone.now()
        oe.save(update_fields=['status', 'sent_at'])

        # Update campaign counters
        campaign = oe.campaign
        campaign.emails_sent = campaign.emails.filter(status__in=['sent', 'delivered', 'opened', 'replied']).count()
        campaign.save(update_fields=['emails_sent'])

        return True
    else:
        oe.status = 'failed'
        oe.save(update_fields=['status'])
        logger.error(f'Failed to send OutreachEmail {oe.id}: {result["error"]}')
        return False


def process_campaign_queue(campaign_id):
    """
    Process queued emails for a campaign, respecting warming limits and daily caps.

    Returns:
        dict with counts: sent, failed, remaining, reason
    """
    try:
        campaign = OutreachCampaign.objects.get(id=campaign_id)
    except OutreachCampaign.DoesNotExist:
        return {'sent': 0, 'failed': 0, 'remaining': 0, 'reason': 'not_found'}

    if campaign.status != 'active':
        return {'sent': 0, 'failed': 0, 'remaining': 0, 'reason': 'not_active'}

    # Check warming limits
    from .warming import can_send_today
    allowed, remaining_warming, reason = can_send_today(campaign.max_emails_per_day)
    if not allowed:
        remaining = campaign.emails.filter(status='queued').count()
        return {'sent': 0, 'failed': 0, 'remaining': remaining, 'reason': reason}

    # Count emails sent today for this campaign
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = campaign.emails.filter(
        sent_at__gte=today_start,
        status__in=['sent', 'delivered', 'opened', 'replied'],
    ).count()

    campaign_remaining = max(0, campaign.max_emails_per_day - sent_today)
    batch_size = min(remaining_warming, campaign_remaining)

    queued = campaign.emails.filter(status='queued').order_by('created_at')[:batch_size]

    stats = {'sent': 0, 'failed': 0, 'remaining': 0, 'reason': 'ok'}

    for oe in queued:
        success = send_outreach_email(oe.id)
        if success:
            stats['sent'] += 1
        else:
            stats['failed'] += 1

    stats['remaining'] = campaign.emails.filter(status='queued').count()

    logger.info(f'Campaign {campaign.name} queue processed: {stats}')
    return stats
