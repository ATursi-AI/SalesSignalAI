"""
Sales Sequence Engine — automated drip sequences for outbound prospecting.

Supports individual high-value targets or small batch campaigns.
Each sequence has ordered steps (email, call, video email, wait)
and prospects are enrolled individually or in bulk.

Integrates with:
- SalesProspect (the prospect record)
- ProspectVideo (personalized video landing pages)
- SalesActivity (call tasks surface on sales dashboard)
- SendGrid (email delivery)
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class SalesSequence(models.Model):
    """
    A reusable sequence template.
    E.g. "Video Drip - Plumbers" with 5 steps over 14 days.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('archived', 'Archived'),
    ]

    name = models.CharField(max_length=200,
        help_text='E.g. "Video Drip - Plumbers" or "High Value Target Sequence"')
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')

    # Targeting context (informational, helps filter)
    target_trade = models.CharField(max_length=100, blank=True,
        help_text='E.g. "plumber", "electrician" — or blank for any trade')
    target_region = models.CharField(max_length=200, blank=True,
        help_text='E.g. "Austin TX", "Nassau County NY"')

    # Sending config
    send_from_name = models.CharField(max_length=100, default='SalesSignal AI')
    send_from_email = models.EmailField(default='outreach@salessignalai.com')
    daily_send_limit = models.IntegerField(default=50,
        help_text='Max emails per day across all enrollments in this sequence')

    # Stats (denormalized for dashboard speed)
    total_enrolled = models.IntegerField(default=0)
    total_completed = models.IntegerField(default=0)
    total_replied = models.IntegerField(default=0)
    total_converted = models.IntegerField(default=0)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name_plural = 'Sales Sequences'

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @property
    def step_count(self):
        return self.steps.count()

    @property
    def active_enrollments(self):
        return self.enrollments.filter(status='active').count()


class SequenceStep(models.Model):
    """
    One step in a sequence. Steps execute in order by step_number.
    Each step has a delay (days from enrollment or previous step).
    """
    STEP_TYPE_CHOICES = [
        ('email', 'Send Email'),
        ('video_email', 'Send Video Email'),
        ('call', 'Phone Call Task'),
        ('sms', 'Send SMS'),
        ('wait', 'Wait Period'),
        ('linkedin', 'LinkedIn Touch'),
    ]

    sequence = models.ForeignKey(SalesSequence, on_delete=models.CASCADE, related_name='steps')
    step_number = models.IntegerField(
        help_text='Order of execution. Step 1 runs first.')
    step_type = models.CharField(max_length=20, choices=STEP_TYPE_CHOICES)
    name = models.CharField(max_length=200, blank=True,
        help_text='E.g. "Intro Video Email", "Follow-up Call"')

    # Timing
    delay_days = models.IntegerField(default=0,
        help_text='Days to wait after previous step completes. 0 = same day as previous.')

    # Email content (for email and video_email types)
    email_subject = models.CharField(max_length=300, blank=True,
        help_text='Supports {business_name}, {owner_name}, {trade}, {city} placeholders')
    email_body = models.TextField(blank=True,
        help_text='HTML email body. Supports same placeholders + {video_link}, {video_thumbnail}')
    use_ai_personalization = models.BooleanField(default=False,
        help_text='Let AI rewrite the email for each prospect')

    # Call task config
    call_script_notes = models.TextField(blank=True,
        help_text='Talking points for the call task')
    call_priority = models.CharField(max_length=10, default='normal',
        choices=[('high', 'High'), ('normal', 'Normal'), ('low', 'Low')])

    # SMS content
    sms_body = models.CharField(max_length=320, blank=True,
        help_text='SMS text. Supports {business_name}, {first_name}, {video_link}')

    # Conditions
    skip_if_replied = models.BooleanField(default=True,
        help_text='Skip this step if prospect already replied')
    skip_if_opened = models.BooleanField(default=False,
        help_text='Skip this step if prospect opened a previous email')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence', 'step_number']
        unique_together = ['sequence', 'step_number']

    def __str__(self):
        return f"Step {self.step_number}: {self.name or self.get_step_type_display()}"


