"""
Monitor schedule — single source of truth for all automated monitors.
Used by run_all_monitors command and Mission Control dashboard.

Each entry: (command_name, kwargs_dict, frequency_hours, description, group)
  group is used for Mission Control dashboard grouping.
"""

MONITOR_GROUPS = {
    'public_records': {'label': 'Public Records', 'icon': 'bi-file-earmark-text', 'color': '#3B82F6'},
    'health':         {'label': 'Health Inspections', 'icon': 'bi-heart-pulse', 'color': '#DC2626'},
    'social_media':   {'label': 'Social Media', 'icon': 'bi-chat-dots', 'color': '#8B5CF6'},
    'reviews':        {'label': 'Reviews & Reputation', 'icon': 'bi-star', 'color': '#F59E0B'},
    'community':      {'label': 'Community & Forums', 'icon': 'bi-people', 'color': '#0D9488'},
    'google':         {'label': 'Google & Maps', 'icon': 'bi-google', 'color': '#059669'},
}

MONITOR_SCHEDULE = [
    # (command_name, kwargs_dict, frequency_hours, description, group)

    # ── PUBLIC RECORDS ────────────────────────────────────────────
    # NYC DOB Violations — every 6 hours, all boroughs
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'queens', 'days': 7}, 6, 'NYC DOB Violations — Queens', 'public_records'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'brooklyn', 'days': 7}, 6, 'NYC DOB Violations — Brooklyn', 'public_records'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'manhattan', 'days': 7}, 6, 'NYC DOB Violations — Manhattan', 'public_records'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'bronx', 'days': 7}, 6, 'NYC DOB Violations — Bronx', 'public_records'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'staten_island', 'days': 7}, 6, 'NYC DOB Violations — Staten Island', 'public_records'),

    # NYC DOB Permits — daily
    ('monitor_nyc_dob', {'type': 'permits', 'borough': 'queens', 'days': 3}, 24, 'NYC DOB Permits — Queens', 'public_records'),
    ('monitor_nyc_dob', {'type': 'permits', 'borough': 'brooklyn', 'days': 3}, 24, 'NYC DOB Permits — Brooklyn', 'public_records'),
    ('monitor_nyc_dob', {'type': 'permits', 'borough': 'manhattan', 'days': 3}, 24, 'NYC DOB Permits — Manhattan', 'public_records'),

    # Property Sales — daily
    ('monitor_property_sales_ny', {'days': 30}, 24, 'NYC Property Sales (ACRIS)', 'public_records'),

    # Business Filings — daily
    ('monitor_ny_business_filings', {'days': 7}, 24, 'NY Business Filings', 'public_records'),

    # California — general
    ('monitor_ca_contractors', {'days': 7}, 24, 'CA Contractor Licenses', 'public_records'),
    ('monitor_ca_violations', {'days': 7}, 24, 'CA OSHA Violations', 'public_records'),

    # California — Building Violations & Permits
    ('monitor_la_building_violations', {'days': 14}, 24, 'LA Building Violations', 'public_records'),
    ('monitor_sf_building_violations', {'days': 7}, 24, 'SF Building Violations', 'public_records'),
    ('monitor_sf_permits', {'days': 7}, 24, 'SF Building Permits', 'public_records'),

    # Chicago — Building Violations, Ordinance Violations
    ('monitor_chicago_building_violations', {'days': 7}, 24, 'Chicago Building Violations', 'public_records'),
    ('monitor_chicago_ordinance_violations', {'days': 14}, 24, 'Chicago Ordinance Violations', 'public_records'),

    # NYC — HPD Housing Violations (by borough)
    ('monitor_nyc_hpd_violations', {'borough': 'queens', 'days': 7}, 12, 'NYC HPD Violations — Queens', 'public_records'),
    ('monitor_nyc_hpd_violations', {'borough': 'brooklyn', 'days': 7}, 12, 'NYC HPD Violations — Brooklyn', 'public_records'),
    ('monitor_nyc_hpd_violations', {'borough': 'manhattan', 'days': 7}, 12, 'NYC HPD Violations — Manhattan', 'public_records'),
    ('monitor_nyc_hpd_violations', {'borough': 'bronx', 'days': 7}, 12, 'NYC HPD Violations — Bronx', 'public_records'),
    ('monitor_nyc_hpd_violations', {'borough': 'staten_island', 'days': 7}, 12, 'NYC HPD Violations — Staten Island', 'public_records'),

    # NYC — OATH/ECB Administrative Summonses
    ('monitor_nyc_ecb_summonses', {'days': 14}, 24, 'NYC ECB Summonses', 'public_records'),

    # ── LOS ANGELES ──────────────────────────────────────────────
    ('monitor_la_code_enforcement', {'days': 14}, 24, 'LA Code Enforcement Cases', 'public_records'),
    ('monitor_la_certificate_occupancy', {'days': 14}, 24, 'LA Certificate of Occupancy', 'public_records'),
    ('monitor_la_building_permits', {'days': 7}, 24, 'LA Building Permits', 'public_records'),

    # ── SAN FRANCISCO ────────────────────────────────────────────
    ('monitor_sf_fire_violations', {'days': 7}, 24, 'SF Fire Violations', 'public_records'),
    ('monitor_sf_health_inspections', {'days': 7}, 24, 'SF Health Inspections', 'health'),
    ('monitor_sf_boiler_permits', {'days': 14}, 24, 'SF Boiler Permits', 'public_records'),
    ('monitor_sf_permit_contacts', {'days': 7}, 24, 'SF Permit Contacts (Plumbing/Electrical/Building)', 'public_records'),

    # ── AUSTIN ───────────────────────────────────────────────────
    ('monitor_austin_construction_permits', {'days': 7}, 24, 'Austin Construction Permits', 'public_records'),
    ('monitor_austin_food_inspections', {'days': 7}, 24, 'Austin Food Inspections', 'health'),
    ('monitor_austin_pool_inspections', {'days': 14}, 24, 'Austin Pool Inspections', 'health'),
    ('monitor_austin_repeat_offender', {'days': 30}, 24, 'Austin Repeat Offender Properties', 'public_records'),

    # ── DALLAS ───────────────────────────────────────────────────
    ('monitor_dallas_code_violations', {'days': 14}, 24, 'Dallas Code Violations', 'public_records'),

    # ── SEATTLE ──────────────────────────────────────────────────
    ('monitor_seattle_code_complaints', {'days': 14}, 24, 'Seattle Code Complaints', 'public_records'),
    ('monitor_seattle_building_permits', {'days': 7}, 24, 'Seattle Building Permits', 'public_records'),
    ('monitor_seattle_electrical_permits', {'days': 7}, 24, 'Seattle Electrical Permits', 'public_records'),
    ('monitor_seattle_trade_permits', {'days': 7}, 24, 'Seattle Trade Permits', 'public_records'),

    # ── TEXAS STATEWIDE ──────────────────────────────────────────
    ('monitor_tceq_violations', {'days': 30}, 24, 'TCEQ Environmental Violations', 'public_records'),
    ('monitor_tceq_remediation', {'days': 30}, 24, 'TCEQ Remediation Sites', 'public_records'),

    # ── MONTGOMERY COUNTY MD ─────────────────────────────────────
    ('monitor_mc_housing_violations', {'days': 14}, 24, 'MC Housing Violations', 'public_records'),
    ('monitor_mc_permits', {'days': 7}, 24, 'MC Permits (7 types)', 'public_records'),
    ('monitor_mc_alcohol_violations', {'days': 30}, 24, 'MC Alcohol Violations', 'public_records'),

    # ── CONNECTICUT ──────────────────────────────────────────────
    ('monitor_ct_contaminated_sites', {'days': 30}, 24, 'CT Contaminated Sites', 'public_records'),
    ('monitor_ct_liquor_suspensions', {'days': 30}, 24, 'CT Liquor Suspensions', 'public_records'),
    ('monitor_ct_storage_tanks', {'days': 30}, 24, 'CT Storage Tanks', 'public_records'),

    # ── HEALTH INSPECTIONS ────────────────────────────────────────
    ('monitor_health_inspections', {'days': 7}, 24, 'NYC Health Inspections', 'health'),
    ('monitor_chicago_food_inspections', {'days': 7}, 24, 'Chicago Food Inspections', 'health'),
    ('monitor_vegas_health', {'days': 7}, 24, 'Las Vegas Health (SNHD)', 'health'),
    ('monitor_maricopa_health', {'days': 7}, 24, 'Phoenix/Maricopa Health', 'health'),
    ('monitor_pima_health', {'days': 7}, 24, 'Tucson/Pima Health', 'health'),
    ('monitor_ca_health', {'county': 'sacramento', 'days': 7}, 24, 'Sacramento Health', 'health'),
    ('monitor_ca_health', {'county': 'san_diego', 'days': 7}, 24, 'San Diego Health', 'health'),
    ('monitor_ca_health', {'county': 'santa_clara', 'days': 7}, 24, 'Santa Clara Health', 'health'),
    ('monitor_ca_health', {'county': 'la', 'days': 120}, 168, 'LA County Health (weekly)', 'health'),

    # ── SOCIAL MEDIA ──────────────────────────────────────────────
    ('monitor_reddit', {'state': 'NY', 'max_age_hours': 48}, 12, 'Reddit — NY', 'social_media'),
    ('monitor_reddit', {'state': 'CA', 'max_age_hours': 48}, 12, 'Reddit — CA', 'social_media'),
    ('monitor_reddit', {'state': 'TX', 'max_age_hours': 48}, 12, 'Reddit — TX', 'social_media'),
    ('monitor_reddit', {'state': 'IL', 'max_age_hours': 48}, 12, 'Reddit — IL', 'social_media'),
    ('monitor_reddit', {'state': 'WA', 'max_age_hours': 48}, 12, 'Reddit — WA', 'social_media'),
    ('monitor_reddit', {'state': 'MD', 'max_age_hours': 48}, 12, 'Reddit — MD', 'social_media'),
    ('monitor_reddit', {'state': 'CT', 'max_age_hours': 48}, 12, 'Reddit — CT', 'social_media'),
    ('monitor_nextdoor_search', {'days': 3}, 12, 'Nextdoor Search', 'social_media'),
    ('monitor_facebook_apify', {'days': 3}, 24, 'Facebook Groups (Apify)', 'social_media'),
    ('monitor_twitter_apify', {'days': 3}, 24, 'Twitter/X (Apify)', 'social_media'),
    ('monitor_threads', {'days': 3}, 24, 'Threads (Apify)', 'social_media'),
    ('monitor_tiktok', {'days': 3}, 24, 'TikTok (Apify)', 'social_media'),

    # ── REVIEWS & REPUTATION ──────────────────────────────────────
    ('monitor_google_reviews', {'days': 7}, 24, 'Google Reviews', 'reviews'),
    ('monitor_yelp_reviews', {'days': 7}, 24, 'Yelp Reviews', 'reviews'),
    ('monitor_bbb', {'days': 7}, 24, 'BBB Complaints', 'reviews'),
    ('monitor_angi_reviews', {'days': 7}, 24, 'Angi Reviews', 'reviews'),
    ('monitor_trustpilot', {'days': 7}, 24, 'Trustpilot Reviews', 'reviews'),
    ('monitor_porch', {'days': 7}, 24, 'Porch Reviews', 'reviews'),
    ('monitor_thumbtack', {'days': 7}, 24, 'Thumbtack Reviews', 'reviews'),
    ('monitor_houzz', {'days': 7}, 24, 'Houzz Reviews', 'reviews'),
    ('monitor_google_qna', {'days': 7}, 24, 'Google Q&A', 'reviews'),

    # ── COMMUNITY & FORUMS ────────────────────────────────────────
    ('monitor_biggerpockets', {'days': 7}, 24, 'BiggerPockets', 'community'),
    ('monitor_alignable', {'days': 7}, 24, 'Alignable', 'community'),
    ('monitor_quora', {'days': 7}, 24, 'Quora (Apify)', 'community'),
    ('monitor_trade_forums', {'days': 7}, 24, 'Trade Forums', 'community'),
    ('monitor_parent_communities', {'days': 7}, 24, 'Parent Communities', 'community'),
    ('monitor_citydata', {'days': 7}, 24, 'City-Data Forums', 'community'),
    ('monitor_patch', {'days': 3}, 24, 'Patch.com Local News', 'community'),
    ('monitor_craigslist', {'days': 3}, 24, 'Craigslist Services', 'community'),
    ('monitor_local_news', {'days': 3}, 24, 'Local News', 'community'),

    # ── GOOGLE & MAPS ─────────────────────────────────────────────
    ('monitor_google_places', {'category': 'plumber', 'city': 'Queens, NY'}, 12, 'Google Places — Plumber Queens', 'google'),
    ('monitor_google_places', {'category': 'electrician', 'city': 'Queens, NY'}, 12, 'Google Places — Electrician Queens', 'google'),
]
