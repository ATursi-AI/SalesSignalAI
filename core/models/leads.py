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
        ('google_maps', 'Google Maps'),
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
        ('public_records', 'Public Records'),
        ('manual', 'Manual Entry'),
    ]

    URGENCY_CHOICES = [
        ('hot', 'HOT'),
        ('warm', 'WARM'),
        ('new', 'NEW'),
        ('stale', 'Stale'),
    ]

    REVIEW_STATUS_CHOICES = [
        ('unreviewed', 'Unreviewed'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('assigned', 'Assigned'),
    ]

    CONFIDENCE_CHOICES = [
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]

    SOURCE_GROUP_CHOICES = [
        ('public_records', 'Public Records'),
        ('social_media', 'Social Media'),
        ('reviews', 'Review Sites'),
        ('weather', 'Weather/Events'),
    ]

    SOURCE_TYPE_CHOICES = [
        # Public Records — Violations
        ('violations', 'DOB Violations'),
        ('building_violations', 'Building Violations'),
        ('hpd_violations', 'HPD Violations'),
        ('ordinance_violations', 'Ordinance Violations'),
        ('ecb_summonses', 'ECB Summonses'),
        ('code_enforcement', 'Code Enforcement'),
        ('code_complaints', 'Code Complaints'),
        ('fire_violations', 'Fire Violations'),
        ('housing_violations', 'Housing Violations'),
        ('environmental_violations', 'Environmental Violations'),
        ('environmental_remediation', 'Environmental Remediation'),
        ('alcohol_violations', 'Alcohol Violations'),
        ('repeat_offender_violations', 'Repeat Offenders'),
        # Public Records — Permits
        ('permits', 'DOB Permits'),
        ('permits_now', 'DOB Permits (NOW)'),
        ('building_permits', 'Building Permits'),
        ('construction_permits', 'Construction Permits'),
        ('electrical_permits', 'Electrical Permits'),
        ('trade_permits', 'Trade Permits'),
        ('boiler_permits', 'Boiler Permits'),
        ('mc_permits', 'MC Permits'),
        ('certificate_of_occupancy', 'Certificate of Occupancy'),
        ('permit_contacts', 'Permit Contacts'),
        # Public Records — Sales & Filings
        ('property_sales', 'Property Sales'),
        ('business_filings', 'Business Filings'),
        ('license_expirations', 'License Expirations'),
        ('liquor_licenses', 'Liquor Licenses'),
        ('liquor_suspensions', 'Liquor Suspensions'),
        ('storage_tanks', 'Storage Tanks'),
        # Public Records — Health
        ('health_inspections', 'Health Inspections'),
        ('health_closures', 'Restaurant Closures'),
        ('food_inspections', 'Food Inspections'),
        ('pool_inspections', 'Pool Inspections'),
        # Google / Reviews
        ('google_reviews', 'Google Reviews'),
        ('no_website', 'No Website Detected'),
        ('closed_business', 'Closed Business'),
        ('new_business', 'New Business'),
        ('google_qa', 'Google Q&A'),
        # Social Media
        ('reddit', 'Reddit'),
        ('nextdoor', 'Nextdoor'),
        ('facebook', 'Facebook Groups'),
        # Weather
        ('noaa', 'NOAA Weather'),
        # Prospect Video
        ('prospect_video', 'Prospect Video Intake'),
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

    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES, default='low')
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS_CHOICES, default='unreviewed')

    discovered_at = models.DateTimeField(auto_now_add=True)
    event_date = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text='Actual date of the event (violation issued, permit filed, etc.)',
    )
    raw_data = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64, unique=True)

    # State/region for multi-state support
    state = models.CharField(max_length=2, blank=True, default='NY',
                             help_text='Two-letter state code', db_index=True)
    region = models.CharField(max_length=100, blank=True,
                              help_text='Sub-region: borough, county, city')

    # Source classification
    source_group = models.CharField(max_length=50, choices=SOURCE_GROUP_CHOICES,
                                    default='public_records', db_index=True)
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPE_CHOICES,
                                   blank=True, db_index=True)

    # Standardized contact info
    contact_name = models.CharField(max_length=200, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_business = models.CharField(max_length=200, blank=True)
    contact_address = models.TextField(blank=True)

    # Enrichment tracking
    ENRICHMENT_STATUS_CHOICES = [
        ('not_enriched', 'Not Enriched'),
        ('enriched', 'Enriched'),
        ('enrichment_failed', 'Enrichment Failed'),
        ('manually_enriched', 'Manually Enriched'),
    ]
    enrichment_status = models.CharField(
        max_length=20, choices=ENRICHMENT_STATUS_CHOICES,
        default='not_enriched', db_index=True,
    )
    enrichment_date = models.DateTimeField(null=True, blank=True)

    # ── Intent classification (AI + manual override) ──
    INTENT_CHOICES = [
        ('not_classified', 'Not Classified'),
        ('real_lead', 'Real Lead'),
        ('mention_only', 'Mention Only'),
        ('false_positive', 'False Positive'),
        ('job_posting', 'Job Posting'),
        ('advice_giving', 'Advice/Discussion'),
    ]
    intent_classification = models.CharField(
        max_length=20, choices=INTENT_CHOICES,
        default='not_classified', db_index=True,
    )
    intent_confidence = models.FloatField(
        default=0.0,
        help_text='AI confidence in intent classification (0.0-1.0)',
    )
    intent_service_detected = models.CharField(
        max_length=100, blank=True,
        help_text='Service type detected by AI classifier',
    )
    intent_classified_at = models.DateTimeField(null=True, blank=True)
    intent_classified_by = models.CharField(
        max_length=20, blank=True, default='',
        help_text='ai or staff username who classified',
    )

    # ── Curation & REACH scoring ──
    is_curated = models.BooleanField(
        default=False,
        help_text='Manually placed into a customer feed by staff',
    )
    curated_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='curated_leads',
    )
    curated_at = models.DateTimeField(null=True, blank=True)
    reach_score = models.IntegerField(
        default=0, db_index=True,
        help_text='REACH priority score (0-100)',
    )
    reach_scored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-event_date', '-discovered_at']

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
        ('dismissed', 'Dismissed'),
    ]

    ASSIGNMENT_TYPE_CHOICES = [
        ('auto', 'Auto-Assigned'),
        ('curated', 'Staff Curated'),
    ]
    SERVICE_TIER_CHOICES = [
        ('self_service', 'Self-Service (Customer contacts lead)'),
        ('managed', 'Managed (SalesSignalAI contacts lead)'),
        ('unset', 'Not Selected'),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='assignments')
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='lead_assignments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    assignment_type = models.CharField(
        max_length=10, choices=ASSIGNMENT_TYPE_CHOICES, default='auto',
    )
    service_tier = models.CharField(
        max_length=15, choices=SERVICE_TIER_CHOICES, default='unset',
        help_text='How this lead will be worked: customer contacts or we contact for them',
    )
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


class AgentMission(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('complete', 'Complete'),
        ('error', 'Error'),
    ]
    agent_name = models.CharField(max_length=50)
    goal = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    result = models.TextField(blank=True)
    steps_taken = models.IntegerField(default=0)
    leads_found = models.IntegerField(default=0)
    mission_log = models.JSONField(default=list)
    triggered_by = models.CharField(max_length=100, blank=True, help_text='sms, web, cron, or username')
    triggered_from = models.CharField(max_length=50, blank=True, help_text='Phone number or IP')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.agent_name}: {self.goal[:50]} ({self.status})"
