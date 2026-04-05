from django.urls import path
from core.views import landing, auth, onboarding, dashboard, leads, competitors, territory, campaigns, analytics
from core.views import monitor_health, webhooks, user_settings, admin_leads, ingest_api, crm
from core.views import sales_admin, sales, industries, prospect_videos, static_pages, seo, call_center
from core.views import service_pages, signup, blog, workflows, conversations, telegram_bot, engagement
from core.views import geo_audit, agent_rep

urlpatterns = [
    path('', landing.landing_page, name='landing'),
    path('pricing/', landing.pricing_page, name='pricing'),
    path('about/', static_pages.about_page, name='about_page'),
    path('privacy/', static_pages.privacy_page, name='privacy_page'),
    path('terms/', static_pages.terms_page, name='terms_page'),
    path('sitemap.xml', seo.sitemap_xml, name='sitemap'),
    path('robots.txt', seo.robots_txt, name='robots_txt'),
    path('llms.txt', seo.llms_txt, name='llms_txt'),
    path('google2568d017b4e7e9e5.html', seo.google_verification, name='google_verification'),
    # Blog
    path('blog/', blog.blog_list, name='blog_list'),
    path('blog/<slug:slug>/', blog.blog_detail, name='blog_detail'),

    path('industries/', industries.industry_index, name='industry_index'),
    path('industries/<slug:slug>/', industries.industry_detail, name='industry_detail'),

    # Service Landing Pages
    path('find/<slug:trade_slug>/<slug:area_slug>/', service_pages.service_landing_page, name='service_landing_page'),
    path('pro/<slug:customer_slug>/<slug:area_slug>/', service_pages.service_landing_page_branded, name='service_landing_page_branded'),
    path('api/service-page-submit/', service_pages.service_page_submit, name='service_page_submit'),
    path('admin/service-pages/', service_pages.service_page_list, name='service_page_list'),
    path('admin/service-pages/create/', service_pages.service_page_create, name='service_page_create'),
    path('admin/service-pages/bulk-create/', service_pages.service_page_bulk_create, name='service_page_bulk_create'),
    path('admin/service-pages/<int:page_id>/edit/', service_pages.service_page_edit, name='service_page_edit'),
    path('admin/service-pages/submissions/', service_pages.service_page_submissions, name='service_page_submissions'),
    path('admin/service-pages/submissions/<int:submission_id>/action/', service_pages.service_page_submission_action, name='service_page_submission_action'),
    path('signup/', signup.signup_view, name='signup'),
    path('verify/<str:uidb64>/<str:token>/', signup.verify_email, name='verify_email'),
    path('auth/register/', auth.register_view, name='register'),
    path('auth/login/', auth.login_view, name='login'),
    path('auth/logout/', auth.logout_view, name='logout'),
    path('auth/password-change/', signup.force_password_change, name='force_password_change'),
    path('auth/password-reset/', auth.password_reset_request, name='password_reset'),
    path('auth/password-reset/confirm/<str:uidb64>/<str:token>/', auth.password_reset_confirm, name='password_reset_confirm'),

    # Stripe
    path('api/stripe/webhook/', signup.stripe_webhook, name='stripe_webhook'),
    path('api/stripe/checkout/', signup.create_checkout_session, name='stripe_checkout'),
    path('dashboard/billing/', signup.billing_page, name='billing_page'),
    path('dashboard/billing/portal/', signup.create_portal_session, name='stripe_portal'),

    # Sales-assisted
    path('sales/create-customer/', signup.sales_create_customer, name='sales_create_customer'),
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
    path('dashboard/conversations/', conversations.conversations, name='conversations'),
    path('dashboard/conversations/<int:assignment_id>/', conversations.conversation_detail_api, name='conversation_detail_api'),
    path('dashboard/conversations/<int:assignment_id>/update/', conversations.conversation_update, name='conversation_update'),
    path('dashboard/appointments/', crm.appointment_list, name='crm_appointments'),
    path('dashboard/appointments/create/', crm.appointment_create, name='crm_appointment_create'),
    path('dashboard/appointments/<int:appointment_id>/status/', crm.appointment_update_status, name='crm_appointment_status'),
    path('dashboard/competitors/', crm.competitor_dashboard, name='crm_competitors'),
    path('dashboard/revenue-data/', crm.revenue_data, name='crm_revenue_data'),

    path('leads/', leads.lead_feed, name='lead_feed'),
    path('leads/<int:assignment_id>/', leads.lead_detail, name='lead_detail'),
    path('leads/<int:assignment_id>/status/', leads.lead_update_status, name='lead_update_status'),
    path('leads/<int:assignment_id>/dismiss/', leads.lead_dismiss, name='lead_dismiss'),
    path('leads/<int:assignment_id>/service-tier/', leads.lead_set_service_tier, name='lead_set_service_tier'),
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
    # Workflows
    path('dashboard/workflows/', workflows.workflow_list, name='workflow_list'),
    path('dashboard/workflows/create/', workflows.workflow_builder, name='workflow_builder'),
    path('dashboard/workflows/<int:rule_id>/', workflows.workflow_detail, name='workflow_detail'),
    path('dashboard/workflows/<int:rule_id>/edit/', workflows.workflow_builder, name='workflow_edit'),
    path('dashboard/workflows/<int:rule_id>/toggle/', workflows.workflow_toggle, name='workflow_toggle'),
    path('dashboard/workflows/<int:rule_id>/delete/', workflows.workflow_delete, name='workflow_delete'),

    path('campaigns/', campaigns.campaign_list, name='campaign_list'),
    path('campaigns/new/', campaigns.campaign_wizard, name='campaign_wizard'),
    path('campaigns/prospect-scrape/', campaigns.prospect_scrape, name='prospect_scrape'),
    path('campaigns/prospects/', campaigns.prospect_list_api, name='prospect_list_api'),
    path('campaigns/<int:campaign_id>/', campaigns.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:campaign_id>/action/', campaigns.campaign_action, name='campaign_action'),
    path('campaigns/<int:campaign_id>/add-prospect/', campaigns.campaign_add_prospects, name='campaign_add_prospect'),
    path('campaigns/<int:campaign_id>/import-leads/', campaigns.campaign_import_leads, name='campaign_import_leads'),
    path('campaigns/<int:campaign_id>/import-contacts/', campaigns.campaign_import_contacts, name='campaign_import_contacts'),
    path('campaigns/<int:campaign_id>/import-csv/', campaigns.campaign_import_csv, name='campaign_import_csv'),
    path('compose/', campaigns.compose_email, name='compose_email'),
    path('api/email-templates/', campaigns.email_templates_api, name='email_templates_api'),
    path('api/quick-send/', campaigns.quick_send_email, name='quick_send_email'),
    path('campaigns/leads-api/', campaigns.campaign_leads_api, name='campaign_leads_api'),
    path('campaigns/contacts-api/', campaigns.campaign_contacts_api, name='campaign_contacts_api'),
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
    path('admin-leads/customers/', admin_leads.customer_accounts, name='customer_accounts'),
    path('admin-leads/mission-control/', admin_leads.mission_control, name='mission_control'),
    path('admin-leads/mission-control/run/', admin_leads.run_monitor_now, name='run_monitor'),
    path('admin-leads/mission-control/agent/', admin_leads.launch_agent, name='launch_agent'),
    path('admin-leads/mission-control/agent/<int:mission_id>/', admin_leads.agent_mission_status, name='agent_mission_status'),
    path('admin-leads/detail/<int:lead_id>/', admin_leads.lead_detail_api, name='admin_lead_detail_api'),
    path('admin-leads/detail/<int:lead_id>/action/', admin_leads.lead_action, name='admin_lead_action'),
    path('admin-leads/bulk/', admin_leads.lead_bulk_action, name='admin_lead_bulk_action'),
    path('admin-leads/delete-all/', admin_leads.lead_delete_all, name='admin_lead_delete_all'),
    path('admin-leads/<str:group>/', admin_leads.source_group_page, name='leads_source_group'),

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
    path('sales/dashboard/', sales.sales_dashboard, name='sales_dashboard'),
    path('sales/quick-log/', sales.quick_log, name='sales_quick_log'),
    path('sales/complete-task/', sales.complete_task, name='sales_complete_task'),
    path('sales/pipeline/', sales.pipeline, name='sales_pipeline'),
    path('sales/pipeline/move/', sales.pipeline_move, name='sales_pipeline_move'),
    path('sales/prospects/', sales.prospects, name='sales_prospects'),
    path('sales/prospects/<int:prospect_id>/', sales.prospect_detail, name='sales_prospect_detail'),
    path('sales/prospects/<int:prospect_id>/email/', sales.send_prospect_email, name='sales_send_email'),
    path('sales/email-templates/', sales.get_email_templates, name='sales_email_templates'),
    path('sales/call-scripts/', sales.get_call_scripts, name='sales_call_scripts'),
    path('sales/prospects/<int:prospect_id>/script/', sales.get_prospect_script, name='sales_prospect_script'),
    path('sales/today/', sales.today_calls, name='sales_today'),
    path('sales/calendar/', sales.sales_calendar, name='sales_calendar'),
    path('sales/calendar/reschedule/<int:prospect_id>/', sales.calendar_reschedule, name='calendar_reschedule'),
    path('sales/stats/', sales.stats, name='sales_stats'),
    path('sales/high-value/', sales.high_value_leads, name='sales_high_value_leads'),

    # Prospect Video Pages
    path('demo/<slug:slug>/', prospect_videos.prospect_video_landing, name='prospect_video_landing'),
    path('admin/prospect-videos/', prospect_videos.prospect_video_list, name='prospect_video_list'),
    path('admin/prospect-videos/create/', prospect_videos.prospect_video_create, name='prospect_video_create'),
    path('admin/prospect-videos/<int:video_id>/edit/', prospect_videos.prospect_video_edit, name='prospect_video_edit'),
    path('admin/prospect-videos/<int:video_id>/stats/', prospect_videos.prospect_video_stats, name='prospect_video_stats'),
    path('api/prospect-video-track/', prospect_videos.prospect_video_track, name='prospect_video_track'),
    path('api/prospect-video-intake/', prospect_videos.prospect_video_intake, name='prospect_video_intake'),

    # GEO Audit Tool
    path('tools/geo-audit/', geo_audit.geo_audit_tool, name='geo_audit_tool'),
    path('tools/geo-audit/api/', geo_audit.geo_audit_api, name='geo_audit_api'),
    path('tools/geo-audit/pdf/', geo_audit.geo_audit_pdf, name='geo_audit_pdf'),
    path('tools/geo-audit/email/', geo_audit.geo_audit_email, name='geo_audit_email'),

    # Agent REP — Reputation Intelligence
    path('tools/agent-rep/', agent_rep.agent_rep_tool, name='agent_rep_tool'),
    path('tools/agent-rep/api/', agent_rep.agent_rep_api, name='agent_rep_api'),
    path('tools/agent-rep/pdf/', agent_rep.agent_rep_pdf, name='agent_rep_pdf'),
    path('tools/agent-rep/email/', agent_rep.agent_rep_email, name='agent_rep_email'),

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
    path('api/signalwire/relay-token/', call_center.get_relay_token, name='signalwire_relay_token'),
    path('api/signalwire/lead-lookup/', call_center.lookup_lead_by_phone, name='signalwire_lead_lookup'),
    path('api/signalwire/assigned-leads/', call_center.get_assigned_leads, name='signalwire_assigned_leads'),
    path('api/signalwire/recording-webhook/', call_center.recording_webhook, name='signalwire_recording_webhook'),
    path('api/signalwire/dialer-queue/', call_center.get_dialer_queue, name='dialer_queue'),
    path('api/signalwire/dialer-disposition/', call_center.log_dialer_disposition, name='dialer_disposition'),
    # Telegram
    path('api/telegram/webhook/', telegram_bot.telegram_webhook, name='telegram_webhook'),
    path('api/signalwire/transfer-call/', call_center.transfer_call, name='signalwire_transfer_call'),
    path('api/signalwire/transfer-xml/', call_center.transfer_xml, name='signalwire_transfer_xml'),

    # Call Center Pages
    path('sales/sms-inbox/', call_center.sms_inbox, name='sms_inbox'),
    path('sales/sms-inbox/api/', call_center.sms_inbox_api, name='sms_inbox_api'),
    path('sales/phone/', call_center.softphone, name='softphone'),
    path('sales/call-center/', call_center.call_center_dashboard, name='call_center_dashboard'),
    path('sales/my-calls/', call_center.my_calls, name='my_calls'),

    # ── Engagement: Voicemail Drops ──
    path('dashboard/voicemail-drops/', engagement.voicemail_drops, name='voicemail_drops'),
    path('dashboard/voicemail-drops/create/', engagement.voicemail_drop_create, name='voicemail_drop_create'),
    path('dashboard/voicemail-drops/<int:vm_id>/delete/', engagement.voicemail_drop_delete, name='voicemail_drop_delete'),
    path('api/voicemail-drop/send/', engagement.voicemail_drop_send, name='voicemail_drop_send'),
    path('api/voicemail-drop/status/', engagement.voicemail_drop_status_webhook, name='voicemail_drop_status'),

    # ── Engagement: Booking Pages ──
    path('dashboard/booking-pages/', engagement.booking_page_list, name='booking_page_list'),
    path('dashboard/booking-pages/create/', engagement.booking_page_create, name='booking_page_create'),
    path('dashboard/booking-pages/<int:page_id>/update/', engagement.booking_page_update, name='booking_page_update'),
    path('dashboard/bookings/<int:submission_id>/action/', engagement.booking_submission_action, name='booking_submission_action'),
    path('book/<slug:slug>/', engagement.booking_public_page, name='booking_public_page'),
    path('book/<slug:slug>/submit/', engagement.booking_submit, name='booking_submit'),

    # ── Engagement: Review Campaigns ──
    path('dashboard/review-campaigns/', engagement.review_campaigns, name='review_campaigns'),
    path('dashboard/review-campaigns/create/', engagement.review_campaign_create, name='review_campaign_create'),
    path('dashboard/review-campaigns/<int:campaign_id>/', engagement.review_campaign_detail, name='review_campaign_detail'),
    path('dashboard/review-campaigns/<int:campaign_id>/send/', engagement.review_campaign_send, name='review_campaign_send'),
    path('dashboard/review-campaigns/<int:campaign_id>/toggle/', engagement.review_campaign_toggle, name='review_campaign_toggle'),
    path('r/<int:request_id>/', engagement.review_click_track, name='review_click_track'),

    # Webhooks & Compliance
    path('webhooks/sendgrid/', webhooks.sendgrid_webhook, name='sendgrid_webhook'),
    path('unsubscribe/', webhooks.unsubscribe_page, name='unsubscribe'),
]
