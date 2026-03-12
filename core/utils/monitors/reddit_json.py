"""
Reddit monitor for SalesSignal AI.
Uses Reddit's public JSON endpoints — no API key required.

Fetches /r/{subreddit}/new.json for each configured subreddit,
matches posts against service keywords, extracts location, and
creates Lead records via the standard process_lead() pipeline.
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
# Subreddit classification: LOCAL vs NATIONAL
# LOCAL subs are geographically relevant — every post accepted
# NATIONAL subs require a NYC-area location mention in text
# ─────────────────────────────────────────────────────────────
LOCAL_SUBREDDITS = [
    'AskNYC',
    'longisland',
    'brooklyn',
    'nyc',
    'astoria',
    'queens',
]

NATIONAL_SUBREDDITS = [
    'HomeImprovement',
    'personalfinance',
    'firsttimehomebuyer',
    'insurance',
    'RealEstate',
    'Moving',
    'legaladvice',
    'smallbusiness',
]

DEFAULT_SUBREDDITS = LOCAL_SUBREDDITS + NATIONAL_SUBREDDITS

# User-Agent per Reddit's API guidelines for public JSON
REDDIT_USER_AGENT = 'SalesSignalAI/1.0 (monitoring service)'

# Rate limit: seconds between subreddit requests
REQUEST_DELAY = 2.0

# ─────────────────────────────────────────────────────────────
# NYC-area location references for national sub geo-filter
# ─────────────────────────────────────────────────────────────
_NYC_AREA_TERMS = [
    # Boroughs & city
    'nyc', 'new york city', 'new york', 'manhattan', 'brooklyn', 'queens',
    'bronx', 'staten island', 'harlem', 'astoria', 'flushing',
    'williamsburg', 'bushwick', 'greenpoint', 'bed-stuy', 'bed stuy',
    'crown heights', 'park slope', 'bay ridge', 'sunset park',
    'east village', 'west village', 'upper east side', 'upper west side',
    'lower east side', 'tribeca', 'soho', 'chelsea', 'hells kitchen',
    'inwood', 'washington heights', 'jackson heights', 'forest hills',
    'rego park', 'jamaica', 'bayside', 'woodside', 'sunnyside',
    # Long Island
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
    # North / NJ
    'westchester', 'yonkers', 'white plains', 'new rochelle',
    'mount vernon', 'scarsdale', 'tarrytown', 'dobbs ferry',
    'jersey city', 'hoboken', 'newark', 'weehawken', 'bayonne',
    'north bergen', 'union city', 'fort lee', 'edgewater',
    # Codes / abbreviations
    'tri-state', 'tristate',
]

# Build a single compiled regex for NYC area detection
_nyc_area_re = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in _NYC_AREA_TERMS) + r')\b',
    re.IGNORECASE
)


def _has_nyc_area_reference(text):
    """Return True if text mentions a NYC-area location."""
    return bool(_nyc_area_re.search(text))


def _is_local_subreddit(subreddit):
    """Check if a subreddit is in the local (geo-relevant) group."""
    return subreddit.lower() in [s.lower() for s in LOCAL_SUBREDDITS]


# ─────────────────────────────────────────────────────────────
# Intent filtering — exclude posts that are NOT service requests
# ─────────────────────────────────────────────────────────────
_NOT_LEAD_PATTERNS = [
    # Career / becoming a professional
    r'(?:looking into|thinking about|want to|how to|trying to)\s+becom(?:e|ing)\s+(?:a |an )',
    r'how (?:do|can) (?:i|you|someone) become\b',
    r'career (?:in|as|change)',
    r'getting (?:into|licensed|certified)\b',
    r'should i become\b',
    # Author IS the professional (AMA, self-promo, offering services)
    r'\bi am a\b.{0,30}(?:plumber|electrician|contractor|roofer|painter|landscaper|hvac|handyman|realtor|agent|lawyer|attorney|dentist|therapist|vet|tutor|photographer|dj|caterer|mechanic|locksmith|accountant|cpa)',
    r"\bi'm a\b.{0,30}(?:plumber|electrician|contractor|roofer|painter|landscaper|hvac|handyman|realtor|agent|lawyer|attorney|dentist|therapist|vet|tutor|photographer|dj|caterer|mechanic|locksmith|accountant|cpa)",
    r'\bi(?:\'ve| have) been (?:a |an |doing |in ).{0,20}(?:plumber|electrician|contractor|roofer|painter|landscaper|hvac|handyman|realtor|agent|lawyer|attorney)',
    r'(?:^|\n)\s*(?:i offer|i provide|i do|my company|we offer|we provide|we specialize)\b',
    # Industry discussion, not consumer requests
    r'commission (?:rate|split|structure|percentage)',
    r'buyer.{0,5}s? agent commission',
    r'listing agent commission',
    r'agent.to.agent',
    r'NAR (?:settlement|ruling|lawsuit)',
    r'broker.{0,5}s? fee',
    # AMA / "ask me anything"
    r'ask me anything',
    r'\bama\b',
]

_not_lead_re = re.compile(
    '|'.join(_NOT_LEAD_PATTERNS), re.IGNORECASE
)


def _is_provider_not_consumer(text):
    """Return True if the post appears to be from a provider, not a consumer seeking service."""
    return bool(_not_lead_re.search(text))


# ─────────────────────────────────────────────────────────────
# Fetch & process
# ─────────────────────────────────────────────────────────────

def fetch_subreddit_posts(subreddit, limit=50):
    """
    Fetch recent posts from a subreddit using Reddit's public JSON API.
    Returns list of post dicts or empty list on failure.
    """
    url = f'https://www.reddit.com/r/{subreddit}/new.json?limit={limit}'
    headers = {'User-Agent': REDDIT_USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code == 429:
            logger.warning(f'Reddit rate limited on /r/{subreddit}, stopping')
            return None  # Signal to stop

        if resp.status_code != 200:
            logger.warning(f'Reddit /r/{subreddit} returned {resp.status_code}')
            return []

        data = resp.json()
        children = data.get('data', {}).get('children', [])
        posts = []
        for child in children:
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


def clean_content(title, selftext):
    """Combine title + selftext into clean content string."""
    parts = []
    if title:
        parts.append(title.strip())
    if selftext:
        # Truncate extremely long posts
        text = selftext.strip()
        if len(text) > 3000:
            text = text[:3000] + '...'
        parts.append(text)
    return '\n\n'.join(parts)


def _post_lead_remote(ingest_url, api_key, lead_data):
    """
    POST a lead to a remote SalesSignal instance via the ingest API.

    Args:
        ingest_url: full URL of the /api/ingest-lead/ endpoint
        api_key: Bearer token for authentication
        lead_data: dict with platform, source_url, source_content, etc.

    Returns:
        (success: bool, status_code: int, response_body: dict)
    """
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(
            ingest_url,
            data=json.dumps(lead_data),
            headers=headers,
            timeout=15,
        )
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = {'raw': resp.text[:200]}
        return resp.status_code in (201, 409), resp.status_code, body
    except requests.RequestException as e:
        logger.error(f'[Remote] POST failed: {e}')
        return False, 0, {'error': str(e)}


def monitor_reddit(subreddits=None, max_age_hours=48, dry_run=False, remote=False):
    """
    Main monitoring function. Scans subreddits for service-related posts.

    Args:
        subreddits: list of subreddit names (default: DEFAULT_SUBREDDITS)
        max_age_hours: ignore posts older than this (default: 48)
        dry_run: if True, log matches but don't create Lead records
        remote: if True, POST leads to REMOTE_INGEST_URL instead of saving locally

    Returns:
        dict with counts: scraped, created, duplicates, matched, errors
    """
    if subreddits is None:
        subreddits = DEFAULT_SUBREDDITS

    # Resolve remote config
    ingest_url = ''
    ingest_key = ''
    if remote:
        ingest_url = getattr(settings, 'REMOTE_INGEST_URL', '')
        ingest_key = getattr(settings, 'INGEST_API_KEY', '')
        if not ingest_url or not ingest_key:
            logger.error(
                '[Remote] REMOTE_INGEST_URL and INGEST_API_KEY must be set '
                'in .env for --remote mode'
            )
            return {
                'scraped': 0, 'created': 0, 'duplicates': 0, 'matched': 0,
                'assigned': 0, 'errors': 1, 'geo_filtered': 0,
                'intent_filtered': 0, 'dry_run_matches': [],
                'remote_sent': 0, 'remote_failed': 0,
            }

    stats = {
        'scraped': 0,
        'created': 0,
        'duplicates': 0,
        'matched': 0,
        'assigned': 0,
        'errors': 0,
        'geo_filtered': 0,
        'intent_filtered': 0,
        'dry_run_matches': [],
        'remote_sent': 0,
        'remote_failed': 0,
    }

    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    for i, subreddit in enumerate(subreddits):
        # Rate limit between requests
        if i > 0:
            time.sleep(REQUEST_DELAY)

        logger.info(f'Scanning /r/{subreddit}...')
        posts = fetch_subreddit_posts(subreddit)

        # None means rate limited — stop entirely
        if posts is None:
            logger.warning('Rate limited by Reddit, stopping run')
            break

        if not posts:
            continue

        stats['scraped'] += len(posts)
        is_local = _is_local_subreddit(subreddit)

        for post in posts:
            try:
                # Check age
                created_utc = post.get('created_utc', 0)
                if created_utc:
                    posted_at = datetime.utcfromtimestamp(created_utc)
                    import datetime as _dt
                    posted_at = timezone.make_aware(posted_at, timezone=_dt.timezone.utc)
                    if posted_at < cutoff:
                        continue
                else:
                    posted_at = None

                # Build content
                content = clean_content(post['title'], post['selftext'])
                if not content:
                    continue

                # Build source URL
                permalink = post.get('permalink', '')
                source_url = f'https://www.reddit.com{permalink}' if permalink else ''

                if not source_url:
                    continue

                # ── Geographic filter for national subs ──
                if not is_local and not _has_nyc_area_reference(content):
                    stats['geo_filtered'] += 1
                    continue

                # ── Intent filter: skip provider posts / industry discussion ──
                if _is_provider_not_consumer(content):
                    stats['intent_filtered'] += 1
                    continue

                # Quick keyword pre-check using the lead processor's match_keywords
                from .lead_processor import match_keywords
                keyword_matches = match_keywords(content)
                if not keyword_matches:
                    continue

                stats['matched'] += 1
                best_category, matched_kws, score, confidence = keyword_matches[0]

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
                        'age_hours': round(
                            (timezone.now() - posted_at).total_seconds() / 3600, 1
                        ) if posted_at else '?',
                    })
                    continue

                # ── Remote mode: POST to remote ingest API ──
                if remote:
                    payload = {
                        'platform': 'reddit',
                        'source_url': source_url,
                        'source_content': content,
                        'author': post.get('author', ''),
                        'confidence': confidence,
                        'detected_category': best_category.slug,
                        'raw_data': {
                            'subreddit': post['subreddit'],
                            'score': post.get('score', 0),
                            'num_comments': post.get('num_comments', 0),
                            'flair': post.get('link_flair_text', ''),
                            'matched_keywords': matched_kws[:5],
                        },
                    }
                    ok, status_code, body = _post_lead_remote(
                        ingest_url, ingest_key, payload,
                    )
                    if ok:
                        if status_code == 201:
                            stats['remote_sent'] += 1
                            stats['created'] += 1
                        else:
                            stats['duplicates'] += 1
                        logger.info(
                            f'[Remote] {status_code} — {body.get("status", "?")}'
                        )
                    else:
                        stats['remote_failed'] += 1
                        stats['errors'] += 1
                        logger.warning(
                            f'[Remote] Failed ({status_code}): {body}'
                        )
                    continue

                # Create lead via standard pipeline
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
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except Exception as e:
                logger.error(f'Error processing Reddit post: {e}')
                stats['errors'] += 1
                continue

    logger.info(f'Reddit monitor complete: {stats}')
    return stats
