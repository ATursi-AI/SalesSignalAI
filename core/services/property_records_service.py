"""
Property records lookup — authoritative current-owner data from public registries.

Primary purpose: given a lead (typically a DOB permit / building violation),
find the *legal owner* of the parcel plus their *mailing address*, which is
often different from the property address. That mailing address is what Gemini
or white-pages searches should be targeting — not the renovation site.

Supported registries:
  - NYC ACRIS (free SODA API, all 5 boroughs)

Future extensions:
  - Nassau County Clerk land records (scrape required)
  - Suffolk County Clerk (SCRS) land records (scrape required)
  - LA County Assessor parcel roll

Usage:
    from core.services.property_records_service import lookup_current_owner

    result = lookup_current_owner(
        borough='Brooklyn', block='3954', lot='35',   # NYC path
        # OR
        address='308 Bradford St', city='Brooklyn', state='NY',
    )
    # => {
    #     'owner_name': 'PERROTTA, GREGORY',
    #     'owner_mailing_address': '123 OCEAN AVE',
    #     'owner_mailing_city': 'BROOKLYN',
    #     'owner_mailing_state': 'NY',
    #     'owner_mailing_zip': '11230',
    #     'deed_date': '2019-06-15',
    #     'sale_price': 650000,
    #     'doc_type': 'DEED',
    #     'source': 'nyc_acris',
    #     'bbl': '3039540035',
    # }
"""
import hashlib
import logging

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Deeds rarely change — cache lookups for 90 days
CACHE_TTL_FOUND = 60 * 60 * 24 * 90
CACHE_TTL_NOT_FOUND = 60 * 60 * 24 * 14

ACRIS_MASTER = 'https://data.cityofnewyork.us/resource/bnx9-e6tj.json'
ACRIS_PARTIES = 'https://data.cityofnewyork.us/resource/636b-3b5g.json'
ACRIS_LEGALS = 'https://data.cityofnewyork.us/resource/8h5j-fqxa.json'

# Borough name -> ACRIS single-digit code
BOROUGH_TO_ACRIS = {
    'manhattan': '1', 'bronx': '2', 'brooklyn': '3',
    'queens': '4', 'staten island': '5', 'staten_island': '5',
    '1': '1', '2': '2', '3': '3', '4': '4', '5': '5',
    'MANHATTAN': '1', 'BRONX': '2', 'BROOKLYN': '3',
    'QUEENS': '4', 'STATEN ISLAND': '5',
}

NYC_CITIES = {
    'new york', 'nyc', 'manhattan', 'bronx', 'brooklyn',
    'queens', 'staten island', 'staten_island',
}

# Deed-like document types in ACRIS Master
DEED_DOC_TYPES = {'DEED', 'DEEDO', 'DEED, QUITCLAIM', 'DEED, BARGAIN AND SALE'}


def _cache_key(*parts):
    raw = '|'.join(str(p).lower().strip() for p in parts)
    return f'property_records:{hashlib.md5(raw.encode()).hexdigest()}'


def _is_nyc(city, state):
    if state and state.upper() != 'NY':
        return False
    return (city or '').strip().lower() in NYC_CITIES


def _acris_borough_digit(borough):
    if not borough:
        return None
    return BOROUGH_TO_ACRIS.get(str(borough).strip().lower()) \
        or BOROUGH_TO_ACRIS.get(str(borough).strip().upper())


def _bbl_10digit(borough, block, lot):
    """Format a 10-digit BBL: B + BBBBB + LLLL."""
    bdigit = _acris_borough_digit(borough)
    if not bdigit or not block or not lot:
        return None
    try:
        return f'{bdigit}{int(block):05d}{int(lot):04d}'
    except (ValueError, TypeError):
        return None


def lookup_current_owner(bbl=None, borough=None, block=None, lot=None,
                         address=None, city=None, state='NY'):
    """
    Look up the current legal owner of a parcel from public deed records.

    Preferred inputs (most to least authoritative):
      1. borough + block + lot   -> direct ACRIS lookup
      2. bbl (10-digit)          -> direct ACRIS lookup
      3. address + city/borough  -> ACRIS address lookup (less reliable, picks
                                    first matching doc)

    Returns dict or None if no deed found.
    """
    # NYC path
    if _is_nyc(city, state) or borough:
        # Build BBL from parts if we have them
        resolved_bbl = bbl
        if not resolved_bbl and borough and block and lot:
            resolved_bbl = _bbl_10digit(borough, block, lot)

        if resolved_bbl:
            ck = _cache_key('acris', 'bbl', resolved_bbl)
            cached = cache.get(ck)
            if cached is not None:
                return cached if cached.get('_found') else None
            result = _acris_lookup_by_bbl(resolved_bbl)
            cache.set(
                ck,
                result or {'_found': False},
                CACHE_TTL_FOUND if result else CACHE_TTL_NOT_FOUND,
            )
            return result

        if address and borough:
            ck = _cache_key('acris', 'addr', address, borough)
            cached = cache.get(ck)
            if cached is not None:
                return cached if cached.get('_found') else None
            result = _acris_lookup_by_address(address, borough)
            cache.set(
                ck,
                result or {'_found': False},
                CACHE_TTL_FOUND if result else CACHE_TTL_NOT_FOUND,
            )
            return result

    # Non-NYC: not yet implemented
    logger.debug(
        '[property_records] No registry for city=%s state=%s', city, state
    )
    return None


