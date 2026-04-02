from django.db import models
from django.contrib.auth.models import User
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

    STYLE_CHOICES = [
        ('professional', 'Professional'),
        ('friendly', 'Friendly'),
        ('direct', 'Direct'),
    ]

    SEND_MODE_CHOICES = [
        ('salessignal', 'SalesSignal Email'),
        ('gmail', 'Connected Gmail'),
    ]

    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Targeting
    target_business_types = models.JSONField(default=list)
    target_zip_codes = models.JSONField(default=list)
    target_radius_miles = models.IntegerField(null=True, blank=True)
    target_category = models.CharField(max_length=200, blank=True,
        help_text='What kind of businesses to prospect (e.g. "property manager", "restaurant")')
    target_location = models.CharField(max_length=200, blank=True,
        help_text='City, zip, or area to target')

    # Email templates (legacy - kept for backward compat)
    email_subject_template = models.CharField(max_length=200, blank=True)
    email_body_template = models.TextField(blank=True)
    use_ai_personalization = models.BooleanField(default=True)

    # AI email generation settings
    email_sequence_count = models.IntegerField(default=3,
        help_text='Number of emails in the sequence (1-3)')
    email_style = models.CharField(max_length=20, choices=STYLE_CHOICES, default='professional')
    customer_custom_instructions = models.TextField(blank=True,
        help_text='Custom instructions for AI email generation (e.g. "mention our 20 years experience")')

    # Sending settings
    max_emails_per_day = models.IntegerField(default=25)
    daily_send_limit = models.IntegerField(default=15,
        help_text='New prospects per day (rest reserved for follow-ups)')
    followup_delay_days = models.IntegerField(default=3)
    max_followups = models.IntegerField(default=2)

    # Sending identity
    sending_email = models.EmailField(blank=True,
        help_text='Email address to send from')
    reply_to_email = models.EmailField(blank=True,
        help_text="Customer's real email for replies")
    send_mode = models.CharField(max_length=20, choices=SEND_MODE_CHOICES, default='salessignal')

    # Metrics
    total_prospects = models.IntegerField(default=0)
    emails_sent = models.IntegerField(default=0)
    emails_opened = models.IntegerField(default=0)
    emails_replied = models.IntegerField(default=0)
    emails_bounced = models.IntegerField(default=0)

    # AI cost tracking
    ai_total_tokens = models.IntegerField(default=0)
    ai_estimated_cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.status})"

    @property
    def open_rate(self):
        if self.emails_sent == 0:
            return 0
        return round(self.emails_opened / self.emails_sent * 100)

    @property
    def reply_rate(self):
        if self.emails_sent == 0:
            return 0
        return round(self.emails_replied / self.emails_sent * 100)

    @property
    def bounce_rate(self):
        if self.emails_sent == 0:
            return 0
        return round(self.emails_bounced / self.emails_sent * 100)


class OutreachEmail(models.Model):
    """Legacy email model - kept for backward compatibility."""
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


class OutreachProspect(models.Model):
    """Per-campaign prospect with status tracking and enrichment data."""
    STATUS_CHOICES = [
        ('new', 'New'),
        ('email1_sent', 'Email 1 Sent'),
        ('email2_sent', 'Email 2 Sent'),
        ('email3_sent', 'Email 3 Sent'),
        ('replied', 'Replied'),
        ('interested', 'Interested'),
        ('not_interested', 'Not Interested'),
        ('bounced', 'Bounced'),
    ]

    SOURCE_CHOICES = [
        ('google_maps', 'Google Maps'),
        ('manual_upload', 'Manual Upload'),
        ('customer_list', 'Customer List'),
        ('no_website_lead', 'No Website Lead'),
    ]

    campaign = models.ForeignKey(OutreachCampaign, on_delete=models.CASCADE, related_name='prospects')
    prospect_business = models.ForeignKey(ProspectBusiness, on_delete=models.CASCADE,
        related_name='campaign_prospects', null=True, blank=True)

    # Prospect info (denormalized for display even if ProspectBusiness deleted)
    business_name = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=20, blank=True)
    website_url = models.URLField(blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='google_maps')

    # Status pipeline
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    # Email send tracking
    email1_sent_at = models.DateTimeField(null=True, blank=True)
    email2_sent_at = models.DateTimeField(null=True, blank=True)
    email3_sent_at = models.DateTimeField(null=True, blank=True)
    email1_opened = models.BooleanField(default=False)
    email2_opened = models.BooleanField(default=False)
    email3_opened = models.BooleanField(default=False)

    # Reply tracking
    replied_at = models.DateTimeField(null=True, blank=True)
    reply_text = models.TextField(blank=True)
    reply_classification = models.CharField(max_length=20, blank=True,
        help_text='AI classification: interested/not_interested/question/out_of_office')

    # AI enrichment from website scraping
    enrichment_data = models.JSONField(default=dict, blank=True,
        help_text='Scraped website info, owner name, business details for AI personalization')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['campaign', 'contact_email']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.business_name} ({self.status})"


class GeneratedEmail(models.Model):
    """AI-generated email content per prospect per sequence."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('sent', 'Sent'),
        ('opened', 'Opened'),
        ('replied', 'Replied'),
        ('bounced', 'Bounced'),
    ]

    prospect = models.ForeignKey(OutreachProspect, on_delete=models.CASCADE,
        related_name='generated_emails')
    sequence_number = models.IntegerField(default=1,
        help_text='1=intro, 2=follow-up day 3, 3=final touch day 7')
    subject = models.CharField(max_length=300)
    body = models.TextField()
    ai_model_used = models.CharField(max_length=100, blank=True,
        help_text='Which AI model generated this email')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    tracking_id = models.CharField(max_length=64, blank=True,
        help_text='Unique ID for open/click tracking')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['prospect', 'sequence_number']
        ordering = ['sequence_number']

    def __str__(self):
        return f"Email #{self.sequence_number} to {self.prospect.business_name}"
