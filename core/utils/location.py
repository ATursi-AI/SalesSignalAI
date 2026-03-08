"""
Location extraction utility for SalesSignal AI.
Detects city/town names, zip codes, and state references from free-text posts.
Focused on NY/NJ/CT tri-state area for initial beta.
"""
import re

# NY/NJ/CT zip code ranges (first 3 digits)
TRISTATE_ZIP_PREFIXES = {
    # New York
    '100', '101', '102', '103', '104', '105', '106', '107', '108', '109',
    '110', '111', '112', '113', '114', '115', '116', '117', '118', '119',
    '120', '121', '122', '123', '124', '125', '126', '127', '128', '129',
    '130', '131', '132', '133', '134', '135', '136', '137', '138', '139',
    '140', '141', '142', '143', '144', '145', '146', '147', '148', '149',
    # New Jersey
    '070', '071', '072', '073', '074', '075', '076', '077', '078', '079',
    '080', '081', '082', '083', '084', '085', '086', '087', '088', '089',
    # Connecticut
    '060', '061', '062', '063', '064', '065', '066', '067', '068', '069',
}

# Comprehensive town/city list for Long Island, NYC, NJ, CT, Westchester, Hudson Valley
# Format: 'lowercased name': ('Display Name', 'State', 'ZIP')
KNOWN_LOCATIONS = {
    # Nassau County, Long Island
    'garden city': ('Garden City', 'NY', '11530'),
    'mineola': ('Mineola', 'NY', '11501'),
    'westbury': ('Westbury', 'NY', '11590'),
    'new cassel': ('New Cassel', 'NY', '11590'),
    'hicksville': ('Hicksville', 'NY', '11801'),
    'levittown': ('Levittown', 'NY', '11756'),
    'massapequa': ('Massapequa', 'NY', '11758'),
    'massapequa park': ('Massapequa Park', 'NY', '11762'),
    'plainview': ('Plainview', 'NY', '11803'),
    'east meadow': ('East Meadow', 'NY', '11554'),
    'freeport': ('Freeport', 'NY', '11520'),
    'merrick': ('Merrick', 'NY', '11566'),
    'bellmore': ('Bellmore', 'NY', '11710'),
    'wantagh': ('Wantagh', 'NY', '11793'),
    'seaford': ('Seaford', 'NY', '11783'),
    'baldwin': ('Baldwin', 'NY', '11510'),
    'oceanside': ('Oceanside', 'NY', '11572'),
    'lynbrook': ('Lynbrook', 'NY', '11563'),
    'rockville centre': ('Rockville Centre', 'NY', '11570'),
    'valley stream': ('Valley Stream', 'NY', '11580'),
    'hempstead': ('Hempstead', 'NY', '11550'),
    'west hempstead': ('West Hempstead', 'NY', '11552'),
    'franklin square': ('Franklin Square', 'NY', '11010'),
    'floral park': ('Floral Park', 'NY', '11001'),
    'new hyde park': ('New Hyde Park', 'NY', '11040'),
    'great neck': ('Great Neck', 'NY', '11021'),
    'manhasset': ('Manhasset', 'NY', '11030'),
    'port washington': ('Port Washington', 'NY', '11050'),
    'roslyn': ('Roslyn', 'NY', '11576'),
    'glen cove': ('Glen Cove', 'NY', '11542'),
    'oyster bay': ('Oyster Bay', 'NY', '11771'),
    'syosset': ('Syosset', 'NY', '11791'),
    'jericho': ('Jericho', 'NY', '11753'),
    'woodbury': ('Woodbury', 'NY', '11797'),
    'bethpage': ('Bethpage', 'NY', '11714'),
    'farmingdale': ('Farmingdale', 'NY', '11735'),
    'long beach': ('Long Beach', 'NY', '11561'),
    'island park': ('Island Park', 'NY', '11558'),
    'elmont': ('Elmont', 'NY', '11003'),
    'carle place': ('Carle Place', 'NY', '11514'),
    'williston park': ('Williston Park', 'NY', '11596'),
    'albertson': ('Albertson', 'NY', '11507'),
    'east rockaway': ('East Rockaway', 'NY', '11518'),
    # Suffolk County, Long Island
    'huntington': ('Huntington', 'NY', '11743'),
    'babylon': ('Babylon', 'NY', '11702'),
    'islip': ('Islip', 'NY', '11751'),
    'smithtown': ('Smithtown', 'NY', '11787'),
    'brookhaven': ('Brookhaven', 'NY', '11719'),
    'riverhead': ('Riverhead', 'NY', '11901'),
    'southampton': ('Southampton', 'NY', '11968'),
    'east hampton': ('East Hampton', 'NY', '11937'),
    'commack': ('Commack', 'NY', '11725'),
    'dix hills': ('Dix Hills', 'NY', '11746'),
    'deer park': ('Deer Park', 'NY', '11729'),
    'lindenhurst': ('Lindenhurst', 'NY', '11757'),
    'west islip': ('West Islip', 'NY', '11795'),
    'bay shore': ('Bay Shore', 'NY', '11706'),
    'brentwood': ('Brentwood', 'NY', '11717'),
    'central islip': ('Central Islip', 'NY', '11722'),
    'patchogue': ('Patchogue', 'NY', '11772'),
    'sayville': ('Sayville', 'NY', '11782'),
    'coram': ('Coram', 'NY', '11727'),
    'port jefferson': ('Port Jefferson', 'NY', '11777'),
    'stony brook': ('Stony Brook', 'NY', '11790'),
    'lake ronkonkoma': ('Lake Ronkonkoma', 'NY', '11779'),
    'hauppauge': ('Hauppauge', 'NY', '11788'),
    'bohemia': ('Bohemia', 'NY', '11716'),
    'centereach': ('Centereach', 'NY', '11720'),
    'selden': ('Selden', 'NY', '11784'),
    'medford': ('Medford', 'NY', '11763'),
    'east northport': ('East Northport', 'NY', '11731'),
    'northport': ('Northport', 'NY', '11768'),
    'cold spring harbor': ('Cold Spring Harbor', 'NY', '11724'),
    # NYC
    'manhattan': ('Manhattan', 'NY', '10001'),
    'brooklyn': ('Brooklyn', 'NY', '11201'),
    'queens': ('Queens', 'NY', '11101'),
    'bronx': ('Bronx', 'NY', '10451'),
    'the bronx': ('Bronx', 'NY', '10451'),
    'staten island': ('Staten Island', 'NY', '10301'),
    'astoria': ('Astoria', 'NY', '11102'),
    'flushing': ('Flushing', 'NY', '11354'),
    'jamaica': ('Jamaica', 'NY', '11432'),
    'bayside': ('Bayside', 'NY', '11361'),
    'forest hills': ('Forest Hills', 'NY', '11375'),
    'jackson heights': ('Jackson Heights', 'NY', '11372'),
    'woodside': ('Woodside', 'NY', '11377'),
    'ridgewood': ('Ridgewood', 'NY', '11385'),
    'williamsburg': ('Williamsburg', 'NY', '11211'),
    'park slope': ('Park Slope', 'NY', '11215'),
    'bay ridge': ('Bay Ridge', 'NY', '11209'),
    'bensonhurst': ('Bensonhurst', 'NY', '11214'),
    'flatbush': ('Flatbush', 'NY', '11226'),
    'harlem': ('Harlem', 'NY', '10027'),
    'upper east side': ('Upper East Side', 'NY', '10021'),
    'upper west side': ('Upper West Side', 'NY', '10024'),
    'greenwich village': ('Greenwich Village', 'NY', '10014'),
    'soho': ('SoHo', 'NY', '10012'),
    'tribeca': ('Tribeca', 'NY', '10013'),
    'chelsea': ('Chelsea', 'NY', '10011'),
    # Westchester
    'white plains': ('White Plains', 'NY', '10601'),
    'yonkers': ('Yonkers', 'NY', '10701'),
    'new rochelle': ('New Rochelle', 'NY', '10801'),
    'mount vernon': ('Mount Vernon', 'NY', '10550'),
    'scarsdale': ('Scarsdale', 'NY', '10583'),
    'tarrytown': ('Tarrytown', 'NY', '10591'),
    'mamaroneck': ('Mamaroneck', 'NY', '10543'),
    'rye': ('Rye', 'NY', '10580'),
    'larchmont': ('Larchmont', 'NY', '10538'),
    'pelham': ('Pelham', 'NY', '10803'),
    'bronxville': ('Bronxville', 'NY', '10708'),
    'tuckahoe': ('Tuckahoe', 'NY', '10707'),
    'eastchester': ('Eastchester', 'NY', '10709'),
    'dobbs ferry': ('Dobbs Ferry', 'NY', '10522'),
    'hastings on hudson': ('Hastings-on-Hudson', 'NY', '10706'),
    'ossining': ('Ossining', 'NY', '10562'),
    'peekskill': ('Peekskill', 'NY', '10566'),
    # Rockland County
    'nanuet': ('Nanuet', 'NY', '10954'),
    'new city': ('New City', 'NY', '10956'),
    'spring valley': ('Spring Valley', 'NY', '10977'),
    'suffern': ('Suffern', 'NY', '10901'),
    'nyack': ('Nyack', 'NY', '10960'),
    'pearl river': ('Pearl River', 'NY', '10965'),
    # Hudson Valley
    'newburgh': ('Newburgh', 'NY', '12550'),
    'poughkeepsie': ('Poughkeepsie', 'NY', '12601'),
    'middletown': ('Middletown', 'NY', '10940'),
    'kingston': ('Kingston', 'NY', '12401'),
    'beacon': ('Beacon', 'NY', '12508'),
    # New Jersey (common towns)
    'jersey city': ('Jersey City', 'NJ', '07302'),
    'hoboken': ('Hoboken', 'NJ', '07030'),
    'newark': ('Newark', 'NJ', '07102'),
    'elizabeth': ('Elizabeth', 'NJ', '07201'),
    'paterson': ('Paterson', 'NJ', '07501'),
    'clifton': ('Clifton', 'NJ', '07011'),
    'passaic': ('Passaic', 'NJ', '07055'),
    'hackensack': ('Hackensack', 'NJ', '07601'),
    'paramus': ('Paramus', 'NJ', '07652'),
    'teaneck': ('Teaneck', 'NJ', '07666'),
    'fort lee': ('Fort Lee', 'NJ', '07024'),
    'englewood': ('Englewood', 'NJ', '07631'),
    'ridgewood nj': ('Ridgewood', 'NJ', '07450'),
    'montclair': ('Montclair', 'NJ', '07042'),
    'bloomfield': ('Bloomfield', 'NJ', '07003'),
    'nutley': ('Nutley', 'NJ', '07110'),
    'bayonne': ('Bayonne', 'NJ', '07002'),
    'union city': ('Union City', 'NJ', '07087'),
    'west new york': ('West New York', 'NJ', '07093'),
    'north bergen': ('North Bergen', 'NJ', '07047'),
    'secaucus': ('Secaucus', 'NJ', '07094'),
    'morristown': ('Morristown', 'NJ', '07960'),
    'edison': ('Edison', 'NJ', '08817'),
    'woodbridge': ('Woodbridge', 'NJ', '07095'),
    'new brunswick': ('New Brunswick', 'NJ', '08901'),
    'princeton': ('Princeton', 'NJ', '08540'),
    'trenton': ('Trenton', 'NJ', '08608'),
    'cherry hill': ('Cherry Hill', 'NJ', '08002'),
    'toms river': ('Toms River', 'NJ', '08753'),
    # Connecticut
    'stamford': ('Stamford', 'CT', '06901'),
    'bridgeport': ('Bridgeport', 'CT', '06601'),
    'new haven': ('New Haven', 'CT', '06510'),
    'hartford': ('Hartford', 'CT', '06101'),
    'waterbury': ('Waterbury', 'CT', '06701'),
    'norwalk': ('Norwalk', 'CT', '06850'),
    'danbury': ('Danbury', 'CT', '06810'),
    'greenwich': ('Greenwich', 'CT', '06830'),
    'fairfield': ('Fairfield', 'CT', '06824'),
    'westport': ('Westport', 'CT', '06880'),
    'darien': ('Darien', 'CT', '06820'),
    'new canaan': ('New Canaan', 'CT', '06840'),
    'milford': ('Milford', 'CT', '06460'),
    'shelton': ('Shelton', 'CT', '06484'),
    'trumbull': ('Trumbull', 'CT', '06611'),
    'stratford': ('Stratford', 'CT', '06614'),
    # Regional references
    'long island': ('Long Island', 'NY', ''),
    'nassau county': ('Nassau County', 'NY', ''),
    'suffolk county': ('Suffolk County', 'NY', ''),
    'westchester county': ('Westchester County', 'NY', ''),
    'rockland county': ('Rockland County', 'NY', ''),
    'bergen county': ('Bergen County', 'NJ', ''),
    'hudson county': ('Hudson County', 'NJ', ''),
    'essex county nj': ('Essex County', 'NJ', ''),
    'fairfield county': ('Fairfield County', 'CT', ''),
}

