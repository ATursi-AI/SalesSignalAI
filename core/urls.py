from django.urls import path
from core.views import landing, auth, onboarding, dashboard, leads, competitors, territory, campaigns, analytics
from core.views import monitor_health, webhooks, user_settings, admin_leads, ingest_api, crm
from core.views import sales_admin, sales, industries, prospect_videos, static_pages, seo, call_center

urlpatterns = [
    path('', landing.landing_page, name='landing'),
    path('about/', static_pages.about_page, name='about_page'),
    path('privacy/', static_pages.privacy_page, name='privacy_page'),
    path('terms/', static_pages.terms_page, name='terms_page'),
    path('sitemap.xml', seo.sitemap_xml, name='sitemap'),
    path('robots.txt', seo.robots_txt, name='robots_txt'),
    path('google2568d017b4e7e9e5.html', seo.google_verification, name='google_verification'),
    path('industries/', industries.industry_index, name='industry_index'),
    path('industries/<slug:slug>/', industries.industry_detail, name='industry_detail'),
    path('auth/register/', auth.register_view, name='register'),
    path('auth/login/', auth.login_view, name='login'),
    path('auth/logout/', auth.logout_view, name='logout'),
    path('onboarding/', onboarding.onboarding_view, name='onboarding'),
    path('dashboard/', dashboard.dashboard_home, name='dashboard_home'),

    # CRM
    path('dashboard/pipeline/', crm.pipeline, name='crm_pipeline'),
    path('dashboard/pipeline/move/', crm.pipeline_move, name='crm_pipeline_move'),
    path('dashboard/contacts/', crm.contact_list, name='crm_contacts'),
    path('dashboard/contacts/create/', crm.contact_create, name='crm_contact_create'),
    path('dashboard/contacts/<int:contact_id>/', crm.contact_detail, name='crm_contact_detail'),
    path('dashboard/contacts/<int:contact_id>/note/', crm.contact_add_note, name='crm_contact_note'),
    path('dashboard/contacts/<int:contact_id>/update/', crm.contact_update, name='crm_contact_update'),
    path('dashboard/inbox/', crm.inbox, name='crm_inbox'),
    path('dashboard/appointments/', crm.appointment_list, name='crm_appointments'),
    path('dashboard/appointments/create/', crm.appointment_create, name='crm_appointment_create'),
    path('dashboard/appointments/<int:appointment_id>/status/', crm.appointment_update_status, name='crm_appointment_status'),
    path('dashboard/competitors/', crm.competitor_dashboard, name='crm_competitors'),
    path('dashboard/revenue-data/', crm.revenue_data, name='crm_revenue_data'),

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
    path('campaigns/<int:campaign_id>/add-prospect/', campaigns.campaign_add_prospects, name='campaign_add_prospect'),
    path('campaigns/<int:campaign_id>/prospects/<int:prospect_id>/', campaigns.prospect_detail_api, name='prospect_detail_api'),
    path('campaigns/<int:campaign_id>/prospects/<int:prospect_id>/status/', campaigns.prospect_mark_status, name='prospect_mark_status'),
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

    # Lead Repository — Command Center + Source Groups (staff only)
    path('admin-leads/', admin_leads.lead_repository, name='admin_lead_repository'),
    path('admin-leads/api/', admin_leads.lead_repository_api, name='admin_lead_repository_api'),
    path('admin-leads/<str:group>/', admin_leads.source_group_page, name='leads_source_group'),
    path('admin-leads/detail/<int:lead_id>/', admin_leads.lead_detail_api, name='admin_lead_detail_api'),
    path('admin-leads/detail/<int:lead_id>/action/', admin_leads.lead_action, name='admin_lead_action'),
    path('admin-leads/bulk/', admin_leads.lead_bulk_action, name='admin_lead_bulk_action'),
    path('admin-leads/delete-all/', admin_leads.lead_delete_all, name='admin_lead_delete_all'),

    # Monitor Health (staff only)
    path('monitors/', monitor_health.monitor_health_dashboard, name='monitor_health'),
    path('monitors/api/', monitor_health.monitor_health_api, name='monitor_health_api'),

    # Lead Ingestion API
    path('api/ingest-lead/', ingest_api.ingest_lead, name='ingest_lead'),

    # Sales Admin (superuser)
    path('sales-admin/', sales_admin.dashboard, name='sales_admin_dashboard'),
    path('sales-admin/team/', sales_admin.manage_team, name='sales_admin_team'),
    path('sales-admin/assign/', sales_admin.assign_prospects, name='sales_admin_assign'),
    path('sales-admin/team/<int:sp_id>/', sales_admin.salesperson_detail, name='sales_admin_sp_detail'),

    # Sales (salesperson)
    path('sales/pipeline/', sales.pipeline, name='sales_pipeline'),
    path('sales/pipeline/move/', sales.pipeline_move, name='sales_pipeline_move'),
    path('sales/prospects/', sales.prospects, name='sales_prospects'),
    path('sales/prospects/<int:prospect_id>/', sales.prospect_detail, name='sales_prospect_detail'),
    path('sales/today/', sales.today_calls, name='sales_today'),
    path('sales/stats/', sales.stats, name='sales_stats'),

    # Prospect Video Pages
    path('demo/<slug:slug>/', prospect_videos.prospect_video_landing, name='prospect_video_landing'),
    path('admin/prospect-videos/', prospect_videos.prospect_video_list, name='prospect_video_list'),
    path('admin/prospect-videos/create/', prospect_videos.prospect_video_create, name='prospect_video_create'),
    path('admin/prospect-videos/<int:video_id>/edit/', prospect_videos.prospect_video_edit, name='prospect_video_edit'),
    path('admin/prospect-videos/<int:video_id>/stats/', prospect_videos.prospect_video_stats, name='prospect_video_stats'),
    path('api/prospect-video-track/', prospect_videos.prospect_video_track, name='prospect_video_track'),
    path('api/prospect-video-intake/', prospect_videos.prospect_video_intake, name='prospect_video_intake'),

    # SignalWire Webhooks (called by SignalWire — no auth)
    path('api/signalwire/sms-webhook/', call_center.sms_webhook, name='signalwire_sms_webhook'),
    path('api/signalwire/voice-webhook/', call_center.voice_webhook, name='signalwire_voice_webhook'),
    path('api/signalwire/call-status-webhook/', call_center.call_status_webhook, name='signalwire_call_status_webhook'),
    path('api/signalwire/transcription-webhook/', call_center.transcription_webhook, name='signalwire_transcription_webhook'),

    # SMS API (staff)
    path('api/sms/send/', call_center.api_send_sms, name='api_send_sms'),
    path('api/sms/send-bulk/', call_center.api_send_bulk_sms, name='api_send_bulk_sms'),
    path('api/sms/reply/', call_center.api_sms_reply, name='api_sms_reply'),
    path('api/sms/thread/<str:phone>/', call_center.api_sms_thread, name='api_sms_thread'),

    # Call API (staff)
    path('api/calls/<int:call_id>/disposition/', call_center.api_call_disposition, name='api_call_disposition'),

    # Call Center Pages
    path('sales/sms-inbox/', call_center.sms_inbox, name='sms_inbox'),
    path('sales/sms-inbox/api/', call_center.sms_inbox_api, name='sms_inbox_api'),
    path('sales/phone/', call_center.softphone, name='softphone'),
    path('sales/call-center/', call_center.call_center_dashboard, name='call_center_dashboard'),
    path('sales/my-calls/', call_center.my_calls, name='my_calls'),

    # Webhooks & Compliance
    path('webhooks/sendgrid/', webhooks.sendgrid_webhook, name='sendgrid_webhook'),
    path('unsubscribe/', webhooks.unsubscribe_page, name='unsubscribe'),
]
