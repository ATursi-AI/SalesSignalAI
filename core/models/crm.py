from django.conf import settings
from django.db import models

from .business import BusinessProfile
from .leads import Lead, LeadAssignment
from .outreach import ProspectBusiness, OutreachEmail


class Contact(models.Model):
    """
    Unified contact record for CRM. Every person the business interacts with
    — whether from an inbound lead or an outbound campaign — becomes a Contact.
    """
    SOURCE_CHOICES = [
        ('lead', 'Inbound Lead'),
        ('outreach', 'Outreach Prospect'),
        ('manual', 'Manual Entry'),
        ('referral', 'Referral'),
    ]

    STAGE_CHOICES = [
        ('new', 'New Lead'),
        ('contacted', 'Contacted'),
        ('follow_up', 'Follow-up'),
        ('quoted', 'Quoted'),
        ('won', 'Won'),
        ('lost', 'Lost'),
    ]

    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=300, blank=True)

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='lead')
    source_platform = models.CharField(max_length=50, blank=True)

    # Link to originating records
    source_lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name='contacts')
    source_assignment = models.ForeignKey(LeadAssignment, null=True, blank=True, on_delete=models.SET_NULL, related_name='contacts')
    source_prospect = models.ForeignKey(ProspectBusiness, null=True, blank=True, on_delete=models.SET_NULL, related_name='contacts')

    pipeline_stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='new')
    service_needed = models.CharField(max_length=200, blank=True)

    estimated_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    won_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    next_follow_up = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.name} ({self.get_pipeline_stage_display()})"

    @property
    def last_activity(self):
        return self.activities.order_by('-created_at').first()

    @property
    def activity_count(self):
        return self.activities.count()


class Activity(models.Model):
    """
    Timeline entry for a contact — every interaction is logged here.
    """
    TYPE_CHOICES = [
        ('note', 'Note'),
        ('call', 'Phone Call'),
        ('email_sent', 'Email Sent'),
        ('email_opened', 'Email Opened'),
        ('email_replied', 'Email Replied'),
        ('meeting', 'Meeting'),
        ('quoted', 'Quote Sent'),
        ('won', 'Deal Won'),
        ('lost', 'Deal Lost'),
        ('follow_up', 'Follow-up Set'),
        ('stage_change', 'Stage Changed'),
        ('lead_found', 'Lead Discovered'),
        ('appointment', 'Appointment'),
    ]

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='activities')
    activity_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.TextField()
    value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Link to email if applicable
    outreach_email = models.ForeignKey(OutreachEmail, null=True, blank=True, on_delete=models.SET_NULL)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Activities'

    def __str__(self):
        return f"{self.get_activity_type_display()} - {self.contact.name}"

    @property
    def icon(self):
        icons = {
            'note': 'bi-sticky-fill',
            'call': 'bi-telephone-fill',
            'email_sent': 'bi-envelope-fill',
            'email_opened': 'bi-envelope-open-fill',
            'email_replied': 'bi-reply-fill',
            'meeting': 'bi-people-fill',
            'quoted': 'bi-calculator-fill',
            'won': 'bi-trophy-fill',
            'lost': 'bi-x-circle-fill',
            'follow_up': 'bi-alarm-fill',
            'stage_change': 'bi-arrow-right-circle-fill',
            'lead_found': 'bi-lightning-fill',
            'appointment': 'bi-calendar-check-fill',
        }
        return icons.get(self.activity_type, 'bi-circle-fill')

    @property
    def color(self):
        colors = {
            'note': '#A0A0B8',
            'call': '#3B82F6',
            'email_sent': '#8B5CF6',
            'email_opened': '#F59E0B',
            'email_replied': '#10B981',
            'meeting': '#3B82F6',
            'quoted': '#F59E0B',
            'won': '#10B981',
            'lost': '#FF4757',
            'follow_up': '#F59E0B',
            'stage_change': '#3B82F6',
            'lead_found': '#FF4757',
            'appointment': '#10B981',
        }
        return colors.get(self.activity_type, '#A0A0B8')


class Appointment(models.Model):
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('completed', 'Completed'),
        ('no_show', 'No Show'),
        ('cancelled', 'Cancelled'),
    ]

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='appointments')
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='appointments')
    date = models.DateField()
    time = models.TimeField()
    duration_minutes = models.IntegerField(default=60)
    service_needed = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'time']

    def __str__(self):
        return f"{self.contact.name} - {self.date} {self.time}"
