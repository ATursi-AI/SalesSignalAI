"""
Portal registry — maps portal domains to their adapter type and state.
"""

PORTAL_REGISTRY = {
    # ── Socrata portals ──
    # New York
    'data.ny.gov': {'adapter': 'socrata', 'state': 'NY', 'city': ''},
    'data.cityofnewyork.us': {'adapter': 'socrata', 'state': 'NY', 'city': 'New York City'},

    # California
    'data.ca.gov': {'adapter': 'socrata', 'state': 'CA', 'city': ''},
    'data.sfgov.org': {'adapter': 'socrata', 'state': 'CA', 'city': 'San Francisco'},
    'data.lacity.org': {'adapter': 'socrata', 'state': 'CA', 'city': 'Los Angeles'},
    'data.sccgov.org': {'adapter': 'socrata', 'state': 'CA', 'city': 'Santa Clara County'},

    # Texas
    'data.texas.gov': {'adapter': 'socrata', 'state': 'TX', 'city': ''},
    'data.austintexas.gov': {'adapter': 'socrata', 'state': 'TX', 'city': 'Austin'},

    # Illinois
    'data.illinois.gov': {'adapter': 'socrata', 'state': 'IL', 'city': ''},
    'data.cityofchicago.org': {'adapter': 'socrata', 'state': 'IL', 'city': 'Chicago'},

    # Florida
    'data.florida.gov': {'adapter': 'socrata', 'state': 'FL', 'city': ''},

    # Other Socrata states
    'data.pa.gov': {'adapter': 'socrata', 'state': 'PA', 'city': ''},
    'data.ohio.gov': {'adapter': 'socrata', 'state': 'OH', 'city': ''},
    'data.georgia.gov': {'adapter': 'socrata', 'state': 'GA', 'city': ''},
    'data.nc.gov': {'adapter': 'socrata', 'state': 'NC', 'city': ''},
    'data.michigan.gov': {'adapter': 'socrata', 'state': 'MI', 'city': ''},
    'data.wa.gov': {'adapter': 'socrata', 'state': 'WA', 'city': ''},
    'data.colorado.gov': {'adapter': 'socrata', 'state': 'CO', 'city': ''},
    'data.az.gov': {'adapter': 'socrata', 'state': 'AZ', 'city': ''},
    'data.nv.gov': {'adapter': 'socrata', 'state': 'NV', 'city': ''},
    'data.maryland.gov': {'adapter': 'socrata', 'state': 'MD', 'city': ''},
    'data.ct.gov': {'adapter': 'socrata', 'state': 'CT', 'city': ''},

    # ── CKAN portals ──
    'catalog.data.gov': {'adapter': 'ckan', 'state': '', 'city': '', 'note': 'Federal — filter by state tag'},

    # ── ArcGIS portals (add as discovered) ──
    # 'gis.acgov.org': {'adapter': 'arcgis', 'state': 'CA', 'city': 'Alameda County'},
}


def get_portals_for_state(state):
    """Return list of (portal_url, info_dict) for a state."""
    results = []
    for portal, info in PORTAL_REGISTRY.items():
        if info['state'] == state or (not info['state'] and state):
            results.append((portal, info))
    return results


def get_adapter(adapter_name):
    """Return adapter class by name."""
    from .socrata import SocrataAdapter
    from .arcgis import ArcGISAdapter
    from .ckan import CKANAdapter

    adapters = {
        'socrata': SocrataAdapter,
        'arcgis': ArcGISAdapter,
        'ckan': CKANAdapter,
    }
    cls = adapters.get(adapter_name)
    if cls:
        return cls()
    raise ValueError(f"Unknown adapter: {adapter_name}")
