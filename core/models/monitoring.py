from django.db import models
from django.utils import timezone
from .business import BusinessProfile


class MonitorRun(models.Model):
    """Logs each monitor execution for health tracking and debugging."""
    STATUS_CHOICES = [
        ('running', 'Running'),
        ('success', 'Success'),
        ('partial', 'Partial Success'),
        ('failed', 'Failed'),
    ]

    monitor_name = models.CharField(max_length=100, db_index=True,
        help_text='e.g. craigslist, reddit, facebook, patch')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running')
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    items_scraped = models.IntegerField(default=0)
    leads_created = models.IntegerField(default=0)
    duplicates = models.IntegerField(default=0)
    errors = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True,
        help_text='Extra stats, region counts, etc.')

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.monitor_name} @ {self.started_at:%Y-%m-%d %H:%M} [{self.status}]"

    def finish(self, status='success', error_message=''):
        self.status = status
        self.finished_at = timezone.now()
        self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        if error_message:
            self.error_message = error_message[:2000]
        self.save()


class EmailSendLog(models.Model):
    """Tracks domain warming pace and bounce/complaint rates."""
    date = models.DateField(db_index=True)
    emails_sent = models.IntegerField(default=0)
    emails_delivered = models.IntegerField(default=0)
    emails_bounced = models.IntegerField(default=0)
    emails_complained = models.IntegerField(default=0)
    warming_limit = models.IntegerField(default=5,
        help_text='Max emails allowed for this date per warming schedule')

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.date} — sent:{self.emails_sent} bounced:{self.emails_bounced}"

    @property
    def bounce_rate(self):
        return round(self.emails_bounced / self.emails_sent * 100, 1) if self.emails_sent else 0

    @property
    def remaining(self):
        return max(0, self.warming_limit - self.emails_sent)


class Unsubscribe(models.Model):
    """CAN-SPAM opt-out list. Checked before every outreach send."""
    email = models.EmailField(unique=True, db_index=True)
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.email


class MonitoredLocalSite(models.Model):
    SCRAPE_PATTERN_CHOICES = [
        ('wordpress_comments', 'WordPress Comments'),
        ('discourse', 'Discourse Forum'),
        ('custom_html', 'Custom HTML'),
    ]

    name = models.CharField(max_length=200)
    base_url = models.URLField()
    community_section_url = models.URLField(blank=True)
    scrape_pattern = models.CharField(max_length=50, choices=SCRAPE_PATTERN_CHOICES, default='custom_html')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per site. Keys: '
        'article_list, article_link, article_title, article_date, '
        'article_author, article_body, comment_list, comment_body'
    ))
    is_active = models.BooleanField(default=True)
    last_scraped = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PermitSource(models.Model):
    """
    Configurable data source for county building permit portals.
    Each county has a different website structure — this model stores
    the URL and CSS selectors needed to scrape each one.
    Adding a new county is a database entry, not a code change.
    """
    SCRAPE_METHOD_CHOICES = [
        ('html_table', 'HTML Table'),
        ('pdf_report', 'PDF Report'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ]

    name = models.CharField(max_length=200, help_text='e.g. Los Angeles County Building Permits')
    county = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    source_url = models.URLField(help_text='URL of the permit search/listing page')
    scrape_method = models.CharField(max_length=50, choices=SCRAPE_METHOD_CHOICES, default='html_table')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per source. Keys: '
        'table_selector, row_selector, permit_type, address, filing_date, '
        'estimated_value, owner_name, contractor_name, status, next_page'
    ))
    api_config = models.JSONField(default=dict, blank=True, help_text=(
        'API-specific config: endpoint, params, headers, pagination key, result key'
    ))
    schedule = models.CharField(max_length=20, default='weekly')
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['state', 'county']
        unique_together = ['county', 'state']

    def __str__(self):
        return f'{self.county}, {self.state} — {self.name}'


class PropertyTransferSource(models.Model):
    """
    Configurable data source for county property transfer/recorder portals.
    Each county publishes property sales/transfers differently.
    Adding a new county is a database entry, not a code change.
    """
    SCRAPE_METHOD_CHOICES = [
        ('html_table', 'HTML Table'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
        ('apify_zillow', 'Apify Zillow Scraper'),
    ]

    name = models.CharField(max_length=200, help_text='e.g. Los Angeles County Recorder')
    county = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    source_url = models.URLField(help_text='URL of the property transfer search/listing page')
    scrape_method = models.CharField(max_length=50, choices=SCRAPE_METHOD_CHOICES, default='html_table')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per source. Keys: '
        'table_selector, row_selector, address, sale_date, sale_price, '
        'buyer_name, property_type, square_footage, next_page'
    ))
    api_config = models.JSONField(default=dict, blank=True, help_text=(
        'API/Apify-specific config: endpoint, params, search_area, property_type_filter'
    ))
    schedule = models.CharField(max_length=20, default='weekly')
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['state', 'county']
        unique_together = ['county', 'state']

    def __str__(self):
        return f'{self.county}, {self.state} — {self.name}'


