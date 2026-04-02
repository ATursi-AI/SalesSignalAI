from django.db import models
from django.contrib.auth.models import User


class ServiceCategory(models.Model):
    INDUSTRY_GROUPS = [
        ('home_services', 'Home Services & Repair'),
        ('cleaning', 'Cleaning Services'),
        ('outdoor', 'Outdoor & Landscaping'),
        ('construction', 'Construction & Remodeling'),
        ('auto', 'Automotive'),
        ('professional', 'Professional Services'),
        ('healthcare', 'Healthcare & Wellness'),
        ('events', 'Events & Entertainment'),
        ('education', 'Education & Training'),
        ('pet', 'Pet Services'),
        ('senior', 'Senior Care'),
        ('technology', 'Technology'),
    ]

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    icon = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    industry_group = models.CharField(
        max_length=30, choices=INDUSTRY_GROUPS, default='home_services',
        help_text='Groups categories in the onboarding wizard',
    )
    default_keywords = models.JSONField(default=list)
    craigslist_section = models.CharField(max_length=50, blank=True)
    google_maps_terms = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        verbose_name_plural = 'Service Categories'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class ServiceSubcategory(models.Model):
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, related_name='subcategories')
    name = models.CharField(max_length=100)
    slug = models.SlugField()
    additional_keywords = models.JSONField(default=list)

    class Meta:
        verbose_name_plural = 'Service Subcategories'
        unique_together = ['category', 'slug']
        ordering = ['name']

    def __str__(self):
        return f"{self.category.name} > {self.name}"


class BusinessProfile(models.Model):
    TIER_CHOICES = [
        ('none', 'No Plan'),
        ('outreach', 'Starter AI ($599/mo)'),
        ('growth', 'Growth AI ($1,199/mo)'),
        ('dominate', 'Dominate AI ($1,999/mo)'),
        ('concierge', 'Concierge (Custom)'),
        ('custom_outbound', 'Custom Outbound (Custom)'),
    ]

    ACCOUNT_STATUS_CHOICES = [
        ('active', 'Active'),
        ('trial', 'Free Trial'),
        ('pending_payment', 'Pending Payment'),
        ('pending_plan', 'Pending Plan Selection'),
        ('pending_verification', 'Pending Email Verification'),
        ('paused', 'Paused'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='business_profile')
    business_name = models.CharField(max_length=200)
    owner_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField()
    website = models.URLField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    zip_code = models.CharField(max_length=10, blank=True)
    service_category = models.ForeignKey(ServiceCategory, on_delete=models.PROTECT, null=True, blank=True)
    service_subcategories = models.ManyToManyField(ServiceSubcategory, blank=True)
    service_radius_miles = models.IntegerField(default=15)
    service_zip_codes = models.JSONField(default=list, blank=True)
    logo = models.ImageField(upload_to='logos/', blank=True)
    alert_via_email = models.BooleanField(default=True)
    alert_via_sms = models.BooleanField(default=False)
    alert_phone = models.CharField(max_length=20, blank=True)
    subscription_tier = models.CharField(max_length=20, choices=TIER_CHOICES, default='none')
    stripe_customer_id = models.CharField(max_length=100, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    account_status = models.CharField(max_length=30, choices=ACCOUNT_STATUS_CHOICES, default='active')
    is_active = models.BooleanField(default=True)
    onboarding_complete = models.BooleanField(default=False)

    # Trial
    trial_leads_remaining = models.IntegerField(default=10, help_text='Free lead views remaining for trial users')

    # Sales-assisted signup
    created_by_sales = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    temp_password = models.CharField(max_length=20, blank=True)

    # Onboarding data
    years_in_business = models.IntegerField(null=True, blank=True)
    num_employees = models.CharField(max_length=20, blank=True)
    marketing_channels = models.JSONField(default=list, blank=True)
    marketing_budget = models.CharField(max_length=50, blank=True)
    biggest_challenge = models.TextField(blank=True)
    desired_customers_per_month = models.CharField(max_length=20, blank=True)
    business_description = models.TextField(blank=True)
    email_style_guide = models.TextField(
        blank=True,
        help_text='Describe how you want outreach emails written (tone, style, key points).',
    )
    email_signature = models.TextField(
        blank=True,
        help_text='Your preferred email sign-off (name, title, phone, website).',
    )

    # Custom SMTP — when enabled, outreach campaigns send through customer's own server
    use_custom_smtp = models.BooleanField(
        default=False,
        help_text='Send outreach emails through your own SMTP server instead of our default.',
    )
    custom_smtp_host = models.CharField(max_length=255, blank=True)
    custom_smtp_port = models.IntegerField(default=587)
    custom_smtp_username = models.CharField(max_length=255, blank=True)
    custom_smtp_password_encrypted = models.TextField(
        blank=True,
        help_text='Fernet-encrypted SMTP password. Use set_smtp_password() / get_smtp_password().',
    )
    custom_from_email = models.EmailField(blank=True)
    custom_from_name = models.CharField(max_length=200, blank=True)

    # Theme preference
    theme_preference = models.CharField(
        max_length=10, choices=[('dark', 'Dark'), ('light', 'Light')],
        default='light',
    )

    # Welcome banner tracking
    has_seen_welcome = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_smtp_password(self, plaintext):
        """Encrypt and store the SMTP password."""
        from core.utils.crypto import encrypt_value
        self.custom_smtp_password_encrypted = encrypt_value(plaintext)

    def get_smtp_password(self):
        """Decrypt and return the SMTP password."""
        from core.utils.crypto import decrypt_value
        return decrypt_value(self.custom_smtp_password_encrypted)

    def __str__(self):
        return self.business_name or f"Profile for {self.user.username}"

    def get_active_keywords(self):
        """Return list of active keyword strings for this business."""
        return list(
            self.keywords.filter(is_active=True).values_list('keyword', flat=True)
        )

    def populate_default_keywords(self):
        """
        Populate UserKeyword records from the business's ServiceCategory defaults.
        Skips keywords that already exist. Called during onboarding and when
        category changes.
        """
        if not self.service_category:
            return 0

        keywords = list(self.service_category.default_keywords or [])
        for sub in self.service_category.subcategories.all():
            keywords.extend(sub.additional_keywords or [])

        created = 0
        for kw in keywords:
            _, was_created = UserKeyword.objects.get_or_create(
                business=self,
                keyword=kw,
                defaults={'source': 'category', 'is_active': True},
            )
            if was_created:
                created += 1
        return created


class UserKeyword(models.Model):
    SOURCE_CHOICES = [
        ('category', 'Category Default'),
        ('subcategory', 'Subcategory'),
        ('custom', 'Custom'),
    ]

    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='keywords')
    keyword = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='custom')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['business', 'keyword']
        ordering = ['source', 'keyword']

    def __str__(self):
        status = 'ON' if self.is_active else 'OFF'
        return f"{self.keyword} [{status}] ({self.business})"
