"""
Sales Team CRM models — internal tool for SalesSignal's own sales team.
Tracks salespeople, prospects, activities, and deal pipeline.
"""
from django.db import models
from django.contrib.auth.models import User

from .business import BusinessProfile


class SalesPerson(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='salesperson_profile')
    phone = models.CharField(max_length=20, blank=True)
    territory = models.CharField(max_length=300, blank=True,
        help_text='Territory description, e.g. "Nassau County plumbers"')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    hire_date = models.DateField(null=True, blank=True)
    daily_call_goal = models.IntegerField(default=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Salespeople'

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.territory})"


class SalesProspect(models.Model):
    PIPELINE_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('callback', 'Callback'),
        ('demo_scheduled', 'Demo Scheduled'),
        ('demo_completed', 'Demo Completed'),
        ('proposal_sent', 'Proposal Sent'),
        ('closed_won', 'Closed Won'),
        ('closed_lost', 'Closed Lost'),
    ]

    SOURCE_CHOICES = [
        ('google_maps_scan', 'Google Maps Scan'),
        ('manual_entry', 'Manual Entry'),
        ('referral', 'Referral'),
        ('inbound', 'Inbound'),
    ]

    LOST_REASON_CHOICES = [
        ('too_expensive', 'Too Expensive'),
        ('not_interested', 'Not Interested'),
        ('using_competitor', 'Using Competitor'),
        ('no_response', 'No Response'),
        ('other', 'Other'),
    ]

    salesperson = models.ForeignKey(SalesPerson, on_delete=models.CASCADE, related_name='prospects')
    business_name = models.CharField(max_length=200)
    owner_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    zip_code = models.CharField(max_length=10, blank=True)

    service_category = models.CharField(max_length=100, blank=True,
        help_text='e.g. plumber, electrician, cleaner')
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='manual_entry')
    source_lead_id = models.IntegerField(null=True, blank=True,
        help_text='ID of the Lead record this was imported from')

    google_rating = models.FloatField(null=True, blank=True)
    google_review_count = models.IntegerField(null=True, blank=True)
    has_website = models.BooleanField(default=True)

    pipeline_stage = models.CharField(max_length=20, choices=PIPELINE_CHOICES, default='new')
    lost_reason = models.CharField(max_length=20, choices=LOST_REASON_CHOICES, blank=True)
    notes = models.TextField(blank=True)
    next_follow_up_date = models.DateField(null=True, blank=True)
    estimated_monthly_value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Estimated monthly subscription value')

    # Link to customer if converted
    converted_business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sales_origin')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.business_name} ({self.get_pipeline_stage_display()})"


class SalesActivity(models.Model):
    TYPE_CHOICES = [
        ('call', 'Call'),
        ('voicemail', 'Voicemail'),
        ('email', 'Email'),
        ('demo', 'Demo'),
        ('proposal', 'Proposal'),
        ('follow_up', 'Follow-up'),
        ('note', 'Note'),
        ('closed_won', 'Closed Won'),
        ('closed_lost', 'Closed Lost'),
    ]

    OUTCOME_CHOICES = [
        ('connected', 'Connected'),
        ('no_answer', 'No Answer'),
        ('voicemail', 'Voicemail'),
        ('busy', 'Busy'),
        ('wrong_number', 'Wrong Number'),
    ]

    prospect = models.ForeignKey(SalesProspect, on_delete=models.CASCADE, related_name='activities')
    salesperson = models.ForeignKey(SalesPerson, on_delete=models.CASCADE, related_name='activities')
    activity_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.TextField(blank=True)
    call_duration = models.IntegerField(null=True, blank=True,
        help_text='Call duration in seconds')
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Sales Activities'

    def __str__(self):
        return f"{self.get_activity_type_display()} — {self.prospect.business_name}"

    @property
    def icon(self):
        icons = {
            'call': 'bi-telephone',
            'voicemail': 'bi-voicemail',
            'email': 'bi-envelope',
            'demo': 'bi-display',
            'proposal': 'bi-file-earmark-text',
            'follow_up': 'bi-arrow-repeat',
            'note': 'bi-sticky',
            'closed_won': 'bi-trophy',
            'closed_lost': 'bi-x-circle',
        }
        return icons.get(self.activity_type, 'bi-dot')

    @property
    def color(self):
        colors = {
            'call': 'var(--accent-blue)',
            'voicemail': 'var(--accent-amber)',
            'email': 'var(--accent-blue)',
            'demo': 'var(--accent-emerald)',
            'proposal': 'var(--accent-amber)',
            'follow_up': 'var(--text-secondary)',
            'note': 'var(--text-muted)',
            'closed_won': 'var(--accent-emerald)',
            'closed_lost': 'var(--accent-coral)',
        }
        return colors.get(self.activity_type, 'var(--text-muted)')
