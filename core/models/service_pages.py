from django.db import models
from django.utils.text import slugify

from .business import BusinessProfile


class TradeCategory(models.Model):
    CATEGORY_TYPES = [
        ('home_service', 'Home Service'),
        ('commercial_service', 'Commercial Service'),
        ('professional', 'Professional Service'),
        ('emergency', 'Emergency Service'),
    ]

    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    emergency_keywords = models.TextField(
        blank=True,
        help_text="Comma-separated: emergency plumber, 24 hour plumber, plumber near me",
    )
    service_keywords = models.TextField(
        blank=True,
        help_text="Comma-separated: drain cleaning, water heater repair, toilet repair",
    )
    pain_points = models.TextField(
        blank=True,
        help_text="Common customer problems: burst pipe flooding, no hot water",
    )
    category_type = models.CharField(max_length=20, choices=CATEGORY_TYPES, default='home_service')
    icon = models.CharField(max_length=50, blank=True, help_text="Bootstrap icon class")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Trade Categories"
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class ServiceArea(models.Model):
    AREA_TYPES = [
        ('borough', 'Borough'),
        ('city', 'City'),
        ('county', 'County'),
        ('town', 'Town'),
        ('village', 'Village'),
        ('neighborhood', 'Neighborhood'),
        ('zip', 'ZIP Code'),
    ]

    name = models.CharField(max_length=100)
    slug = models.SlugField()
    area_type = models.CharField(max_length=20, choices=AREA_TYPES, default='city')
    parent_area = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='children',
        help_text="Parent area (e.g. Queens -> New York City)",
    )
    state = models.CharField(max_length=2, default='NY')
    state_full = models.CharField(max_length=50, default='New York')
    county = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    population = models.IntegerField(null=True, blank=True)
    neighboring_areas = models.ManyToManyField('self', blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ['slug', 'state']
        ordering = ['state', 'name']

    def __str__(self):
        return f"{self.name}, {self.state}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.name}-{self.state}")
        super().save(*args, **kwargs)


