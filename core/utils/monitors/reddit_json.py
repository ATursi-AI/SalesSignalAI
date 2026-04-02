"""
Reddit monitor for SalesSignal AI.
Uses Reddit's public JSON endpoints — no API key required.

Fetches /r/{subreddit}/new.json for each configured subreddit,
matches posts against service keywords, extracts location, and
creates Lead records via the standard process_lead() pipeline.

Supports NY, CA, TX, IL, WA, MD, CT local subreddits, trade-specific
national subs, and state-based geo filtering.
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Subreddit classification
# ─────────────────────────────────────────────────────────────
NY_LOCAL_SUBREDDITS = [
    'AskNYC', 'longisland', 'brooklyn', 'nyc', 'astoria', 'queens',
]

CA_LOCAL_SUBREDDITS = [
    # LA area
    'LosAngeles', 'AskLosAngeles', 'LAlist', 'Pasadena', 'LongBeach',
    'orangecounty', 'InlandEmpire',
    # SF / Bay Area
    'sanfrancisco', 'AskSF', 'bayarea', 'oakland', 'SanJose', 'berkeley',
    # San Diego
    'SanDiego', 'sandiego',
    # Sacramento
    'Sacramento',
]

TX_LOCAL_SUBREDDITS = [
    # Austin
    'Austin', 'austinjobs',
    # Dallas / Fort Worth
    'Dallas', 'FortWorth', 'askdfw', 'plano', 'frisco',
    # Houston
    'houston',
    # San Antonio
    'sanantonio',
    # General Texas
    'texas',
]

IL_LOCAL_SUBREDDITS = [
    'chicago', 'ChicagoSuburbs', 'chicagoapartments',
]

WA_LOCAL_SUBREDDITS = [
    'Seattle', 'seattlehomes', 'Tacoma', 'Bellevue', 'everett',
    'olympia', 'washington',
]

MD_LOCAL_SUBREDDITS = [
    'MontgomeryCountyMD', 'maryland', 'bethesda', 'rockville',
    'SilverSpring', 'FrederickMD', 'baltimore',
]

CT_LOCAL_SUBREDDITS = [
    'Connecticut', 'stamford', 'newhaven', 'Hartford',
    'Fairfield', 'Bridgeport',
]

NATIONAL_SUBREDDITS = [
    'HomeImprovement', 'personalfinance', 'firsttimehomebuyer',
    'insurance', 'RealEstate', 'Moving', 'legaladvice', 'smallbusiness',
]

TRADE_SUBREDDITS = [
    'HVAC', 'Plumbing', 'Electricians', 'Roofing', 'Carpentry',
    'fixit', 'HomeRepair', 'Appliances', 'Landlord', 'homeowners', 'Home',
]

# State -> subreddits map for easy lookup
STATE_SUBREDDITS = {
    'NY': NY_LOCAL_SUBREDDITS,
    'CA': CA_LOCAL_SUBREDDITS,
    'TX': TX_LOCAL_SUBREDDITS,
    'IL': IL_LOCAL_SUBREDDITS,
    'WA': WA_LOCAL_SUBREDDITS,
    'MD': MD_LOCAL_SUBREDDITS,
    'CT': CT_LOCAL_SUBREDDITS,
}

ALL_LOCAL_SUBREDDITS = (
    NY_LOCAL_SUBREDDITS + CA_LOCAL_SUBREDDITS + TX_LOCAL_SUBREDDITS +
    IL_LOCAL_SUBREDDITS + WA_LOCAL_SUBREDDITS + MD_LOCAL_SUBREDDITS +
    CT_LOCAL_SUBREDDITS
)

# Keep backward compat
LOCAL_SUBREDDITS = NY_LOCAL_SUBREDDITS
DEFAULT_SUBREDDITS = NY_LOCAL_SUBREDDITS + NATIONAL_SUBREDDITS

REDDIT_USER_AGENT = 'SalesSignalAI/1.0 (monitoring service)'
REQUEST_DELAY = 2.0

# ─────────────────────────────────────────────────────────────
# NYC geo terms
# ─────────────────────────────────────────────────────────────
_NYC_AREA_TERMS = [
    'nyc', 'new york city', 'new york', 'manhattan', 'brooklyn', 'queens',
    'bronx', 'staten island', 'harlem', 'astoria', 'flushing',
    'williamsburg', 'bushwick', 'greenpoint', 'bed-stuy', 'bed stuy',
    'crown heights', 'park slope', 'bay ridge', 'sunset park',
    'east village', 'west village', 'upper east side', 'upper west side',
    'lower east side', 'tribeca', 'soho', 'chelsea', 'hells kitchen',
    'inwood', 'washington heights', 'jackson heights', 'forest hills',
    'rego park', 'jamaica', 'bayside', 'woodside', 'sunnyside',
    'long island', 'nassau county', 'suffolk county', 'nassau', 'suffolk',
    'hempstead', 'babylon', 'islip', 'huntington', 'smithtown',
    'brookhaven', 'oyster bay', 'north hempstead', 'massapequa',
    'levittown', 'freeport', 'valley stream', 'garden city',
    'mineola', 'westbury', 'hicksville', 'syosset', 'jericho',
    'great neck', 'manhasset', 'port washington', 'roslyn',
    'rockville centre', 'lynbrook', 'malverne', 'merrick', 'bellmore',
    'wantagh', 'seaford', 'east meadow', 'uniondale', 'elmont',
    'floral park', 'new hyde park', 'franklin square', 'plainview',
    'farmingdale', 'bethpage', 'lindenhurst', 'copiague', 'amityville',
    'west islip', 'bay shore', 'brentwood', 'central islip',
    'commack', 'deer park', 'dix hills', 'hauppauge', 'lake ronkonkoma',
    'patchogue', 'medford', 'shirley', 'riverhead', 'southampton',
    'east hampton', 'montauk', 'shelter island', 'greenport',
    'westchester', 'yonkers', 'white plains', 'new rochelle',
    'mount vernon', 'scarsdale', 'tarrytown', 'dobbs ferry',
    'jersey city', 'hoboken', 'newark', 'weehawken', 'bayonne',
    'north bergen', 'union city', 'fort lee', 'edgewater',
    'tri-state', 'tristate',
]

_nyc_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _NYC_AREA_TERMS) + r')\b',
    re.IGNORECASE,
)


def _has_nyc_area_reference(text):
    return bool(_nyc_area_re.search(text))


# ─────────────────────────────────────────────────────────────
# California geo terms
# ─────────────────────────────────────────────────────────────
_CA_AREA_TERMS = [
    # LA
    'los angeles', 'hollywood', 'venice', 'santa monica', 'pasadena',
    'burbank', 'glendale', 'long beach', 'culver city', 'west hollywood',
    'beverly hills', 'westwood', 'brentwood', 'silver lake', 'echo park',
    'koreatown', 'downtown la', 'dtla', 'highland park', 'eagle rock',
    'los feliz', 'studio city', 'sherman oaks', 'encino', 'van nuys',
    'north hollywood', 'noho', 'woodland hills', 'calabasas', 'malibu',
    'torrance', 'redondo beach', 'hermosa beach', 'manhattan beach',
    'el segundo', 'inglewood', 'compton', 'carson', 'lakewood', 'downey',
    'whittier', 'pomona', 'ontario', 'rancho cucamonga', 'fontana',
    'san bernardino', 'riverside', 'corona', 'anaheim', 'irvine',
    'costa mesa', 'newport beach', 'huntington beach', 'fullerton',
    # SF / Bay Area
    'san francisco', 'mission district', 'castro', 'soma', 'marina',
    'nob hill', 'north beach', 'haight', 'richmond', 'sunset',
    'tenderloin', 'financial district', 'embarcadero',
    'oakland', 'berkeley', 'alameda', 'fremont', 'hayward', 'san leandro',
    'palo alto', 'menlo park', 'mountain view', 'sunnyvale', 'cupertino',
    'san jose', 'santa clara', 'milpitas', 'redwood city', 'san mateo',
    'daly city', 'south san francisco', 'burlingame', 'san rafael',
    'walnut creek', 'concord', 'pleasant hill', 'danville', 'livermore',
    # San Diego
    'san diego', 'la jolla', 'pacific beach', 'ocean beach',
    'gaslamp', 'north park', 'hillcrest', 'mission valley', 'del mar',
    'encinitas', 'carlsbad', 'oceanside', 'escondido', 'chula vista',
    # Sacramento
    'sacramento', 'elk grove', 'roseville', 'folsom', 'davis',
    # Counties / regions
    'la county', 'los angeles county', 'orange county',
    'san diego county', 'alameda county', 'santa clara county',
    'san mateo county', 'contra costa county', 'sacramento county',
    'riverside county', 'san bernardino county', 'ventura county',
    'california', 'socal', 'southern california', 'norcal',
    'northern california', 'bay area', 'inland empire',
]

_ca_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _CA_AREA_TERMS) + r')\b',
    re.IGNORECASE,
)


def _has_ca_area_reference(text):
    return bool(_ca_area_re.search(text))


# ─────────────────────────────────────────────────────────────
# Texas geo terms
# ─────────────────────────────────────────────────────────────
_TX_AREA_TERMS = [
    # Austin
    'austin', 'round rock', 'cedar park', 'pflugerville', 'georgetown',
    'leander', 'lakeway', 'bee cave', 'dripping springs', 'kyle',
    'buda', 'manor', 'bastrop', 'travis county', 'williamson county',
    # Dallas / Fort Worth
    'dallas', 'fort worth', 'arlington', 'plano', 'irving', 'garland',
    'frisco', 'mckinney', 'denton', 'richardson', 'mesquite', 'carrollton',
    'lewisville', 'allen', 'flower mound', 'rowlett', 'rockwall',
    'dallas county', 'tarrant county', 'collin county', 'denton county',
    'dfw', 'north texas',
    # Houston
    'houston', 'sugar land', 'katy', 'the woodlands', 'pearland',
    'pasadena', 'baytown', 'league city', 'missouri city', 'cypress',
    'spring', 'humble', 'conroe', 'tomball', 'harris county',
    'fort bend county', 'montgomery county tx',
    # San Antonio
    'san antonio', 'new braunfels', 'schertz', 'boerne', 'bexar county',
    # General
    'texas', 'tx',
]

_tx_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _TX_AREA_TERMS) + r')\b',
    re.IGNORECASE,
)


def _has_tx_area_reference(text):
    return bool(_tx_area_re.search(text))


# ─────────────────────────────────────────────────────────────
# Illinois geo terms
# ─────────────────────────────────────────────────────────────
_IL_AREA_TERMS = [
    'chicago', 'evanston', 'oak park', 'naperville', 'aurora', 'joliet',
    'schaumburg', 'skokie', 'des plaines', 'arlington heights',
    'palatine', 'mount prospect', 'waukegan', 'elgin', 'cicero',
    'berwyn', 'oak lawn', 'tinley park', 'orland park', 'bolingbrook',
    'wheaton', 'downers grove', 'lombard', 'elmhurst', 'glen ellyn',
    'cook county', 'dupage county', 'lake county il', 'will county',
    'kane county', 'chicagoland', 'illinois', 'il',
]

_il_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _IL_AREA_TERMS) + r')\b',
    re.IGNORECASE,
)


def _has_il_area_reference(text):
    return bool(_il_area_re.search(text))


# ─────────────────────────────────────────────────────────────
# Washington geo terms
# ─────────────────────────────────────────────────────────────
_WA_AREA_TERMS = [
    'seattle', 'bellevue', 'tacoma', 'everett', 'redmond', 'kirkland',
    'renton', 'kent', 'federal way', 'auburn', 'burien', 'tukwila',
    'shoreline', 'lynnwood', 'edmonds', 'bothell', 'woodinville',
    'issaquah', 'sammamish', 'mercer island', 'bainbridge island',
    'olympia', 'lacey', 'tumwater', 'spokane', 'vancouver wa',
    'king county', 'pierce county', 'snohomish county', 'thurston county',
    'puget sound', 'pacific northwest', 'pnw', 'washington state',
]

_wa_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _WA_AREA_TERMS) + r')\b',
    re.IGNORECASE,
)


def _has_wa_area_reference(text):
    return bool(_wa_area_re.search(text))


# ─────────────────────────────────────────────────────────────
# Maryland geo terms
# ─────────────────────────────────────────────────────────────
_MD_AREA_TERMS = [
    'montgomery county', 'bethesda', 'silver spring', 'rockville',
    'germantown', 'gaithersburg', 'chevy chase', 'potomac', 'olney',
    'wheaton', 'takoma park', 'kensington', 'columbia', 'ellicott city',
    'laurel', 'bowie', 'annapolis', 'frederick', 'hagerstown',
    'baltimore', 'towson', 'college park', 'hyattsville', 'greenbelt',
    'prince george', 'howard county', 'anne arundel', 'baltimore county',
    'maryland', 'md', 'dmv area',
]

_md_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _MD_AREA_TERMS) + r')\b',
    re.IGNORECASE,
)


def _has_md_area_reference(text):
    return bool(_md_area_re.search(text))


# ─────────────────────────────────────────────────────────────
# Connecticut geo terms
# ─────────────────────────────────────────────────────────────
_CT_AREA_TERMS = [
    'connecticut', 'stamford', 'bridgeport', 'new haven', 'hartford',
    'waterbury', 'norwalk', 'danbury', 'greenwich', 'fairfield',
    'west hartford', 'milford', 'stratford', 'shelton', 'trumbull',
    'darien', 'westport', 'new canaan', 'ridgefield', 'newtown',
    'glastonbury', 'simsbury', 'avon', 'farmington', 'manchester',
    'enfield', 'east hartford', 'middletown', 'meriden', 'wallingford',
    'hamden', 'guilford', 'madison', 'branford', 'cheshire',
    'fairfield county', 'hartford county', 'new haven county',
    'ct',
]

_ct_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _CT_AREA_TERMS) + r')\b',
    re.IGNORECASE,
)


def _has_ct_area_reference(text):
    return bool(_ct_area_re.search(text))


# Combined geo-detection map for all states
_STATE_GEO_DETECTORS = {
    'NY': _has_nyc_area_reference,
    'CA': _has_ca_area_reference,
    'TX': _has_tx_area_reference,
    'IL': _has_il_area_reference,
    'WA': _has_wa_area_reference,
    'MD': _has_md_area_reference,
    'CT': _has_ct_area_reference,
}


# ─────────────────────────────────────────────────────────────
# Urgency detection
# ─────────────────────────────────────────────────────────────
URGENCY_KEYWORDS = [
    'asap', 'urgent', 'emergency', 'immediately', 'right now', 'today',
    'broken', 'burst', 'flooding', 'flooded', 'leak', 'leaking',
    'no heat', 'no hot water', 'no ac', 'no air conditioning',
    'backed up', 'clogged', 'overflowing', 'sewage',
    'dangerous', 'hazard', 'unsafe', 'sparking', 'smoke',
    'need someone now', 'need help asap', 'cant wait',
]

_urgency_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(k) for k in URGENCY_KEYWORDS) + r')\b',
    re.IGNORECASE,
)


def _has_urgency(text):
    return bool(_urgency_re.search(text))


# ─────────────────────────────────────────────────────────────
# Intent filtering
# ─────────────────────────────────────────────────────────────
_NOT_LEAD_PATTERNS = [
    r'(?:looking into|thinking about|want to|how to|trying to)\s+becom(?:e|ing)\s+(?:a |an )',
    r'how (?:do|can) (?:i|you|someone) become\b',
    r'career (?:in|as|change)',
    r'getting (?:into|licensed|certified)\b',
    r'should i become\b',
    r'\bi am a\b.{0,30}(?:plumber|electrician|contractor|roofer|painter|landscaper|hvac|handyman|realtor|agent|lawyer|attorney|dentist|therapist|vet|tutor|photographer|dj|caterer|mechanic|locksmith|accountant|cpa)',
    r"\bi'm a\b.{0,30}(?:plumber|electrician|contractor|roofer|painter|landscaper|hvac|handyman|realtor|agent|lawyer|attorney|dentist|therapist|vet|tutor|photographer|dj|caterer|mechanic|locksmith|accountant|cpa)",
    r'\bi(?:\'ve| have) been (?:a |an |doing |in ).{0,20}(?:plumber|electrician|contractor|roofer|painter|landscaper|hvac|handyman|realtor|agent|lawyer|attorney)',
    r'(?:^|\n)\s*(?:i offer|i provide|i do|my company|we offer|we provide|we specialize)\b',
    r'commission (?:rate|split|structure|percentage)',
    r'buyer.{0,5}s? agent commission',
    r'listing agent commission',
    r'agent.to.agent',
    r'NAR (?:settlement|ruling|lawsuit)',
    r'broker.{0,5}s? fee',
    r'ask me anything',
    r'\bama\b',
]

_not_lead_re = re.compile('|'.join(_NOT_LEAD_PATTERNS), re.IGNORECASE)


def _is_provider_not_consumer(text):
    return bool(_not_lead_re.search(text))


def _is_local_subreddit(subreddit):
    all_local = [s.lower() for s in ALL_LOCAL_SUBREDDITS]
    return subreddit.lower() in all_local


def _detect_state(subreddit, content):
    """Detect which state a post belongs to based on subreddit + content."""
    sub_lower = subreddit.lower()
    # Check subreddit membership first (fast)
    for state_code, subs in STATE_SUBREDDITS.items():
        if sub_lower in [s.lower() for s in subs]:
            return state_code
    # Fall back to content geo-matching
    for state_code, detector in _STATE_GEO_DETECTORS.items():
        if detector(content):
            return state_code
    return ''


# ─────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────

def fetch_subreddit_posts(subreddit, limit=50):
    url = f'https://www.reddit.com/r/{subreddit}/new.json?limit={limit}'
    headers = {'User-Agent': REDDIT_USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 429:
            logger.warning(f'/r/{subreddit}: rate limited (429)')
            return None
        if resp.status_code != 200:
            logger.warning(f'/r/{subreddit}: HTTP {resp.status_code}')
            return []
        data = resp.json()
        posts = []
        for child in data.get('data', {}).get('children', []):
            post = child.get('data', {})
            posts.append({
                'title': post.get('title', ''),
                'selftext': post.get('selftext', ''),
                'author': post.get('author', ''),
                'created_utc': post.get('created_utc', 0),
                'permalink': post.get('permalink', ''),
                'subreddit': post.get('subreddit', subreddit),
                'score': post.get('score', 0),
                'num_comments': post.get('num_comments', 0),
                'link_flair_text': post.get('link_flair_text', ''),
            })
        return posts
    except requests.RequestException as e:
        logger.error(f'Failed to fetch /r/{subreddit}: {e}')
        return []
    except (ValueError, KeyError) as e:
        logger.error(f'Failed to parse /r/{subreddit} JSON: {e}')
        return []


def fetch_subreddit_posts_apify(subreddit, limit=50):
    """Apify fallback stub. Returns empty list until credentials configured."""
    api_key = getattr(settings, 'APIFY_API_KEY', '')
    if not api_key:
        logger.info(f'[Apify] No APIFY_API_KEY, skipping /r/{subreddit}')
        return []
    logger.info(f'[Apify] Would fetch /r/{subreddit} (not yet implemented)')
    return []


def clean_content(title, selftext):
    parts = []
    if title:
        parts.append(title.strip())
    if selftext:
        text = selftext.strip()
        if len(text) > 3000:
            text = text[:3000] + '...'
        parts.append(text)
    return '\n\n'.join(parts)


def _post_lead_remote(ingest_url, api_key, lead_data):
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    try:
        resp = requests.post(ingest_url, data=json.dumps(lead_data), headers=headers, timeout=15)
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = {'raw': resp.text[:200]}
        return resp.status_code in (201, 409), resp.status_code, body
    except requests.RequestException as e:
        logger.error(f'[Remote] POST failed: {e}')
        return False, 0, {'error': str(e)}


# ─────────────────────────────────────────────────────────────
# Main monitor function
# ─────────────────────────────────────────────────────────────

def monitor_reddit(subreddits=None, max_age_hours=48, dry_run=False, remote=False,
                   state='ALL', use_apify=False):
    """
    Main monitoring function. Scans subreddits for service-related posts.

    Args:
        state: 'NY', 'CA', 'TX', 'IL', 'WA', 'MD', 'CT', or 'ALL'
        use_apify: if True, try Apify when direct fetch fails
    """
    if subreddits is None:
        if state in STATE_SUBREDDITS:
            subreddits = STATE_SUBREDDITS[state] + NATIONAL_SUBREDDITS + TRADE_SUBREDDITS
        else:
            subreddits = ALL_LOCAL_SUBREDDITS + NATIONAL_SUBREDDITS + TRADE_SUBREDDITS

    # Remote config
    ingest_url = ''
    ingest_key = ''
    if remote:
        ingest_url = getattr(settings, 'REMOTE_INGEST_URL', '')
        ingest_key = getattr(settings, 'INGEST_API_KEY', '')
        if not ingest_url or not ingest_key:
            logger.error('[Remote] REMOTE_INGEST_URL and INGEST_API_KEY required')
            return {'scraped': 0, 'created': 0, 'duplicates': 0, 'matched': 0,
                    'assigned': 0, 'errors': 1, 'geo_filtered': 0,
                    'intent_filtered': 0, 'dry_run_matches': [],
                    'remote_sent': 0, 'remote_failed': 0}

    stats = {
        'scraped': 0, 'created': 0, 'duplicates': 0, 'matched': 0,
        'assigned': 0, 'errors': 0, 'geo_filtered': 0, 'intent_filtered': 0,
        'dry_run_matches': [], 'remote_sent': 0, 'remote_failed': 0,
    }

    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    for i, subreddit in enumerate(subreddits):
        if i > 0:
            time.sleep(REQUEST_DELAY)

        logger.info(f'Scanning /r/{subreddit}...')
        posts = fetch_subreddit_posts(subreddit)

        if posts is None and use_apify:
            logger.info(f'[Apify] Falling back for /r/{subreddit}')
            posts = fetch_subreddit_posts_apify(subreddit)

        if posts is None:
            logger.warning('Rate limited by Reddit, stopping run')
            break

        if not posts:
            continue

        stats['scraped'] += len(posts)
        is_local = _is_local_subreddit(subreddit)

        for post in posts:
            try:
                # Age check
                created_utc = post.get('created_utc', 0)
                if created_utc:
                    posted_at = datetime.utcfromtimestamp(created_utc)
                    import datetime as _dt
                    posted_at = timezone.make_aware(posted_at, timezone=_dt.timezone.utc)
                    if posted_at < cutoff:
                        continue
                else:
                    posted_at = None

                content = clean_content(post['title'], post['selftext'])
                if not content:
                    continue

                permalink = post.get('permalink', '')
                source_url = f'https://www.reddit.com{permalink}' if permalink else ''
                if not source_url:
                    continue

                # Geo filter for non-local subs
                if not is_local:
                    if state in STATE_SUBREDDITS:
                        # Single-state mode: must match that state
                        detector = _STATE_GEO_DETECTORS.get(state)
                        if detector and not detector(content):
                            stats['geo_filtered'] += 1
                            continue
                    else:
                        # ALL mode: must match at least one served state
                        has_any = any(
                            det(content) for det in _STATE_GEO_DETECTORS.values()
                        )
                        if not has_any:
                            stats['geo_filtered'] += 1
                            continue

                # Intent filter
                if _is_provider_not_consumer(content):
                    stats['intent_filtered'] += 1
                    continue

                # Keyword matching
                from .lead_processor import match_keywords
                keyword_matches = match_keywords(content)
                if not keyword_matches:
                    continue

                stats['matched'] += 1
                best_category, matched_kws, score, confidence = keyword_matches[0]

                # Detect state + urgency
                detected_state = _detect_state(post['subreddit'], content)
                urgency = 'hot' if _has_urgency(content) else 'warm'

                if dry_run:
                    stats['dry_run_matches'].append({
                        'subreddit': post['subreddit'],
                        'title': post['title'][:100],
                        'category': best_category.name,
                        'keywords': matched_kws[:5],
                        'author': post['author'],
                        'url': source_url,
                        'confidence': confidence,
                        'score': score,
                        'state': detected_state,
                        'urgency': urgency,
                        'age_hours': round(
                            (timezone.now() - posted_at).total_seconds() / 3600, 1
                        ) if posted_at else '?',
                    })
                    continue

                # Remote mode
                if remote:
                    payload = {
                        'platform': 'reddit',
                        'source_url': source_url,
                        'source_content': content,
                        'author': post.get('author', ''),
                        'confidence': confidence,
                        'detected_category': best_category.slug,
                        'state': detected_state,
                        'raw_data': {
                            'subreddit': post['subreddit'],
                            'score': post.get('score', 0),
                            'num_comments': post.get('num_comments', 0),
                            'flair': post.get('link_flair_text', ''),
                            'matched_keywords': matched_kws[:5],
                        },
                    }
                    ok, status_code, body = _post_lead_remote(ingest_url, ingest_key, payload)
                    if ok:
                        if status_code == 201:
                            stats['remote_sent'] += 1
                            stats['created'] += 1
                        else:
                            stats['duplicates'] += 1
                    else:
                        stats['remote_failed'] += 1
                        stats['errors'] += 1
                    continue

                # Local save
                lead, created, num_assigned = process_lead(
                    platform='reddit',
                    source_url=source_url,
                    content=content,
                    author=post.get('author', ''),
                    posted_at=posted_at,
                    raw_data={
                        'subreddit': post['subreddit'],
                        'score': post.get('score', 0),
                        'num_comments': post.get('num_comments', 0),
                        'flair': post.get('link_flair_text', ''),
                    },
                    source_group='social_media',
                    source_type='reddit',
                    state=detected_state,
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except Exception as e:
                logger.error(f'Error processing Reddit post: {e}')
                stats['errors'] += 1

    logger.info(f'Reddit monitor complete: {stats}')
    return stats
