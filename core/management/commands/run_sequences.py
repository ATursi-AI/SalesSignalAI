"""
Process sales sequence steps that are due today.

Runs through all active enrollments where next_action_date <= today,
executes the current step (send email, create call task, etc.),
and advances to the next step.

Usage:
    python manage.py run_sequences                  # Process all due steps
    python manage.py run_sequences --dry-run        # Preview without executing
    python manage.py run_sequences --sequence 5     # Only process sequence #5
    python manage.py run_sequences --batch "austin-plumbers"  # Only this batch
"""
import logging
from datetime import date

from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone
from django.conf import settings

from core.models.sales_sequences import (
    SalesSequence, SequenceStep, SequenceEnrollment, SequenceStepLog,
)
from core.models.sales import SalesActivity

logger = logging.getLogger(__name__)


def _fill_placeholders(template, enrollment):
    """Replace {business_name}, {owner_name}, etc. in email/sms templates."""
    prospect = enrollment.prospect
    video_page = enrollment.video_page

    replacements = {
        '{business_name}': prospect.business_name or '',
        '{owner_name}': prospect.owner_name or '',
        '{first_name}': (prospect.owner_name or '').split()[0] if prospect.owner_name else '',
        '{trade}': prospect.service_category or '',
        '{city}': prospect.city or '',
        '{state}': prospect.state or '',
        '{phone}': prospect.phone or '',
        '{email}': prospect.email or '',
    }

    if video_page:
        video_url = f"https://www.salessignalai.com/demo/{video_page.slug}/"
        replacements['{video_link}'] = video_url
        replacements['{video_thumbnail}'] = video_page.video_thumbnail_url or ''
        replacements['{video_url}'] = video_url
    else:
        replacements['{video_link}'] = ''
        replacements['{video_thumbnail}'] = ''
        replacements['{video_url}'] = ''

    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def _send_email(enrollment, step):
    """Send an email via SendGrid for this step."""
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content

        prospect = enrollment.prospect
        if not prospect.email:
            logger.warning(f'[Sequences] No email for {prospect.business_name} — skipping email step')
            return 'skipped', 'No email address'

        subject = _fill_placeholders(step.email_subject, enrollment)
        body = _fill_placeholders(step.email_body, enrollment)

        sg_key = getattr(settings, 'SENDGRID_API_KEY', '')
        if not sg_key:
            logger.error('[Sequences] SENDGRID_API_KEY not configured')
            return 'failed', 'No SendGrid API key'

        seq = enrollment.sequence
        message = Mail(
            from_email=Email(seq.send_from_email, seq.send_from_name),
            to_emails=To(prospect.email),
            subject=subject,
            html_content=Content('text/html', body),
        )

        sg = sendgrid.SendGridAPIClient(api_key=sg_key)
        response = sg.send(message)

        if response.status_code in (200, 201, 202):
            enrollment.emails_sent = models.F('emails_sent') + 1
            enrollment.save(update_fields=['emails_sent', 'updated_at'])
            msg_id = response.headers.get('X-Message-Id', '')
            logger.info(f'[Sequences] Email sent to {prospect.email} — {subject[:50]}')
            return 'sent', msg_id
        else:
            logger.error(f'[Sequences] SendGrid error {response.status_code}')
            return 'failed', f'SendGrid {response.status_code}'

    except Exception as e:
        logger.error(f'[Sequences] Email send error: {e}')
        return 'failed', str(e)


def _create_call_task(enrollment, step):
    """Create a SalesActivity call task on the sales dashboard."""
    prospect = enrollment.prospect

    # Find the salesperson assigned to this prospect
    salesperson = prospect.salesperson

    activity = SalesActivity.objects.create(
        prospect=prospect,
        salesperson=salesperson,
        activity_type='call',
        description=step.call_script_notes or f'Sequence call: {step.name or "Follow-up call"}',
        is_task=True,
        task_due_date=date.today(),
        task_completed=False,
    )

    logger.info(f'[Sequences] Call task created for {prospect.business_name} — assigned to {salesperson}')
    return 'task_created', activity