class StateBusinessFilingSource(models.Model):
    """
    Configurable data source for state corporation/business filing databases.
    Each state has a different portal — the model stores the URL and scrape
    configuration per state. Adding a new state is a database entry, not code.
    """
    SCRAPE_METHOD_CHOICES = [
        ('html_table', 'HTML Table'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ]

    state = models.CharField(max_length=2, unique=True, db_index=True)
    state_name = models.CharField(max_length=50)
    source_url = models.URLField(help_text='URL of the state corporation search portal')
    scrape_method = models.CharField(max_length=50, choices=SCRAPE_METHOD_CHOICES, default='html_table')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per source. Keys: '
        'table_selector, row_selector, business_name, filing_date, '
        'entity_type, registered_agent, address, status, next_page'
    ))
    api_config = models.JSONField(default=dict, blank=True, help_text=(
        'API-specific config: endpoint, params, headers, result_key'
    ))
    search_params = models.JSONField(default=dict, blank=True, help_text=(
        'Default search parameters: date_range_days, entity_types, etc.'
    ))
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['state']
        verbose_name = 'State Business Filing Source'
        verbose_name_plural = 'State Business Filing Sources'

    def __str__(self):
        return f'{self.state_name} ({self.state})'


class CodeViolationSource(models.Model):
    """
    Configurable data source for municipal code enforcement databases.
    Each municipality has a different portal — this model stores the URL
    and CSS selectors needed to scrape each one.
    Adding a new municipality is a database entry, not a code change.
    """
    SCRAPE_METHOD_CHOICES = [
        ('html_table', 'HTML Table'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ]

    name = models.CharField(max_length=200, help_text='e.g. City of Houston Code Enforcement')
    municipality = models.CharField(max_length=200, db_index=True,
        help_text='City or township name')
    county = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, db_index=True)
    source_url = models.URLField(help_text='URL of the code violation search/listing page')
    scrape_method = models.CharField(max_length=50, choices=SCRAPE_METHOD_CHOICES, default='html_table')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per source. Keys: '
        'table_selector, row_selector, address, violation_type, violation_date, '
        'compliance_deadline, owner_name, status, next_page'
    ))
    api_config = models.JSONField(default=dict, blank=True, help_text=(
        'API-specific config: endpoint, params, headers, result_key'
    ))
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['state', 'municipality']
        unique_together = ['municipality', 'state']
        verbose_name = 'Code Violation Source'
        verbose_name_plural = 'Code Violation Sources'

    def __str__(self):
        return f'{self.municipality}, {self.state} — {self.name}'


class HealthInspectionSource(models.Model):
    """
    Configurable data source for county/city health department inspection databases.
    Each jurisdiction publishes inspection results differently.
    Adding a new jurisdiction is a database entry, not a code change.
    """
    SCRAPE_METHOD_CHOICES = [
        ('html_table', 'HTML Table'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ]

    name = models.CharField(max_length=200, help_text='e.g. Los Angeles County Health Inspections')
    jurisdiction = models.CharField(max_length=200, db_index=True,
        help_text='County or city name')
    state = models.CharField(max_length=2, db_index=True)
    source_url = models.URLField(help_text='URL of the inspection results page')
    scrape_method = models.CharField(max_length=50, choices=SCRAPE_METHOD_CHOICES, default='html_table')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per source. Keys: '
        'table_selector, row_selector, restaurant_name, address, '
        'inspection_date, score, grade, violations, next_page'
    ))
    api_config = models.JSONField(default=dict, blank=True, help_text=(
        'API-specific config: endpoint, params, headers, result_key'
    ))
    failing_threshold = models.IntegerField(default=70,
        help_text='Score below this is considered a failure (default: 70)')
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['state', 'jurisdiction']
        unique_together = ['jurisdiction', 'state']
        verbose_name = 'Health Inspection Source'
        verbose_name_plural = 'Health Inspection Sources'

    def __str__(self):
        return f'{self.jurisdiction}, {self.state} — {self.name}'


