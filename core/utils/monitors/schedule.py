"""
Monitor schedule — single source of truth for all automated monitors.
Used by run_all_monitors command and Mission Control dashboard.
"""

MONITOR_SCHEDULE = [
    # (command_name, kwargs_dict, frequency_hours, description)

    # NYC DOB Violations — every 6 hours, all boroughs
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'queens', 'days': 7}, 6, 'NYC DOB Violations — Queens'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'brooklyn', 'days': 7}, 6, 'NYC DOB Violations — Brooklyn'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'manhattan', 'days': 7}, 6, 'NYC DOB Violations — Manhattan'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'bronx', 'days': 7}, 6, 'NYC DOB Violations — Bronx'),
    ('monitor_nyc_dob', {'type': 'violations', 'borough': 'staten_island', 'days': 7}, 6, 'NYC DOB Violations — Staten Island'),

    # NYC DOB Permits — daily
    ('monitor_nyc_dob', {'type': 'permits', 'borough': 'queens', 'days': 3}, 24, 'NYC DOB Permits — Queens'),
    ('monitor_nyc_dob', {'type': 'permits', 'borough': 'brooklyn', 'days': 3}, 24, 'NYC DOB Permits — Brooklyn'),
    ('monitor_nyc_dob', {'type': 'permits', 'borough': 'manhattan', 'days': 3}, 24, 'NYC DOB Permits — Manhattan'),

    # Health Inspections — daily
    ('monitor_health_inspections', {'days': 7}, 24, 'NYC Health Inspections'),

    # Property Sales — daily
    ('monitor_property_sales_ny', {'days': 30}, 24, 'NYC Property Sales (ACRIS)'),

    # Business Filings — daily
    ('monitor_ny_business_filings', {'days': 7}, 24, 'NY Business Filings'),

    # Google Places — every 12 hours
    ('monitor_google_places', {'category': 'plumber', 'city': 'Queens, NY'}, 12, 'Google Places — Plumber Queens'),
    ('monitor_google_places', {'category': 'electrician', 'city': 'Queens, NY'}, 12, 'Google Places — Electrician Queens'),

    # California — general
    ('monitor_ca_contractors', {'days': 7}, 24, 'CA Contractor Licenses'),
    ('monitor_ca_violations', {'days': 7}, 24, 'CA OSHA Violations'),

    # California — LA Building Violations (weekly update, check daily)
    ('monitor_la_building_violations', {'days': 14}, 24, 'LA Building Violations'),

    # California — SF Building Violations (daily update)
    ('monitor_sf_building_violations', {'days': 7}, 24, 'SF Building Violations'),

    # California — SF Building Permits (nightly update)
    ('monitor_sf_permits', {'days': 7}, 24, 'SF Building Permits'),

    # ── WESTERN STATES — Health Inspections (late-night calling) ──

    # Las Vegas / Clark County NV — nightly CSV update
    ('monitor_vegas_health', {'days': 7}, 24, 'Las Vegas Health Inspections (SNHD)'),

    # Phoenix / Maricopa County AZ — weekly reports
    ('monitor_maricopa_health', {'days': 7}, 24, 'Phoenix/Maricopa Health Inspections'),

    # Tucson / Pima County AZ — current portal
    ('monitor_pima_health', {'days': 7}, 24, 'Tucson/Pima Health Inspections'),

    # California — County Health Inspections
    ('monitor_ca_health', {'county': 'sacramento', 'days': 7}, 24, 'Sacramento Health Inspections (daily)'),
    ('monitor_ca_health', {'county': 'san_diego', 'days': 7}, 24, 'San Diego Health Inspections'),
    ('monitor_ca_health', {'county': 'santa_clara', 'days': 7}, 24, 'Santa Clara Health Inspections'),
    ('monitor_ca_health', {'county': 'la', 'days': 120}, 168, 'LA County Health Inspections (weekly — quarterly data)'),

    # myhealthdepartment.com jurisdictions — daily
    ('monitor_myhealthdept', {'jurisdiction': 'denver', 'days': 7}, 24, 'Denver Health Inspections'),
    ('monitor_myhealthdept', {'jurisdiction': 'portland', 'days': 14}, 24, 'Portland Health Inspections'),
    ('monitor_myhealthdept', {'jurisdiction': 'colorado_springs', 'days': 7}, 24, 'Colorado Springs Health Inspections'),
    ('monitor_myhealthdept', {'jurisdiction': 'honolulu', 'days': 7}, 24, 'Honolulu Health Inspections'),
]