def _send_sms(enrollment, step):
    """Send SMS via SignalWire."""
    prospect = enrollment.prospect
    if not prospect.phone:
        return 'skipped', 'No phone number'

    body = _fill_placeholders(step.sms_body, enrollment)

    try:
        from core.services.signalwire_service import send_sms
        send_sms(prospect.phone, body)
        logger.info(f'[Sequences] SMS sent to {prospect.phone}')
        return 'sent', ''
    except Exception as e:
        logger.error(f'[Sequences] SMS error: {e}')
        return 'failed', str(e)


def process_enrollment(enrollment, dry_run=False):
    """
    Process a single enrollment — execute current step and advance.
    Returns (step, result, detail) or None if nothing to do.
    """
    step = enrollment.sequence.steps.filter(
        step_number=enrollment.current_step
    ).first()

    if not step:
        # No step found for current_step — try to advance
        next_step = enrollment.advance_to_next_step()
        if not next_step:
            return None
        step = next_step

    # Check skip conditions
    if step.skip_if_replied and enrollment.replied:
        if not dry_run:
            SequenceStepLog.objects.create(
                enrollment=enrollment, step=step, result='skipped',
                notes='Prospect already replied'
            )
            enrollment.advance_to_next_step()
        return step, 'skipped', 'Already replied'

    if dry_run:
        return step, 'would_execute', step.get_step_type_display()

    # Execute based on step type
    result = 'skipped'
    detail = ''
    activity = None
    msg_id = ''

    if step.step_type in ('email', 'video_email'):
        result, msg_id = _send_email(enrollment, step)
        detail = msg_id

    elif step.step_type == 'call':
        result, activity = _create_call_task(enrollment, step)

    elif step.step_type == 'sms':
        result, detail = _send_sms(enrollment, step)

    elif step.step_type == 'wait':
        result = 'skipped'
        detail = f'Wait step — {step.delay_days} days'

    elif step.step_type == 'linkedin':
        # LinkedIn is manual — create a task reminder
        result, activity = _create_call_task(enrollment, step)

    # Log the execution
    log = SequenceStepLog.objects.create(
        enrollment=enrollment,
        step=step,
        result=result,
        sendgrid_message_id=msg_id if isinstance(msg_id, str) else '',
        email_subject_sent=_fill_placeholders(step.email_subject, enrollment) if step.email_subject else '',
        sales_activity=activity if isinstance(activity, SalesActivity) else None,
        video_page=enrollment.video_page,
    )

    # Advance to next step
    if result not in ('failed', 'bounced'):
        enrollment.advance_to_next_step()

    return step, result, detail


class Command(BaseCommand):
    help = 'Process sales sequence steps that are due today'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
            help='Preview what would happen without executing')
        parser.add_argument('--sequence', type=int,
            help='Only process a specific sequence ID')
        parser.add_argument('--batch', type=str,
            help='Only process enrollments with this batch tag')
        parser.add_argument('--prospect', type=int,
            help='Only process a specific prospect ID (for individual sends)')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        sequence_id = options.get('sequence')
        batch_tag = options.get('batch')
        prospect_id = options.get('prospect')

        today = date.today()

        # Get due enrollments
        qs = SequenceEnrollment.objects.filter(
            status='active',
            next_action_date__lte=today,
            sequence__status='active',
        ).select_related('prospect', 'sequence', 'video_page')

        if sequence_id:
            qs = qs.filter(sequence_id=sequence_id)
        if batch_tag:
            qs = qs.filter(batch_tag=batch_tag)
        if prospect_id:
            qs = qs.filter(prospect_id=prospect_id)

        enrollments = list(qs)

        if not enrollments:
            self.stdout.write('No due enrollments found.')
            return

        self.stdout.write(f'{"[DRY RUN] " if dry_run else ""}Processing {len(enrollments)} due enrollments...\n')

        stats = {'sent': 0, 'task_created': 0, 'skipped': 0, 'failed': 0}

        for enrollment in enrollments:
            result = process_enrollment(enrollment, dry_run=dry_run)
            if result is None:
                continue

            step, status, detail = result
            prefix = '[DRY RUN] ' if dry_run else ''
            self.stdout.write(
                f'  {prefix}{enrollment.prospect.business_name} — '
                f'Step {step.step_number} ({step.get_step_type_display()}) → {status}'
                f'{f" ({detail[:60]})" if detail else ""}'
            )

            if status in stats:
                stats[status] += 1

        self.stdout.write(f'\n{"[DRY RUN] " if dry_run else ""}Done! {stats}')
