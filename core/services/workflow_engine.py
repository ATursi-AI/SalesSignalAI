"""
Workflow Automation Engine for SalesSignal AI.

Usage:
    from core.services.workflow_engine import trigger_workflow

    trigger_workflow('lead_status_changed', {
        'lead_id': 123,
        'from_status': 'unreviewed',
        'to_status': 'approved',
        'user_id': request.user.id,
    })

    trigger_workflow('prospect_stage_changed', {
        'prospect_id': 45,
        'from_stage': 'new',
        'to_stage': 'contacted',
    })
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from core.models.workflows import WorkflowRule, WorkflowExecution

logger = logging.getLogger(__name__)


def trigger_workflow(trigger_type, context):
    """
    Find all active workflow rules matching this trigger and execute them.
    Returns the number of rules triggered.
    """
    rules = WorkflowRule.objects.filter(trigger=trigger_type, is_active=True)
    triggered = 0

    for rule in rules:
        if not _conditions_match(rule.trigger_conditions, context):
            continue

        execution = WorkflowExecution.objects.create(
            rule=rule,
            triggered_by_model=context.get('model', ''),
            triggered_by_id=context.get('id') or context.get('lead_id') or context.get('prospect_id'),
            status='running',
        )

        rule.times_triggered += 1
        rule.last_triggered_at = timezone.now()
        rule.save(update_fields=['times_triggered', 'last_triggered_at'])

        _execute_actions(execution, context)
        triggered += 1

    return triggered


def resume_waiting_workflows():
    """
    Called by cron command. Resumes executions paused by wait_days actions.
    Returns the number of executions resumed.
    """
    now = timezone.now()
    waiting = WorkflowExecution.objects.filter(
        status='waiting',
        resume_at__lte=now,
    ).select_related('rule')

    resumed = 0
    for execution in waiting:
        execution.status = 'running'
        execution.save(update_fields=['status'])
        _execute_actions(execution, _rebuild_context(execution))
        resumed += 1

    return resumed


def _conditions_match(conditions, context):
    """Check if trigger_conditions dict matches the context dict."""
    if not conditions:
        return True

    for key, expected in conditions.items():
        actual = context.get(key)
        if actual is None:
            return False
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False

    return True


def _execute_actions(execution, context):
    """Execute actions starting from current_action_index."""
    actions = execution.rule.actions or []

    while execution.current_action_index < len(actions):
        action = actions[execution.current_action_index]
        action_type = action.get('type', '')

        try:
            if action_type == 'wait_days':
                days = int(action.get('days', 1))
                execution.resume_at = timezone.now() + timedelta(days=days)
                execution.status = 'waiting'
                execution.result_log.append({
                    'action': action_type,
                    'index': execution.current_action_index,
                    'status': 'waiting',
                    'resume_at': str(execution.resume_at),
                })
                execution.current_action_index += 1
                execution.save()
                return  # Pause here, will resume later

            result = _run_action(action_type, action, context)
            execution.result_log.append({
                'action': action_type,
                'index': execution.current_action_index,
                'status': 'ok' if result else 'no_op',
                'detail': str(result)[:200] if result else '',
            })
        except Exception as e:
            logger.exception(f'Workflow action failed: {action_type}')
            execution.result_log.append({
                'action': action_type,
                'index': execution.current_action_index,
                'status': 'error',
                'detail': str(e)[:200],
            })

        execution.current_action_index += 1

    # All actions completed
    execution.status = 'completed'
    execution.completed_at = timezone.now()
    execution.save()


def _run_action(action_type, action_config, context):
    """Execute a single action. Returns a result string or None."""
    handler = ACTION_HANDLERS.get(action_type)
    if handler:
        return handler(action_config, context)
    logger.warning(f'Unknown workflow action type: {action_type}')
    return None


# ─── Action Handlers ──────────────────────────────────────────────

def _action_send_email(config, context):
    """Send an email notification."""
    to = config.get('to') or context.get('email')
    subject = config.get('subject', 'SalesSignal AI Notification')
    body = config.get('body', '')

    # Template variable substitution
    for key, val in context.items():
        body = body.replace(f'{{{{{key}}}}}', str(val or ''))
        subject = subject.replace(f'{{{{{key}}}}}', str(val or ''))

    if to:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=True)
        return f'Email sent to {to}'
    return None


def _action_send_sms(config, context):
    """Send an SMS via SignalWire."""
    to = config.get('to') or context.get('phone')
    message = config.get('message', '')

    for key, val in context.items():
        message = message.replace(f'{{{{{key}}}}}', str(val or ''))

    if not to or not message:
        return None

    try:
        from core.models.call_center import SMSMessage
        sms = SMSMessage.objects.create(
            to_number=to,
            from_number=getattr(settings, 'SIGNALWIRE_SMS_NUMBER', ''),
            body=message,
            direction='outbound',
            status='queued',
        )
        return f'SMS queued: {sms.id}'
    except Exception as e:
        return f'SMS failed: {e}'


def _action_schedule_followup(config, context):
    """Set next_follow_up_date on a SalesProspect."""
    days = int(config.get('days', 1))
    prospect_id = context.get('prospect_id')
    if not prospect_id:
        return None

    from core.models.sales import SalesProspect
    updated = SalesProspect.objects.filter(pk=prospect_id).update(
        next_follow_up_date=timezone.now().date() + timedelta(days=days),
    )
    return f'Follow-up set +{days}d' if updated else None


def _action_change_stage(config, context):
    """Change a SalesProspect's pipeline stage."""
    new_stage = config.get('stage')
    prospect_id = context.get('prospect_id')
    if not prospect_id or not new_stage:
        return None

    from core.models.sales import SalesProspect
    updated = SalesProspect.objects.filter(pk=prospect_id).update(
        pipeline_stage=new_stage,
    )
    return f'Stage -> {new_stage}' if updated else None