# State abbreviation patterns
STATE_PATTERNS = {
    'new york': 'NY',
    'new jersey': 'NJ',
    'connecticut': 'CT',
    'ny': 'NY',
    'nj': 'NJ',
    'ct': 'CT',
}


def extract_zip_codes(text):
    """Extract 5-digit zip codes from text."""
    return re.findall(r'\b(\d{5})\b', text)


def extract_location(text):
    """
    Extract location info from free text.
    Returns dict with: city, state, zip_code, display (formatted string)
    """
    if not text:
        return {'city': '', 'state': '', 'zip_code': '', 'display': ''}

    text_lower = text.lower()

    # 1. Try to find a zip code first
    zips = extract_zip_codes(text)
    detected_zip = ''
    for z in zips:
        if z[:3] in TRISTATE_ZIP_PREFIXES:
            detected_zip = z
            break

    # 2. Try to match known locations (longest match first)
    best_match = None
    best_len = 0
    for loc_key, loc_data in KNOWN_LOCATIONS.items():
        # Use word boundary matching to avoid false positives
        pattern = r'\b' + re.escape(loc_key) + r'\b'
        if re.search(pattern, text_lower):
            if len(loc_key) > best_len:
                best_match = loc_data
                best_len = len(loc_key)

    if best_match:
        city, state, zip_code = best_match
        if not detected_zip and zip_code:
            detected_zip = zip_code
        display = f"{city}, {state}" if city else ''
        if detected_zip:
            display += f" {detected_zip}" if display else detected_zip
        return {
            'city': city,
            'state': state,
            'zip_code': detected_zip,
            'display': display.strip(),
        }

    # 3. Try to detect state references with nearby words as city
    for state_name, state_abbr in STATE_PATTERNS.items():
        pattern = r'(?:in|near|around|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),?\s*' + re.escape(state_name)
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            city = match.group(1).strip()
            display = f"{city}, {state_abbr}"
            return {
                'city': city,
                'state': state_abbr,
                'zip_code': detected_zip,
                'display': display,
            }

    # 4. If we only found a zip, return that
    if detected_zip:
        # Try to look up zip in our known locations
        for loc_key, loc_data in KNOWN_LOCATIONS.items():
            if loc_data[2] == detected_zip:
                return {
                    'city': loc_data[0],
                    'state': loc_data[1],
                    'zip_code': detected_zip,
                    'display': f"{loc_data[0]}, {loc_data[1]} {detected_zip}",
                }
        return {
            'city': '',
            'state': '',
            'zip_code': detected_zip,
            'display': detected_zip,
        }

    return {'city': '', 'state': '', 'zip_code': '', 'display': ''}