class SequenceEnrollment(models.Model):
    """
    One prospect enrolled in one sequence.
    Tracks their progress through the steps.
    Can be created individually (high-value target) or in bulk.
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
        ('replied', 'Replied'),
        ('converted', 'Converted'),
        ('bounced', 'Bounced'),
        ('opted_out', 'Opted Out'),
        ('removed', 'Removed'),
    ]

    sequence = models.ForeignKey(SalesSequence, on_delete=models.CASCADE, related_name='enrollments')
    prospect = models.ForeignKey('SalesProspect', on_delete=models.CASCADE, related_name='sequence_enrollments')

    # Optional link to their video page
    video_page = models.ForeignKey('ProspectVideo', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enrollments',
        help_text='Personalized video landing page for this prospect')

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='active')
    current_step = models.IntegerField(default=0,
        help_text='The step_number they are currently on. 0 = not started.')
    next_action_date = models.DateField(null=True, blank=True,
        help_text='When the next step should fire')

    # Engagement tracking
    emails_sent = models.IntegerField(default=0)
    emails_opened = models.IntegerField(default=0)
    emails_clicked = models.IntegerField(default=0)
    calls_made = models.IntegerField(default=0)
    replied = models.BooleanField(default=False)
    replied_at = models.DateTimeField(null=True, blank=True)
    reply_sentiment = models.CharField(max_length=20, blank=True,
        choices=[('interested', 'Interested'), ('not_interested', 'Not Interested'),
                 ('question', 'Question'), ('out_of_office', 'Out of Office')])

    # Batch tracking
    batch_tag = models.CharField(max_length=100, blank=True,
        help_text='Tag for grouping batch enrollments. E.g. "austin-plumbers-apr-2026"')

    enrolled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['next_action_date', '-enrolled_at']
        unique_together = ['sequence', 'prospect']  # Can't enroll same prospect twice in same sequence

    def __str__(self):
        return f"{self.prospect.business_name} → {self.sequence.name} (Step {self.current_step})"

    def advance_to_next_step(self):
        """Move to the next step and calculate next_action_date."""
        next_step = self.sequence.steps.filter(
            step_number__gt=self.current_step
        ).first()

        if next_step:
            self.current_step = next_step.step_number
            self.next_action_date = timezone.now().date() + timezone.timedelta(days=next_step.delay_days)
            self.save(update_fields=['current_step', 'next_action_date', 'updated_at'])
            return next_step
        else:
            # Sequence complete
            self.status = 'completed'
            self.completed_at = timezone.now()
            self.next_action_date = None
            self.save(update_fields=['status', 'completed_at', 'next_action_date', 'updated_at'])
            return None

    def mark_replied(self, sentiment='interested'):
        """Mark prospect as replied — stops the sequence."""
        self.status = 'replied'
        self.replied = True
        self.replied_at = timezone.now()
        self.reply_sentiment = sentiment
        self.next_action_date = None
        self.save(update_fields=[
            'status', 'replied', 'replied_at', 'reply_sentiment',
            'next_action_date', 'updated_at'
        ])
        # Update sequence stats
        SalesSequence.objects.filter(pk=self.sequence_id).update(
            total_replied=models.F('total_replied') + 1
        )

    def mark_converted(self):
        """Prospect closed — won the deal."""
        self.status = 'converted'
        self.completed_at = timezone.now()
        self.next_action_date = None
        self.save(update_fields=['status', 'completed_at', 'next_action_date', 'updated_at'])
        SalesSequence.objects.filter(pk=self.sequence_id).update(
            total_converted=models.F('total_converted') + 1
        )


class SequenceStepLog(models.Model):
    """
    Log of every step execution. Immutable audit trail.
    """
    RESULT_CHOICES = [
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('opened', 'Opened'),
        ('clicked', 'Clicked'),
        ('replied', 'Replied'),
        ('bounced', 'Bounced'),
        ('skipped', 'Skipped'),
        ('failed', 'Failed'),
        ('task_created', 'Task Created'),
        ('task_completed', 'Task Completed'),
    ]

    enrollment = models.ForeignKey(SequenceEnrollment, on_delete=models.CASCADE, related_name='step_logs')
    step = models.ForeignKey(SequenceStep, on_delete=models.CASCADE, related_name='logs')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)

    # Email tracking
    sendgrid_message_id = models.CharField(max_length=200, blank=True)
    email_subject_sent = models.CharField(max_length=300, blank=True)
    email_opened_at = models.DateTimeField(null=True, blank=True)
    email_clicked_at = models.DateTimeField(null=True, blank=True)

    # Call task link
    sales_activity = models.ForeignKey('SalesActivity', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sequence_step_logs',
        help_text='The SalesActivity task created for call steps')

    # Video page link
    video_page = models.ForeignKey('ProspectVideo', on_delete=models.SET_NULL,
        null=True, blank=True)

    notes = models.TextField(blank=True)
    executed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-executed_at']

    def __str__(self):
        return f"{self.enrollment.prospect.business_name} — Step {self.step.step_number} → {self.result}"
