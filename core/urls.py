from django.urls import path
from core.views import landing, auth, onboarding, dashboard, leads, competitors, territory, campaigns, analytics
from core.views import monitor_health, webhooks, user_settings, admin_leads, ingest_api

urlpatterns = [
    path('', landing.landing_page, name='landing'),
    path('auth/register/', auth.register_view, name='register'),
    path('auth/login/', auth.login_view, name='login'),
    path('auth/logout/', auth.logout_view, name='logout'),
    path('onboarding/', onboarding.onboarding_view, name='onboarding'),
    path('dashboard/', dashboard.dashboard_home, name='dashboard_home'),
    path('leads/', leads.lead_feed, name='lead_feed'),
    path('leads/<int:assignment_id>/', leads.lead_detail, name='lead_detail'),
    path('leads/<int:assignment_id>/status/', leads.lead_update_status, name='lead_update_status'),
    path('leads/bulk-action/', leads.lead_bulk_action, name='lead_bulk_action'),

    # Competitor Intelligence
    path('competitors/', competitors.competitor_list, name='competitor_list'),
    path('competitors/add/', competitors.competitor_add, name='competitor_add'),
    path('competitors/lookup/', competitors.competitor_lookup, name='competitor_lookup'),
    path('competitors/<int:competitor_id>/', competitors.competitor_detail, name='competitor_detail'),
    path('competitors/<int:competitor_id>/delete/', competitors.competitor_delete, name='competitor_delete'),

    # Territory Map
    path('territory/', territory.territory_map, name='territory_map'),
    path('territory/data/', territory.territory_data, name='territory_data'),

    # Outreach Campaigns
    path('campaigns/', campaigns.campaign_list, name='campaign_list'),
    path('campaigns/new/', campaigns.campaign_wizard, name='campaign_wizard'),
    path('campaigns/prospect-scrape/', campaigns.prospect_scrape, name='prospect_scrape'),
    path('campaigns/prospects/', campaigns.prospect_list_api, name='prospect_list_api'),
    path('campaigns/<int:campaign_id>/', campaigns.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:campaign_id>/action/', campaigns.campaign_action, name='campaign_action'),
    path('prospects/<int:prospect_id>/find-email/', campaigns.prospect_find_email, name='prospect_find_email'),
    path('prospects/<int:prospect_id>/validate/', campaigns.prospect_validate, name='prospect_validate'),

    # Analytics
    path('analytics/', analytics.analytics_dashboard, name='analytics_dashboard'),
    path('analytics/lead-volume/', analytics.analytics_lead_volume, name='analytics_lead_volume'),
    path('analytics/funnel/', analytics.analytics_funnel, name='analytics_funnel'),
    path('analytics/revenue/', analytics.analytics_revenue, name='analytics_revenue'),
    path('analytics/platform-performance/', analytics.analytics_platform_performance, name='analytics_platform_performance'),
    path('analytics/response-time/', analytics.analytics_response_time, name='analytics_response_time'),
    path('analytics/territory/', analytics.analytics_territory, name='analytics_territory'),

    # Settings
    path('settings/', user_settings.settings_page, name='settings_page'),
    path('settings/keywords/add/', user_settings.keyword_add, name='keyword_add'),
    path('settings/keywords/<int:keyword_id>/toggle/', user_settings.keyword_toggle, name='keyword_toggle'),
    path('settings/keywords/<int:keyword_id>/delete/', user_settings.keyword_delete, name='keyword_delete'),
    path('settings/keywords/reset/', user_settings.keyword_reset_defaults, name='keyword_reset_defaults'),
    path('settings/email-prefs/', user_settings.save_email_prefs, name='save_email_prefs'),
    path('settings/smtp/', user_settings.save_smtp_settings, name='save_smtp_settings'),
    path('settings/smtp/test/', user_settings.send_test_email, name='send_test_email'),
    path('settings/theme/', user_settings.save_theme, name='save_theme'),
    path('settings/dismiss-welcome/', user_settings.dismiss_welcome, name='dismiss_welcome'),

    # Lead Repository (staff only)
    path('admin-leads/', admin_leads.lead_repository, name='admin_lead_repository'),
    path('admin-leads/api/', admin_leads.lead_repository_api, name='admin_lead_repository_api'),
    path('admin-leads/<int:lead_id>/', admin_leads.lead_detail_api, name='admin_lead_detail_api'),
    path('admin-leads/<int:lead_id>/action/', admin_leads.lead_action, name='admin_lead_action'),
    path('admin-leads/bulk/', admin_leads.lead_bulk_action, name='admin_lead_bulk_action'),

    # Monitor Health (staff only)
    path('monitors/', monitor_health.monitor_health_dashboard, name='monitor_health'),
    path('monitors/api/', monitor_health.monitor_health_api, name='monitor_health_api'),

    # Lead Ingestion API
    path('api/ingest-lead/', ingest_api.ingest_lead, name='ingest_lead'),

    # Webhooks & Compliance
    path('webhooks/sendgrid/', webhooks.sendgrid_webhook, name='sendgrid_webhook'),
    path('unsubscribe/', webhooks.unsubscribe_page, name='unsubscribe'),
]