def is_in_service_area(lead_location, business_profile):
    """
    Check if a detected location falls within a business's service area.
    Uses zip code matching and city/state proximity.
    For now, uses a simple string-matching approach.
    Full geo-distance calculation would require lat/lng.
    """
    if not lead_location.get('display'):
        # No location detected — include it (better to show than miss)
        return True

    bp = business_profile

    # Check explicit zip code list
    if bp.service_zip_codes and lead_location.get('zip_code'):
        if lead_location['zip_code'] in bp.service_zip_codes:
            return True

    # Check state match
    if lead_location.get('state') and bp.state:
        if lead_location['state'] != bp.state:
            # Different state — but check if the business covers nearby states
            # For tri-state area, NY/NJ/CT businesses often cover each other
            tristate = {'NY', 'NJ', 'CT'}
            if lead_location['state'] in tristate and bp.state in tristate:
                # Allow cross-state for now, radius will filter further
                pass
            else:
                return False

    # Check if lead is in a regional area the business covers
    lead_display = lead_location.get('display', '').lower()
    business_area = f"{bp.city} {bp.state} {bp.zip_code}".lower()

    # Same city
    if bp.city and bp.city.lower() in lead_display:
        return True

    # Same county / regional reference
    county_refs = ['nassau county', 'suffolk county', 'long island',
                   'westchester', 'bergen county', 'hudson county',
                   'fairfield county']
    for ref in county_refs:
        if ref in lead_display and ref in business_area:
            return True

    # For now, accept all leads in the same state
    if lead_location.get('state') == bp.state:
        return True

    # Accept tri-state leads within reasonable proximity
    tristate = {'NY', 'NJ', 'CT'}
    if lead_location.get('state') in tristate and bp.state in tristate:
        return True

    return False