def _action_assign_to_rep(config, context):
    """Assign a SalesProspect to a specific rep."""
    rep_id = config.get('rep_id')
    prospect_id = context.get('prospect_id')
    if not prospect_id or not rep_id:
        return None

    from core.models.sales import SalesProspect
    updated = SalesProspect.objects.filter(pk=prospect_id).update(
        salesperson_id=int(rep_id),
    )
    return f'Assigned to rep {rep_id}' if updated else None


def _action_create_task(config, context):
    """Create a SalesActivity as a task."""
    prospect_id = context.get('prospect_id')
    description = config.get('description', 'Follow up')

    for key, val in context.items():
        description = description.replace(f'{{{{{key}}}}}', str(val or ''))

    if not prospect_id:
        return None

    from core.models.sales import SalesActivity, SalesProspect
    prospect = SalesProspect.objects.filter(pk=prospect_id).first()
    if not prospect:
        return None

    SalesActivity.objects.create(
        prospect=prospect,
        salesperson=prospect.salesperson,
        activity_type='follow_up',
        description=description,
    )
    return f'Task created: {description[:50]}'


def _action_notify_admin(config, context):
    """Send notification email to admin."""
    subject = config.get('subject', 'Workflow Notification')
    message = config.get('message', '')

    for key, val in context.items():
        message = message.replace(f'{{{{{key}}}}}', str(val or ''))
        subject = subject.replace(f'{{{{{key}}}}}', str(val or ''))

    admin_email = getattr(settings, 'ALERT_FROM_EMAIL', 'alerts@salessignal.ai')
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [admin_email], fail_silently=True)
    return f'Admin notified: {subject}'


def _action_add_to_campaign(config, context):
    """Add a lead or prospect to an outreach campaign."""
    campaign_id = config.get('campaign_id')
    if not campaign_id:
        return None

    email = context.get('email')
    name = context.get('name') or context.get('business_name', '')
    if not email:
        return None

    from core.models.outreach import OutreachCampaign, OutreachProspect
    campaign = OutreachCampaign.objects.filter(pk=int(campaign_id)).first()
    if not campaign:
        return None

    if OutreachProspect.objects.filter(campaign=campaign, contact_email=email).exists():
        return 'Already in campaign'

    OutreachProspect.objects.create(
        campaign=campaign,
        business_name=name,
        contact_email=email,
        source='workflow',
        status='new',
    )
    campaign.total_prospects = campaign.prospects.count()
    campaign.save(update_fields=['total_prospects'])
    return f'Added {email} to campaign {campaign.name}'


ACTION_HANDLERS = {
    'send_email': _action_send_email,
    'send_sms': _action_send_sms,
    'schedule_followup': _action_schedule_followup,
    'change_stage': _action_change_stage,
    'assign_to_rep': _action_assign_to_rep,
    'create_task': _action_create_task,
    'notify_admin': _action_notify_admin,
    'add_to_campaign': _action_add_to_campaign,
}


def _rebuild_context(execution):
    """Rebuild context dict for a resumed execution."""
    ctx = {
        'model': execution.triggered_by_model,
        'id': execution.triggered_by_id,
    }

    if execution.triggered_by_model == 'SalesProspect' and execution.triggered_by_id:
        from core.models.sales import SalesProspect
        p = SalesProspect.objects.filter(pk=execution.triggered_by_id).first()
        if p:
            ctx.update({
                'prospect_id': p.id,
                'business_name': p.business_name,
                'phone': p.phone,
                'email': p.email,
            })

    elif execution.triggered_by_model == 'Lead' and execution.triggered_by_id:
        from core.models.leads import Lead
        lead = Lead.objects.filter(pk=execution.triggered_by_id).first()
        if lead:
            ctx.update({
                'lead_id': lead.id,
                'name': lead.contact_name,
                'phone': lead.contact_phone,
                'email': lead.contact_email,
            })

    return ctx