def _acris_lookup_by_bbl(bbl):
    """
    Look up most recent deed for a BBL.

    Flow:
      1. Legals: filter rows where borough/block/lot match the BBL
      2. Master: among those doc_ids, find latest DEED by recorded_datetime
      3. Parties: get grantee (party_type='2') for that doc_id
    """
    bdigit, block, lot = bbl[0], int(bbl[1:6]), int(bbl[6:10])
    try:
        # Step 1: find all recorded docs touching this parcel
        legals = _soda_get(ACRIS_LEGALS, {
            '$where': f"borough='{bdigit}' AND block='{block}' AND lot='{lot}'",
            '$select': 'document_id,street_number,street_name',
            '$limit': 200,
        })
        if not legals:
            return None

        doc_ids = list({row['document_id'] for row in legals if row.get('document_id')})
        if not doc_ids:
            return None

        # Step 2: pull Master rows for those docs, filter to deed types, sort desc
        # SODA IN-clause needs quoted values
        in_clause = ','.join(f"'{d}'" for d in doc_ids[:50])
        masters = _soda_get(ACRIS_MASTER, {
            '$where': f"document_id IN ({in_clause})",
            '$select': 'document_id,doc_type,document_date,document_amt,recorded_datetime',
            '$order': 'recorded_datetime DESC',
            '$limit': 50,
        })
        if not masters:
            return None

        latest_deed = None
        for m in masters:
            if m.get('doc_type', '').upper() in DEED_DOC_TYPES:
                latest_deed = m
                break
        if not latest_deed:
            return None

        deed_doc_id = latest_deed['document_id']

        # Step 3: pull grantee party rows (party_type='2')
        parties = _soda_get(ACRIS_PARTIES, {
            '$where': f"document_id='{deed_doc_id}' AND party_type='2'",
            '$limit': 10,
        })
        if not parties:
            return None

        # Combine multiple grantees into a single name string
        grantee = parties[0]
        names = [p.get('name', '').strip() for p in parties if p.get('name')]
        owner_name = ' & '.join(names) if len(names) > 1 else (names[0] if names else '')

        try:
            sale_price = float(latest_deed.get('document_amt') or 0) or None
        except (ValueError, TypeError):
            sale_price = None

        return {
            '_found': True,
            'owner_name': owner_name,
            'owner_mailing_address': grantee.get('address_1', '').strip(),
            'owner_mailing_address_2': grantee.get('address_2', '').strip(),
            'owner_mailing_city': grantee.get('city', '').strip(),
            'owner_mailing_state': grantee.get('state', '').strip(),
            'owner_mailing_zip': grantee.get('zip', '').strip(),
            'all_grantees': names,
            'deed_date': latest_deed.get('document_date', '')[:10],
            'recorded_date': latest_deed.get('recorded_datetime', '')[:10],
            'sale_price': sale_price,
            'doc_type': latest_deed.get('doc_type', ''),
            'doc_id': deed_doc_id,
            'source': 'nyc_acris',
            'bbl': bbl,
        }

    except Exception as e:
        logger.warning('[property_records] ACRIS BBL lookup failed for %s: %s', bbl, e)
        return None


def _acris_lookup_by_address(address, borough):
    """
    Fallback: look up by street number + street name + borough.
    Less reliable than BBL — picks first matching deed.
    """
    bdigit = _acris_borough_digit(borough)
    if not bdigit or not address:
        return None

    # Crude split: first token is house number, rest is street
    parts = address.strip().split(None, 1)
    if len(parts) != 2:
        return None
    house_num, street = parts[0], parts[1].upper()

    try:
        legals = _soda_get(ACRIS_LEGALS, {
            '$where': (
                f"borough='{bdigit}' AND street_number='{house_num}' "
                f"AND upper(street_name) LIKE '%{street}%'"
            ),
            '$select': 'document_id,block,lot',
            '$limit': 50,
        })
        if not legals:
            return None
        # Use the first parcel's BBL for a clean lookup
        first = legals[0]
        bbl = _bbl_10digit(borough, first.get('block'), first.get('lot'))
        if not bbl:
            return None
        return _acris_lookup_by_bbl(bbl)

    except Exception as e:
        logger.warning(
            '[property_records] ACRIS address lookup failed for %s %s: %s',
            address, borough, e,
        )
        return None


def _soda_get(url, params, timeout=15):
    """Wrap SODA API GET with basic error handling."""
    from django.conf import settings
    headers = {}
    app_token = getattr(settings, 'NYC_SODA_APP_TOKEN', '')
    if app_token:
        headers['X-App-Token'] = app_token
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        logger.warning('[property_records] SODA %s -> %s: %s',
                       url, resp.status_code, resp.text[:200])
        return []
    return resp.json()


def attach_property_records_to_lead(lead):
    """
    Resolve current owner for a lead and return the lookup result.

    Also updates lead.raw_data['deed'] and, if the lead's contact_name appears
    to be a generic/company placeholder or is missing, promotes the deed owner
    name into contact_name. Caller is responsible for lead.save().

    Returns the deed dict (with '_found': True) or None.
    """
    raw = lead.raw_data if isinstance(lead.raw_data, dict) else {}

    # Prefer BBL components from raw_data (DOB permits/violations have these)
    borough = raw.get('borough') or lead.region
    block = raw.get('block')
    lot = raw.get('lot')
    address = lead.contact_address or ''
    if not address:
        house = raw.get('house_number', '')
        street = raw.get('street_name', '')
        if house and street:
            address = f'{house} {street}'.strip()

    result = lookup_current_owner(
        borough=borough, block=block, lot=lot,
        address=address, city=lead.region, state=lead.state or 'NY',
    )
    if not result:
        return None

    # Stash the full deed data
    raw['deed'] = result
    lead.raw_data = raw
    return result