class ServiceLandingPage(models.Model):
    PAGE_TYPES = [
        ('salessignal', 'SalesSignal Owned'),
        ('customer', 'Customer Branded'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
    ]

    # Core
    trade = models.ForeignKey(TradeCategory, on_delete=models.CASCADE, related_name='landing_pages')
    area = models.ForeignKey(ServiceArea, on_delete=models.CASCADE, related_name='landing_pages')
    page_type = models.CharField(max_length=20, choices=PAGE_TYPES, default='salessignal')
    slug = models.SlugField(max_length=200, unique=True)
    custom_domain = models.CharField(max_length=200, blank=True)

    # Customer branding
    customer = models.ForeignKey(
        BusinessProfile, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='service_landing_pages',
    )
    branded_business_name = models.CharField(max_length=200, blank=True)
    branded_phone = models.CharField(max_length=20, blank=True)
    branded_email = models.EmailField(blank=True)
    branded_website = models.URLField(blank=True)
    branded_logo_url = models.URLField(blank=True)
    branded_tagline = models.CharField(max_length=200, blank=True)
    branded_years_in_business = models.IntegerField(null=True, blank=True)
    branded_license_number = models.CharField(max_length=100, blank=True)
    branded_google_reviews_url = models.URLField(blank=True)
    branded_star_rating = models.FloatField(null=True, blank=True)
    branded_review_count = models.IntegerField(null=True, blank=True)

    # Phone routing (SalesSignal-owned)
    signalwire_phone = models.CharField(max_length=20, blank=True)
    forward_to_phone = models.CharField(max_length=20, blank=True)

    # SEO Content
    page_title = models.CharField(max_length=200, blank=True)
    meta_description = models.CharField(max_length=300, blank=True)
    h1_headline = models.CharField(max_length=200, blank=True)
    hero_subheadline = models.CharField(max_length=300, blank=True)

    # Dynamic content
    show_live_stats = models.BooleanField(default=True)
    services_offered = models.TextField(blank=True, help_text="One per line")
    about_section = models.TextField(blank=True)
    faq_section = models.JSONField(default=list, blank=True)

    # Stats
    form_submissions = models.IntegerField(default=0)
    phone_calls = models.IntegerField(default=0)
    page_views = models.IntegerField(default=0)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['trade', 'area', 'page_type', 'customer']
        ordering = ['trade__name', 'area__name']

    def __str__(self):
        if self.page_type == 'customer' and self.branded_business_name:
            return f"{self.branded_business_name} - {self.trade.name} in {self.area.name}"
        return f"{self.trade.name} in {self.area.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.trade.name}-{self.area.name}-{self.area.state}")
        self._auto_generate_content()
        super().save(*args, **kwargs)

    def _auto_generate_content(self):
        """Auto-generate SEO content if fields are blank."""
        trade = self.trade.name
        area = self.area.name
        state = self.area.state_full or self.area.state

        if not self.page_title:
            if self.page_type == 'customer' and self.branded_business_name:
                self.page_title = f"{self.branded_business_name} — {trade} in {area}, {self.area.state} | Call Now"
            else:
                self.page_title = f"{trade} in {area}, {self.area.state} — 24/7 Service | Call Now"

        if not self.meta_description:
            if self.page_type == 'customer' and self.branded_business_name:
                years = f"{self.branded_years_in_business} years experience. " if self.branded_years_in_business else ""
                self.meta_description = (
                    f"{self.branded_business_name} provides expert {trade.lower()} services in {area}, {state}. "
                    f"{years}Licensed and insured. Call now for a free estimate."
                )
            else:
                self.meta_description = (
                    f"Need a {trade.lower()} in {area}? Fast, licensed, insured professionals available 24/7. "
                    f"Free estimates. Call now or request service online."
                )

        if not self.h1_headline:
            self.h1_headline = f"{trade} in {area}, {state}"

        if not self.hero_subheadline:
            self.hero_subheadline = (
                f"Fast, reliable {trade.lower()} service when you need it most. "
                f"Licensed and insured professionals available 24/7."
            )

        if not self.about_section:
            if self.page_type == 'customer' and self.branded_business_name:
                years_text = f"for {self.branded_years_in_business} years" if self.branded_years_in_business else ""
                self.about_section = (
                    f"{self.branded_business_name} has been providing expert {trade.lower()} services "
                    f"to {area} and surrounding areas {years_text}. Our team of licensed professionals "
                    f"is available when you need us most. We take pride in quality workmanship, fair pricing, "
                    f"and customer satisfaction. Call us today for a free estimate."
                )
            else:
                self.about_section = (
                    f"Finding a reliable {trade.lower()} in {area} shouldn't be stressful. "
                    f"Our network of licensed, insured {trade.lower()} professionals serves {area} and "
                    f"surrounding communities. Whether it's an emergency at 2 AM or a scheduled service call, "
                    f"we connect you with the right professional fast."
                )

        if not self.services_offered and self.trade.service_keywords:
            keywords = [kw.strip().title() for kw in self.trade.service_keywords.split(',') if kw.strip()]
            self.services_offered = '\n'.join(keywords[:12])

        if not self.faq_section:
            self.faq_section = self._generate_faqs()

    def _generate_faqs(self):
        trade = self.trade.name.lower()
        area = self.area.name
        state = self.area.state_full or self.area.state
        return [
            {
                'question': f"How much does a {trade} cost in {area}?",
                'answer': (
                    f"The cost of {trade} services in {area} varies depending on the job. "
                    f"Most {trade}s offer free estimates. Contact us to get a quote for your specific needs."
                ),
            },
            {
                'question': f"How do I find a licensed {trade} in {area}?",
                'answer': (
                    f"All {trade} professionals in our network are licensed and insured in {state}. "
                    f"Call us or submit a request online and we'll connect you with a vetted professional."
                ),
            },
            {
                'question': f"Are {trade} services available on weekends in {area}?",
                'answer': (
                    f"Yes. Our {trade} professionals serve {area} 7 days a week, including weekends and holidays. "
                    f"Emergency services are available 24/7."
                ),
            },
            {
                'question': f"How quickly can a {trade} arrive in {area}?",
                'answer': (
                    f"For emergencies, a {trade} can typically arrive in {area} within 30-60 minutes. "
                    f"For scheduled service, same-day and next-day appointments are usually available."
                ),
            },
        ]

    def get_phone_display(self):
        if self.page_type == 'customer' and self.branded_phone:
            return self.branded_phone
        return self.signalwire_phone or ''

    def get_business_name_display(self):
        if self.page_type == 'customer' and self.branded_business_name:
            return self.branded_business_name
        return 'SalesSignal AI'


class ServicePageSubmission(models.Model):
    URGENCY_CHOICES = [
        ('emergency', 'Emergency — Need help now'),
        ('today', 'Today'),
        ('this_week', 'This week'),
        ('getting_quotes', 'Just getting quotes'),
    ]
    SOURCE_CHOICES = [
        ('form', 'Web Form'),
        ('phone', 'Phone Call'),
    ]
    STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('converted', 'Converted'),
        ('lost', 'Lost'),
    ]

    landing_page = models.ForeignKey(
        ServiceLandingPage, on_delete=models.CASCADE, related_name='submissions',
    )
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    problem_description = models.TextField()
    urgency = models.CharField(max_length=20, choices=URGENCY_CHOICES, default='today')

    submitted_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='form')

    routed_to = models.ForeignKey(
        BusinessProfile, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='service_page_leads',
    )
    routed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.name} - {self.landing_page} ({self.submitted_at:%Y-%m-%d})"
