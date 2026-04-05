"""
Lead Value Extractor — pulls real dollar amounts from lead raw_data.

Only uses actual data from the lead. Never estimates or guesses.
If no dollar value is found, returns None.

Used to flag high-value leads ($5K+) for immediate sales team review.
"""
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Known dollar value fields by data source / monitor.
# Each entry: (raw_data key, description)
# ─────────────────────────────────────────────────────────────
DOLLAR_FIELDS = [
    # Building permits — project cost
    ('estimated_cost', 'Estimated project cost'),           # Seattle building, Seattle trade, Seattle electrical, SF permits
    ('est_project_cost', 'Estimated project cost'),         # Seattle trade/electrical (alternate key)
    ('job_valuation', 'Total job valuation'),               # Austin construction permits
    ('valuation', 'Permit valuation'),                      # LA building permits, LA certificate of occupancy
    ('declared_value', 'Declared permit value'),            # Montgomery County permits
    ('estimated_cost_of_construction', 'Construction cost'),# Some permit APIs use this

    # Violations — fine amounts
    ('total_fine', 'Total fines imposed'),                  # Chicago ordinance violations
    ('total_penalty', 'Total penalty amount'),              # NYC ECB summonses, NYC HPD violations
    ('admin_costs', 'Administrative costs'),                # Chicago ordinance violations (additive)
    ('current_penalty', 'Current penalty'),                 # CA/OSHA violations
    ('penalty', 'Penalty amount'),                          # CA/OSHA violations (alternate key)
    ('penalty_imposed', 'Penalty imposed'),                 # Some violation APIs
    ('fine_amount', 'Fine amount'),                         # Generic violation field
]


def _parse_dollar(value):
    """
    Parse a dollar value from various formats.
    Returns float or None if unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None

    # String parsing
    s = str(value).strip()
    if not s or s in ('0', '0.0', '0.00', '$0', '$0.00', 'N/A', 'n/a', ''):
        return None

    # Remove currency symbols and commas
    s = s.replace('$', '').replace(',', '').strip()

    try:
        val = float(s)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def extract_lead_value(lead):
    """
    Extract the real dollar value from a lead's raw_data.

    Returns:
        float or None — the dollar amount if found, None if no value data exists.
    """
    raw = lead.raw_data
    if not raw or not isinstance(raw, dict):
        return None

    total = 0.0
    found_any = False

    for field_key, _ in DOLLAR_FIELDS:
        if field_key in raw:
            val = _parse_dollar(raw[field_key])
            if val is not None:
                total += val
                found_any = True

    return total if found_any else None


def is_high_value(lead, threshold=5000):
    """
    Check if a lead has a dollar value >= threshold.
    Returns (is_high, dollar_amount) tuple.
    """
    value = extract_lead_value(lead)
    if value is None:
        return False, None
    return value >= threshold, value


def flag_high_value_leads(queryset=None, threshold=5000, limit=500):
    """
    Scan leads and return those with dollar values >= threshold.
    Only looks at leads that have raw_data.

    Returns list of (lead, dollar_value) tuples, sorted highest value first.
    """
    from core.models.leads import Lead

    if queryset is None:
        queryset = Lead.objects.filter(
            platform='public_records',
            raw_data__isnull=False,
        ).exclude(raw_data={}).order_by('-discovered_at')[:limit]

    high_value = []
    for lead in queryset:
        is_hv, value = is_high_value(lead, threshold=threshold)
        if is_hv:
            high_value.append((lead, value))

    # Sort by dollar value, highest first
    high_value.sort(key=lambda x: x[1], reverse=True)
    return high_value
