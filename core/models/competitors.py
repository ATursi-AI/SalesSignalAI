from django.db import models
from .business import BusinessProfile


class TrackedCompetitor(models.Model):
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='tracked_competitors')
    name = models.CharField(max_length=200)
    google_place_id = models.CharField(max_length=200, blank=True)
    yelp_url = models.URLField(blank=True)
    website = models.URLField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    current_google_rating = models.FloatField(null=True, blank=True)
    current_review_count = models.IntegerField(null=True, blank=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class CompetitorReview(models.Model):
    competitor = models.ForeignKey(TrackedCompetitor, on_delete=models.CASCADE, related_name='reviews')
    platform = models.CharField(max_length=20)
    reviewer_name = models.CharField(max_length=200, blank=True)
    rating = models.IntegerField()
    review_text = models.TextField()
    review_date = models.DateField(null=True, blank=True)
    is_negative = models.BooleanField(default=False)
    is_opportunity = models.BooleanField(default=False)
    ai_analysis = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-review_date']

    def __str__(self):
        return f"{self.competitor.name} - {self.rating} stars ({self.platform})"
