"""
Engagement models — Voicemail Drops, Booking Pages, Review Campaigns.
New features to boost lead conversion and customer retention.
"""
import uuid
from django.db import models
from django.utils import timezone


# ── Voicemail Drops ─────────────────────────────────────────────────

class VoicemailDrop(models.Model):
    """Pre-recorded voicemail message template."""
    business = models.ForeignKey('BusinessProfile', on_delete=models.CASCADE, null=True, blank=True,
                                 help_text='Null = system-wide template')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    audio_url = models.URLField(max_length=500, help_text='URL to the pre-recorded audio file (MP3/WAV)')
    duration_seconds = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    times_used = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class VoicemailDropLog(models.Model):
    """Log of each voicemail drop sent."""
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('calling', 'Calling'),
        ('delivered', 'Delivered'),
        ('failed', 'Failed'),
        ('no_answer', 'No Answer'),
        ('busy', 'Busy'),
    ]

    voicemail = models.ForeignKey(VoicemailDrop, on_delete=models.CASCADE)
    to_number = models.CharField(max_length=20)
    call_sid = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    # Link to various source models
    lead = models.ForeignKey('Lead', on_delete=models.SET_NULL, null=True, blank=True)
    prospect = models.ForeignKey('SalesProspect', on_delete=models.SET_NULL, null=True, blank=True)
    salesperson = models.ForeignKey('SalesPerson', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'VM to {self.to_number} — {self.status}'


# ── Booking Pages ───────────────────────────────────────────────────

class BookingPage(models.Model):
    """Public-facing appointment booking page for a business."""
    business = models.ForeignKey('BusinessProfile', on_delete=models.CASCADE)
    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=200, blank=True, help_text='Page headline')
    description = models.TextField(blank=True, help_text='Shown below the headline')
    # Availability
    available_days = models.JSONField(default=list, blank=True,
                                      help_text='List of weekday ints: 0=Mon .. 6=Sun')
    start_time = models.TimeField(default='09:00')
    end_time = models.TimeField(default='17:00')
    slot_duration_minutes = models.IntegerField(default=30)
    max_bookings_per_day = models.IntegerField(default=10)
    # Appearance
    accent_color = models.CharField(max_length=7, default='#0D9488')
    show_phone = models.BooleanField(default=True)
    show_address = models.BooleanField(default=False)
    # Status
    is_active = models.BooleanField(default=True)
    page_views = models.IntegerField(default=0)
    bookings_made = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.business.business_name} — {self.slug}'

    def get_available_days_display(self):
        names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        return [names[d] for d in (self.available_days or []) if 0 <= d <= 6]


class BookingSubmission(models.Model):
    """A booking made through the public booking page."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
        ('no_show', 'No Show'),
    ]

    booking_page = models.ForeignKey(BookingPage, on_delete=models.CASCADE)
    # Visitor info
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    service_needed = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    # Slot
    date = models.DateField()
    time = models.TimeField()
    # Status & tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    confirmation_code = models.CharField(max_length=12, unique=True, blank=True)
    # Link to CRM
    contact = models.ForeignKey('Contact', on_delete=models.SET_NULL, null=True, blank=True)
    appointment = models.ForeignKey('Appointment', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.confirmation_code:
            self.confirmation_code = uuid.uuid4().hex[:8].upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} — {self.date} {self.time}'


# ── Review Campaigns ────────────────────────────────────────────────

class ReviewCampaign(models.Model):
    """Automated campaign to request Google reviews from happy customers."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
    ]
    CHANNEL_CHOICES = [
        ('sms', 'SMS Only'),
        ('email', 'Email Only'),
        ('both', 'SMS + Email'),
    ]

    business = models.ForeignKey('BusinessProfile', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    # Review destination
    google_review_url = models.URLField(max_length=500, blank=True,
                                        help_text='Direct Google review link for the business')
    yelp_review_url = models.URLField(max_length=500, blank=True)
    # Message templates
    sms_template = models.TextField(
        default='Hi {name}! Thanks for choosing {business}. We\'d love your feedback — would you mind leaving us a quick Google review? {link}',
        help_text='Variables: {name}, {business}, {link}')
    email_subject = models.CharField(max_length=200, default='How did we do?')
    email_template = models.TextField(
        default='Hi {name},\n\nThank you for choosing {business}! We hope you were satisfied with our service.\n\nIf you have a moment, we\'d really appreciate a quick review:\n{link}\n\nThank you!\n{business}',
        help_text='Variables: {name}, {business}, {link}')
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default='sms')
    # Targeting
    auto_send_on_won = models.BooleanField(default=False,
                                            help_text='Automatically send when a contact stage becomes "won"')
    delay_hours = models.IntegerField(default=24,
                                      help_text='Hours to wait after trigger before sending')
    # Metrics
    total_sent = models.IntegerField(default=0)
    total_clicked = models.IntegerField(default=0)
    total_reviews = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} — {self.status}'

    @property
    def click_rate(self):
        return round(self.total_clicked / self.total_sent * 100, 1) if self.total_sent else 0


class ReviewRequest(models.Model):
    """Individual review request sent to a contact."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('clicked', 'Clicked'),
        ('reviewed', 'Reviewed'),
        ('failed', 'Failed'),
        ('opted_out', 'Opted Out'),
    ]

    campaign = models.ForeignKey(ReviewCampaign, on_delete=models.CASCADE)
    contact = models.ForeignKey('Contact', on_delete=models.CASCADE)
    # Delivery
    sent_via = models.CharField(max_length=10, blank=True)  # sms, email
    sent_at = models.DateTimeField(null=True, blank=True)
    sms_sid = models.CharField(max_length=100, blank=True)
    # Tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    clicked_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['campaign', 'contact']

    def __str__(self):
        return f'Review req for {self.contact.name} — {self.status}'
