from django.db import models
from django.contrib.auth.models import User


class ServiceCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    icon = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
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
        ('starter', 'Starter'),
        ('growth', 'Growth'),
        ('pro', 'Pro'),
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
    subscription_tier = models.CharField(max_length=20, choices=TIER_CHOICES, default='starter')
    stripe_customer_id = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    onboarding_complete = models.BooleanField(default=False)
    email_style_guide = models.TextField(
        blank=True,
        help_text='Describe how you want outreach emails written (tone, style, key points).',
    )
    email_signature = models.TextField(
        blank=True,
        help_text='Your preferred email sign-off (name, title, phone, website).',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