class LicensingBoardSource(models.Model):
    """
    Configurable data source for state contractor licensing board databases.
    Each state publishes license data differently.
    Adding a new state/license type is a database entry, not a code change.
    """
    SCRAPE_METHOD_CHOICES = [
        ('html_table', 'HTML Table'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ]

    name = models.CharField(max_length=200, help_text='e.g. California CSLB Contractor Licenses')
    state = models.CharField(max_length=2, db_index=True)
    license_type = models.CharField(max_length=100, db_index=True,
        help_text='e.g. plumbing, electrical, general contractor, HVAC')
    source_url = models.URLField(help_text='URL of the license search/lookup page')
    scrape_method = models.CharField(max_length=50, choices=SCRAPE_METHOD_CHOICES, default='html_table')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per source. Keys: '
        'table_selector, row_selector, contractor_name, license_number, '
        'license_type, expiration_date, status, business_address, next_page'
    ))
    api_config = models.JSONField(default=dict, blank=True, help_text=(
        'API-specific config: endpoint, params, headers, result_key'
    ))
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['state', 'license_type']
        unique_together = ['state', 'license_type']
        verbose_name = 'Licensing Board Source'
        verbose_name_plural = 'Licensing Board Sources'

    def __str__(self):
        return f'{self.state} — {self.license_type} — {self.name}'


class CourtRecordSource(models.Model):
    """
    Configurable data source for county court record portals.
    Used for monitoring commercial eviction filings.
    Each county has a different portal — adding a new county is a database entry.
    IMPORTANT: Only commercial evictions — never residential (ethical concerns).
    """
    SCRAPE_METHOD_CHOICES = [
        ('html_table', 'HTML Table'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ]

    name = models.CharField(max_length=200, help_text='e.g. Harris County Court Records')
    county = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    source_url = models.URLField(help_text='URL of the court record search portal')
    scrape_method = models.CharField(max_length=50, choices=SCRAPE_METHOD_CHOICES, default='html_table')
    css_selectors = models.JSONField(default=dict, blank=True, help_text=(
        'Custom CSS selectors per source. Keys: '
        'table_selector, row_selector, address, filing_date, case_number, '
        'plaintiff, property_type, status, next_page'
    ))
    api_config = models.JSONField(default=dict, blank=True, help_text=(
        'API-specific config: endpoint, params, headers, result_key'
    ))
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['state', 'county']
        unique_together = ['county', 'state']
        verbose_name = 'Court Record Source'
        verbose_name_plural = 'Court Record Sources'

    def __str__(self):
        return f'{self.county}, {self.state} — {self.name}'


class MonitoredFacebookGroup(models.Model):
    business = models.ForeignKey(
        BusinessProfile, on_delete=models.CASCADE,
        related_name='facebook_groups', null=True, blank=True,
        help_text='Owner business; leave blank for shared/global groups',
    )
    name = models.CharField(max_length=300)
    group_id = models.CharField(max_length=100, unique=True, help_text='Numeric Facebook group ID')
    url = models.URLField(help_text='Full group URL, e.g. https://www.facebook.com/groups/123456')
    keywords = models.JSONField(
        default=list, blank=True,
        help_text='Service keywords to match inside this group',
    )
    is_active = models.BooleanField(default=True)
    last_scraped = models.DateTimeField(null=True, blank=True)
    posts_scraped = models.IntegerField(default=0)
    leads_created = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class TrackedGoogleBusiness(models.Model):
    """
    Tracks businesses discovered via the Google Places API.
    Used to detect new businesses (not seen before) and track
    status changes (open -> closed) over time.
    """
    BUSINESS_STATUS_CHOICES = [
        ('OPERATIONAL', 'Operational'),
        ('CLOSED_TEMPORARILY', 'Closed Temporarily'),
        ('CLOSED_PERMANENTLY', 'Closed Permanently'),
    ]

    place_id = models.CharField(max_length=300, unique=True, db_index=True)
    name = models.CharField(max_length=300)
    address = models.CharField(max_length=500, blank=True)
    category = models.CharField(max_length=100, blank=True,
        help_text='Google Places primary type or search category')
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    business_status = models.CharField(max_length=30,
        choices=BUSINESS_STATUS_CHOICES, default='OPERATIONAL')
    avg_rating = models.FloatField(null=True, blank=True)
    total_reviews = models.IntegerField(default=0)
    google_maps_url = models.URLField(max_length=500, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_checked = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-first_seen']
        verbose_name = 'Tracked Google Business'
        verbose_name_plural = 'Tracked Google Businesses'

    def __str__(self):
        return f'{self.name} ({self.place_id[:20]}...)'
