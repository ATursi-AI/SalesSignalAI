from django.contrib import admin
from core.models import (
    ServiceCategory, ServiceSubcategory, BusinessProfile, UserKeyword,
    Lead, LeadAssignment,
    ProspectBusiness, OutreachCampaign, OutreachEmail,
    TrackedCompetitor, CompetitorReview,
    MonitoredLocalSite, MonitoredFacebookGroup,
    MonitorRun, EmailSendLog, Unsubscribe,
    PermitSource, PropertyTransferSource,
    StateBusinessFilingSource,
    CodeViolationSource, HealthInspectionSource,
    LicensingBoardSource, CourtRecordSource,
    ProspectVideo,
    CallLog, SMSMessage, SMSOptOut,
)


@admin.register(CallLog)
class CallLogAdmin(admin.ModelAdmin):
    list_display = ['call_sid', 'direction', 'from_number', 'to_number', 'status', 'duration', 'disposition', 'started_at']
    list_filter = ['direction', 'status', 'disposition']
    search_fields = ['from_number', 'to_number', 'call_sid']
    raw_id_fields = ['lead', 'salesperson']


@admin.register(SMSMessage)
class SMSMessageAdmin(admin.ModelAdmin):
    list_display = ['direction', 'from_number', 'to_number', 'body_preview', 'status', 'is_yes_response', 'sent_at']
    list_filter = ['direction', 'status', 'is_yes_response', 'is_opt_out']
    search_fields = ['from_number', 'to_number', 'body']
    raw_id_fields = ['lead', 'salesperson']

    def body_preview(self, obj):
        return obj.body[:80]
    body_preview.short_description = 'Message'


@admin.register(SMSOptOut)
class SMSOptOutAdmin(admin.ModelAdmin):
    list_display = ['phone_number', 'opted_out_at']
    search_fields = ['phone_number']


class ServiceSubcategoryInline(admin.TabularInline):
    model = ServiceSubcategory
    extra = 1


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'icon', 'is_active', 'sort_order']
    list_filter = ['is_active']
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ServiceSubcategoryInline]


@admin.register(ServiceSubcategory)
class ServiceSubcategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'slug']
    list_filter = ['category']
    search_fields = ['name']


class UserKeywordInline(admin.TabularInline):
    model = UserKeyword
    extra = 1


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ['business_name', 'owner_name', 'service_category', 'city', 'state', 'subscription_tier', 'is_active']
    list_filter = ['subscription_tier', 'is_active', 'service_category', 'state']
    search_fields = ['business_name', 'owner_name', 'email']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [UserKeywordInline]


@admin.register(UserKeyword)
class UserKeywordAdmin(admin.ModelAdmin):
    list_display = ['keyword', 'business', 'is_active', 'source', 'created_at']
    list_filter = ['is_active', 'source']
    search_fields = ['keyword', 'business__business_name']


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ['platform', 'urgency_level', 'detected_location', 'detected_service_type', 'discovered_at']
    list_filter = ['platform', 'urgency_level', 'detected_service_type']
    search_fields = ['source_content', 'detected_location']
    readonly_fields = ['discovered_at', 'content_hash']


class LeadAssignmentInline(admin.TabularInline):
    model = LeadAssignment
    extra = 0


@admin.register(LeadAssignment)
class LeadAssignmentAdmin(admin.ModelAdmin):
    list_display = ['lead', 'business', 'status', 'alert_sent_at', 'created_at']
    list_filter = ['status']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ProspectBusiness)
class ProspectBusinessAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'city', 'state', 'email', 'google_rating']
    list_filter = ['state', 'email_validated']
    search_fields = ['name', 'email']


@admin.register(OutreachCampaign)
class OutreachCampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'business', 'status', 'emails_sent', 'emails_opened', 'emails_replied']
    list_filter = ['status']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(OutreachEmail)
class OutreachEmailAdmin(admin.ModelAdmin):
    list_display = ['prospect', 'campaign', 'sequence_number', 'status', 'sent_at']
    list_filter = ['status', 'sequence_number']


