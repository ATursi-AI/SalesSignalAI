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
