from django.db import models
from .business import BusinessProfile


class WorkflowRule(models.Model):
    """If/then automation rules."""
    TRIGGER_CHOICES = [
        ('lead_status_changed', 'Lead Status Changed'),
        ('prospect_stage_changed', 'Prospect Stage Changed'),
        ('email_replied', 'Email Replied'),
        ('email_opened', 'Email Opened'),
        ('call_completed', 'Call Completed'),
        ('no_response_days', 'No Response After X Days'),
        ('lead_assigned', 'Lead Assigned'),
        ('appointment_booked', 'Appointment Booked'),
        ('form_submitted', 'Form Submitted'),
    ]

    ACTION_CHOICES = [
        ('send_email', 'Send Email'),
        ('send_sms', 'Send SMS'),
        ('schedule_followup', 'Schedule Follow-up'),
        ('change_stage', 'Change Pipeline Stage'),
        ('assign_to_rep', 'Assign to Rep'),
        ('create_task', 'Create Task'),
        ('notify_admin', 'Notify Admin'),
        ('add_to_campaign', 'Add to Campaign'),
        ('wait_days', 'Wait X Days'),
    ]

    business = models.ForeignKey(
        BusinessProfile, on_delete=models.CASCADE,
        related_name='workflow_rules', null=True, blank=True,
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    trigger = models.CharField(max_length=30, choices=TRIGGER_CHOICES)
    trigger_conditions = models.JSONField(
        default=dict, blank=True,
        help_text='JSON conditions, e.g. {"from_status": "new", "to_status": "contacted"}',
    )

    actions = models.JSONField(
        default=list,
        help_text='Ordered list of action dicts: [{"type": "send_email", "template": "...", "delay_days": 2}]',
    )

    times_triggered = models.IntegerField(default=0)
    last_triggered_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class WorkflowExecution(models.Model):
    """Log of each workflow trigger execution."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('waiting', 'Waiting (delay)'),
    ]

    rule = models.ForeignKey(WorkflowRule, on_delete=models.CASCADE, related_name='executions')
    triggered_by_model = models.CharField(max_length=50, blank=True)
    triggered_by_id = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    current_action_index = models.IntegerField(default=0)
    resume_at = models.DateTimeField(null=True, blank=True)
    result_log = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.rule.name} — {self.status} ({self.created_at:%Y-%m-%d %H:%M})"
