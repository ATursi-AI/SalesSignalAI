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

    # California
    ('monitor_ca_contractors', {'days': 7}, 24, 'CA Contractor Licenses'),
    ('monitor_ca_violations', {'days': 7}, 24, 'CA OSHA Violations'),
]
