from django.db import models
from django.contrib.auth.models import User


class DatasetRegistry(models.Model):
    """Approved data sources with field mappings."""
    STATE_CHOICES = [(s, s) for s in ['NY', 'CA', 'TX', 'FL', 'IL', 'PA', 'OH', 'GA', 'NC', 'MI', 'WA', 'MD', 'CT']]
    DATA_TYPE_CHOICES = [
        ('violations', 'Violations'),
        ('permits', 'Permits'),
        ('health_inspections', 'Health Inspections'),
        ('business_filings', 'Business Filings'),
        ('property_sales', 'Property Sales'),
        ('liquor_licenses', 'Liquor Licenses'),
        ('contractor_licenses', 'Contractor Licenses'),
        ('code_enforcement', 'Code Enforcement'),
        ('other', 'Other'),
    ]

    name = models.CharField(max_length=200)
    state = models.CharField(max_length=2, choices=STATE_CHOICES)
    city = models.CharField(max_length=100, blank=True)
    data_type = models.CharField(max_length=30, choices=DATA_TYPE_CHOICES)
    portal_domain = models.CharField(max_length=200, help_text='e.g. data.cityofnewyork.us')
    dataset_id = models.CharField(max_length=50, help_text='Socrata dataset ID e.g. 6bgk-3dad')
    api_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    update_frequency = models.CharField(max_length=50, blank=True, help_text='e.g. daily, weekly, monthly')
    total_records = models.IntegerField(null=True, blank=True)
    last_checked = models.DateTimeField(null=True, blank=True)

    # Field mappings
    address_field = models.CharField(max_length=100, blank=True)
    name_field = models.CharField(max_length=100, blank=True, help_text='respondent_name, owner_name, dba, etc.')
    phone_field = models.CharField(max_length=100, blank=True)
    email_field = models.CharField(max_length=100, blank=True)
    date_field = models.CharField(max_length=100, blank=True, help_text='Field to filter by date')
    status_field = models.CharField(max_length=100, blank=True, help_text='Field to filter active/open records')

    contact_fields = models.JSONField(default=list, blank=True, help_text='List of all contact-related field names')
    all_fields = models.JSONField(default=list, blank=True, help_text='All field names in the dataset')
    sample_data = models.JSONField(default=list, blank=True, help_text='3 sample records')
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ['portal_domain', 'dataset_id']
        ordering = ['state', 'name']
        verbose_name_plural = 'Dataset Registry'

    def __str__(self):
        return f"{self.state} - {self.name} ({self.dataset_id})"


class ScrapeRun(models.Model):
    """Audit log of every scrape."""
    STATUS_CHOICES = [
        ('running', 'Running'),
        ('success', 'Success'),
        ('partial', 'Partial'),
        ('failed', 'Failed'),
    ]

    dataset = models.ForeignKey(DatasetRegistry, on_delete=models.CASCADE, related_name='scrape_runs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    records_fetched = models.IntegerField(default=0)
    leads_created = models.IntegerField(default=0)
    duplicates = models.IntegerField(default=0)
    errors = models.IntegerField(default=0)
    pct_with_phone = models.FloatField(null=True, blank=True)
    pct_with_name = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.dataset.name} - {self.started_at.strftime('%Y-%m-%d %H:%M')}"

    def finish(self, status='success', error_message=''):
        from django.utils import timezone
        self.status = status
        self.finished_at = timezone.now()
        self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        if error_message:
            self.error_message = error_message[:2000]
        self.save()


class DatasetCandidate(models.Model):
    """Discovered but not yet approved datasets."""
    STATUS_CHOICES = [
        ('new', 'New'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    name = models.CharField(max_length=200)
    portal_domain = models.CharField(max_length=200)
    dataset_id = models.CharField(max_length=50)
    api_url = models.URLField(max_length=500, blank=True)
    state = models.CharField(max_length=2, blank=True)
    city = models.CharField(max_length=100, blank=True)
    data_type = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    total_records = models.IntegerField(null=True, blank=True)
    has_phone_field = models.BooleanField(default=False)
    has_email_field = models.BooleanField(default=False)
    has_name_field = models.BooleanField(default=False)
    contact_fields_found = models.JSONField(default=list, blank=True)
    all_fields = models.JSONField(default=list, blank=True)
    sample_data = models.JSONField(default=list, blank=True)
    relevance = models.CharField(max_length=10, blank=True, help_text='HIGH, MEDIUM, LOW')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    discovered_by = models.CharField(max_length=100, blank=True, help_text='agent, manual, audit script')
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['portal_domain', 'dataset_id']
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.status.upper()}] {self.name} ({self.portal_domain})"
