from django.db import models
from .business import BusinessProfile


class ProspectBusiness(models.Model):
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    zip_code = models.CharField(max_length=10, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    google_rating = models.FloatField(null=True, blank=True)
    google_review_count = models.IntegerField(null=True, blank=True)
    google_place_id = models.CharField(max_length=200, blank=True)
    owner_name = models.CharField(max_length=200, blank=True)
    owner_email = models.EmailField(blank=True)
    email_validated = models.BooleanField(default=False)
    email_validation_status = models.CharField(max_length=20, blank=True)
    source = models.CharField(max_length=50, blank=True)
    raw_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Prospect Businesses'

    def __str__(self):
        return self.name


class OutreachCampaign(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
    ]

    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    target_business_types = models.JSONField(default=list)
    target_zip_codes = models.JSONField(default=list)
    target_radius_miles = models.IntegerField(null=True, blank=True)
    email_subject_template = models.CharField(max_length=200, blank=True)
    email_body_template = models.TextField(blank=True)
    use_ai_personalization = models.BooleanField(default=True)
    max_emails_per_day = models.IntegerField(default=25)
    followup_delay_days = models.IntegerField(default=3)
    max_followups = models.IntegerField(default=2)
    total_prospects = models.IntegerField(default=0)
    emails_sent = models.IntegerField(default=0)
    emails_opened = models.IntegerField(default=0)
    emails_replied = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.status})"


class OutreachEmail(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('opened', 'Opened'),
        ('replied', 'Replied'),
        ('bounced', 'Bounced'),
        ('failed', 'Failed'),
    ]

    campaign = models.ForeignKey(OutreachCampaign, on_delete=models.CASCADE, related_name='emails')
    prospect = models.ForeignKey(ProspectBusiness, on_delete=models.CASCADE, related_name='outreach_emails')
    sequence_number = models.IntegerField(default=1)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Email to {self.prospect.name} (#{self.sequence_number})"
