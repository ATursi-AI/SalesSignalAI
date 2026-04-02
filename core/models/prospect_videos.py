from django.db import models


class ProspectVideo(models.Model):
    # Who this prospect page is for
    slug = models.SlugField(max_length=200, unique=True, help_text="URL slug: /demo/joes-plumbing/")

    # Prospect info
    prospect_business_name = models.CharField(max_length=200)
    prospect_owner_name = models.CharField(max_length=200, blank=True)
    prospect_phone = models.CharField(max_length=20, blank=True)
    prospect_email = models.EmailField(blank=True)
    prospect_trade = models.CharField(max_length=100, help_text="Plumbing, Cleaning, Electrical, etc.")
    prospect_city = models.CharField(max_length=100)
    prospect_state = models.CharField(max_length=2, default='NY')

    # Video
    video_url = models.URLField(help_text="YouTube unlisted URL or HeyGen direct link")
    video_thumbnail_url = models.URLField(blank=True, help_text="Optional thumbnail image URL")

    # Custom messaging on the page
    headline = models.CharField(max_length=300, blank=True, help_text="Custom headline. Default: '{business_name} — See What We Found'")
    custom_message = models.TextField(blank=True, help_text="Message shown below the video. E.g. 'We found 47 people in your area looking for a plumber this week.'")
    cta_text = models.CharField(max_length=100, default="Book a Call", help_text="Button text")
    cta_url = models.URLField(blank=True, help_text="Where the CTA button links. Leave blank for default contact form.")

    # If this is on behalf of a paying customer
    customer = models.ForeignKey('BusinessProfile', null=True, blank=True, on_delete=models.SET_NULL, help_text="If created on behalf of a paying customer, select them here")
    customer_business_name = models.CharField(max_length=200, blank=True, help_text="The paying customer's business name to feature in the page")
    customer_phone = models.CharField(max_length=20, blank=True, help_text="Customer's phone for the prospect to call")
    customer_website = models.URLField(blank=True)

    # Lead trigger (why we're reaching out)
    trigger_type = models.CharField(max_length=50, blank=True, choices=[
        ('health_violation', 'Health Inspection Violation'),
        ('building_violation', 'Building Violation'),
        ('new_business', 'New Business Filing'),
        ('property_sale', 'Property Sale'),
        ('permit_filed', 'Permit Filed'),
        ('social_request', 'Social Media Request'),
        ('no_website', 'No Website Detected'),
        ('bad_reviews', 'Low Google Reviews'),
        ('competitor_issue', 'Competitor Issue'),
        ('custom', 'Custom Outreach'),
    ], default='custom')
    trigger_detail = models.TextField(blank=True, help_text="Specific trigger info: 'Health inspection score 42, critical violation for pest issues'")

    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    sms_sent = models.BooleanField(default=False)
    sms_sent_at = models.DateTimeField(null=True, blank=True)
    email_sent = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    page_views = models.IntegerField(default=0)
    video_plays = models.IntegerField(default=0)
    cta_clicks = models.IntegerField(default=0)
    prospect_responded = models.BooleanField(default=False)
    response_date = models.DateTimeField(null=True, blank=True)
    response_notes = models.TextField(blank=True)

    # Status
    status = models.CharField(max_length=20, choices=[
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('responded', 'Responded'),
        ('converted', 'Converted'),
        ('closed', 'Closed'),
    ], default='draft')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.prospect_business_name} ({self.slug})"

    def get_display_headline(self):
        return self.headline or f"{self.prospect_business_name}"

    def is_white_label(self):
        return self.customer is not None or bool(self.customer_business_name)