@admin.register(TrackedCompetitor)
class TrackedCompetitorAdmin(admin.ModelAdmin):
    list_display = ['name', 'business', 'current_google_rating', 'current_review_count', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(CompetitorReview)
class CompetitorReviewAdmin(admin.ModelAdmin):
    list_display = ['competitor', 'platform', 'rating', 'is_negative', 'is_opportunity', 'review_date']
    list_filter = ['platform', 'is_negative', 'is_opportunity']


@admin.register(MonitoredLocalSite)
class MonitoredLocalSiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'base_url', 'scrape_pattern', 'is_active', 'last_scraped']
    list_filter = ['scrape_pattern', 'is_active']
    search_fields = ['name', 'base_url']


@admin.register(MonitoredFacebookGroup)
class MonitoredFacebookGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'group_id', 'is_active', 'last_scraped', 'posts_scraped', 'leads_created']
    list_filter = ['is_active']
    search_fields = ['name', 'group_id']
    readonly_fields = ['created_at']


@admin.register(MonitorRun)
class MonitorRunAdmin(admin.ModelAdmin):
    list_display = ['monitor_name', 'status', 'started_at', 'duration_seconds', 'items_scraped', 'leads_created', 'errors']
    list_filter = ['monitor_name', 'status']
    readonly_fields = ['started_at', 'finished_at']


@admin.register(EmailSendLog)
class EmailSendLogAdmin(admin.ModelAdmin):
    list_display = ['date', 'emails_sent', 'emails_delivered', 'emails_bounced', 'emails_complained', 'warming_limit']
    list_filter = ['date']


@admin.register(Unsubscribe)
class UnsubscribeAdmin(admin.ModelAdmin):
    list_display = ['email', 'reason', 'created_at']
    search_fields = ['email']
    readonly_fields = ['created_at']


@admin.register(PermitSource)
class PermitSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'county', 'state', 'scrape_method', 'is_active', 'last_scraped']
    list_filter = ['state', 'scrape_method', 'is_active']
    search_fields = ['name', 'county', 'state']
    readonly_fields = ['created_at']


@admin.register(PropertyTransferSource)
class PropertyTransferSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'county', 'state', 'scrape_method', 'is_active', 'last_scraped']
    list_filter = ['state', 'scrape_method', 'is_active']
    search_fields = ['name', 'county', 'state']
    readonly_fields = ['created_at']


@admin.register(StateBusinessFilingSource)
class StateBusinessFilingSourceAdmin(admin.ModelAdmin):
    list_display = ['state_name', 'state', 'scrape_method', 'is_active', 'last_scraped']
    list_filter = ['scrape_method', 'is_active']
    search_fields = ['state_name', 'state']
    readonly_fields = ['created_at']


@admin.register(CodeViolationSource)
class CodeViolationSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'municipality', 'state', 'scrape_method', 'is_active', 'last_scraped']
    list_filter = ['state', 'scrape_method', 'is_active']
    search_fields = ['name', 'municipality', 'state']
    readonly_fields = ['created_at']


@admin.register(HealthInspectionSource)
class HealthInspectionSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'jurisdiction', 'state', 'scrape_method', 'failing_threshold', 'is_active', 'last_scraped']
    list_filter = ['state', 'scrape_method', 'is_active']
    search_fields = ['name', 'jurisdiction', 'state']
    readonly_fields = ['created_at']


@admin.register(LicensingBoardSource)
class LicensingBoardSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'state', 'license_type', 'scrape_method', 'is_active', 'last_scraped']
    list_filter = ['state', 'license_type', 'scrape_method', 'is_active']
    search_fields = ['name', 'state', 'license_type']
    readonly_fields = ['created_at']


@admin.register(CourtRecordSource)
class CourtRecordSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'county', 'state', 'scrape_method', 'is_active', 'last_scraped']
    list_filter = ['state', 'scrape_method', 'is_active']
    search_fields = ['name', 'county', 'state']
    readonly_fields = ['created_at']


@admin.register(ProspectVideo)
class ProspectVideoAdmin(admin.ModelAdmin):
    list_display = ['prospect_business_name', 'prospect_trade', 'prospect_city', 'status', 'page_views', 'video_plays', 'cta_clicks', 'sms_sent', 'created_at']
    list_filter = ['status', 'prospect_trade', 'trigger_type', 'sms_sent']
    search_fields = ['prospect_business_name', 'prospect_owner_name', 'slug']
    prepopulated_fields = {'slug': ('prospect_business_name',)}
    readonly_fields = ['created_at', 'page_views', 'video_plays', 'cta_clicks']
