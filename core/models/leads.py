from django.db import models
from .business import ServiceCategory, BusinessProfile


class Lead(models.Model):
    PLATFORM_CHOICES = [
        ('craigslist', 'Craigslist'),
        ('reddit', 'Reddit'),
        ('patch', 'Patch.com'),
        ('houzz', 'Houzz'),
        ('alignable', 'Alignable'),
        ('google_qna', 'Google Q&A'),
        ('google_reviews', 'Google Reviews'),
        ('yelp_review', 'Yelp Review'),
        ('angi_review', 'Angi Review'),
        ('thumbtack', 'Thumbtack'),
        ('porch', 'Porch'),
        ('citydata', 'City-Data Forum'),
        ('biggerpockets', 'BiggerPockets'),
        ('local_news', 'Local News/Blog'),
        ('parent_community', 'Parent Community'),
        ('trade_forum', 'Trade Forum'),
        ('facebook', 'Facebook'),
        ('nextdoor', 'Nextdoor'),
        ('twitter', 'Twitter/X'),
        ('tiktok', 'TikTok'),
        ('quora', 'Quora'),
        ('threads', 'Threads'),
        ('fb_marketplace', 'FB Marketplace'),
        ('trustpilot', 'Trustpilot'),
        ('instagram', 'Instagram'),
        ('bbb', 'BBB'),
        ('permit', 'Building Permit'),
        ('property_sale', 'Property Sale'),
        ('business_filing', 'Business Filing'),
        ('weather_alert', 'Weather Alert'),
        ('code_violation', 'Code Violation'),
        ('eviction_filing', 'Eviction Filing'),
        ('health_inspection', 'Health Inspection'),
        ('license_expiry', 'License Expiry'),
        ('manual', 'Manual Entry'),
    ]

    URGENCY_CHOICES = [
        ('hot', 'HOT'),
        ('warm', 'WARM'),
        ('new', 'NEW'),
        ('stale', 'Stale'),
    ]

    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    source_url = models.URLField(max_length=500)
    source_content = models.TextField()
    source_author = models.CharField(max_length=200, blank=True)
    source_posted_at = models.DateTimeField(null=True, blank=True)

    detected_location = models.CharField(max_length=200, blank=True)
    detected_zip = models.CharField(max_length=10, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    detected_service_type = models.ForeignKey(ServiceCategory, null=True, blank=True, on_delete=models.SET_NULL)
    matched_keywords = models.JSONField(default=list)

    urgency_score = models.IntegerField(default=50)
    urgency_level = models.CharField(max_length=10, choices=URGENCY_CHOICES, default='new')

    ai_summary = models.TextField(blank=True)
    ai_suggested_response = models.TextField(blank=True)

    discovered_at = models.DateTimeField(auto_now_add=True)
    raw_data = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ['-discovered_at']

    def __str__(self):
        return f"[{self.urgency_level.upper()}] {self.platform} - {self.source_content[:60]}"


class LeadAssignment(models.Model):
    STATUS_CHOICES = [
        ('new', 'New'),
        ('alerted', 'Alert Sent'),
        ('viewed', 'Viewed'),
        ('contacted', 'Contacted'),
        ('quoted', 'Quote Sent'),
        ('won', 'Won'),
        ('lost', 'Lost'),
        ('expired', 'Expired'),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='assignments')
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='lead_assignments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    alert_sent_at = models.DateTimeField(null=True, blank=True)
    alert_method = models.CharField(max_length=10, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    revenue = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['lead', 'business']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.business} - {self.lead} ({self.status})"
