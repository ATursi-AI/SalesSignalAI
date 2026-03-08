"""
Reddit local subreddit monitor for SalesSignal AI.
Uses PRAW to scan local/regional subreddits for service requests.
"""
import logging
import random
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Local subreddits to monitor (tri-state area)
DEFAULT_SUBREDDITS = [
    # Long Island
    'longisland',
    'nassaucounty',
    # NYC
    'nyc',
    'asknyc',
    'brooklyn',
    'queens',
    'bronx',
    'StatenIsland',
    # Westchester / Hudson Valley
    'Westchester',
    'hudsonvalley',
    # New Jersey
    'newjersey',
    'jerseycity',
    'hoboken',
    # Connecticut
    'Connecticut',
    'stamford',
    # General service/recommendation subreddits
    'HomeImprovement',
]

# Keywords that signal someone is looking for a service
SERVICE_REQUEST_SIGNALS = [
    'looking for', 'need a', 'need an', 'anyone know', 'can anyone recommend',
    'recommendations for', 'recommendation for', 'who do you use',
    'does anyone know', 'looking to hire', 'need help with',
    'can someone recommend', 'any recommendations', 'suggest a',
    'best plumber', 'best electrician', 'best contractor', 'best roofer',
    'best painter', 'best handyman', 'best mechanic', 'best landscaper',
    'best cleaner', 'best mover', 'best hvac', 'best exterminator',
    'need repair', 'needs fixing', 'broken', 'leak', 'flooding',
    'emergency', 'help needed', 'urgent',
    'quote for', 'estimate for', 'how much to', 'cost to',
    'hiring', 'for hire', 'wanted', 'seeking',
]

# Flairs that typically indicate service requests
SERVICE_FLAIRS = [
    'recommendation', 'recommendations', 'question', 'help',
    'advice', 'request', 'looking for', 'wanted',
]


def get_reddit_client():
    """Initialize and return a PRAW Reddit client."""
    try:
        import praw
    except ImportError:
        logger.error("PRAW not installed. Run: pip install praw")
        return None

    client_id = settings.REDDIT_CLIENT_ID
    client_secret = settings.REDDIT_CLIENT_SECRET

    if not client_id or not client_secret:
        logger.warning("Reddit API credentials not configured. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET.")
        return None

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=settings.REDDIT_USER_AGENT,
            username=settings.REDDIT_USERNAME or None,
            password=settings.REDDIT_PASSWORD or None,
        )
        # Verify connection
        reddit.user.me()
        logger.info("Reddit client authenticated successfully")
    except Exception:
        # Read-only mode (no username/password)
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=settings.REDDIT_USER_AGENT,
        )
        logger.info("Reddit client initialized in read-only mode")

    return reddit


def is_service_request(title, body='', flair=''):
    """Check if a post looks like a service request based on signals."""
    combined = f"{title} {body}".lower()

    # Check flair
    if flair:
        flair_lower = flair.lower()
        for sf in SERVICE_FLAIRS:
            if sf in flair_lower:
                return True

    # Check content for service request signals
    for signal in SERVICE_REQUEST_SIGNALS:
        if signal in combined:
            return True

    return False


def scan_subreddit(reddit, subreddit_name, sort='new', limit=50, max_age_hours=48):
    """
    Scan a subreddit for service request posts.
    Returns list of post dicts that look like service requests.
    """
    try:
        subreddit = reddit.subreddit(subreddit_name)
    except Exception as e:
        logger.error(f"Cannot access r/{subreddit_name}: {e}")
        return []

    cutoff = timezone.now() - timedelta(hours=max_age_hours)
    posts = []

    try:
        if sort == 'new':
            submissions = subreddit.new(limit=limit)
        elif sort == 'hot':
            submissions = subreddit.hot(limit=limit)
        else:
            submissions = subreddit.new(limit=limit)

        for submission in submissions:
            try:
                # Convert Unix timestamp
                posted_at = datetime.fromtimestamp(submission.created_utc, tz=timezone.utc)

                # Skip old posts
                if posted_at < cutoff:
                    continue

                title = submission.title or ''
                body = submission.selftext or ''
                flair = submission.link_flair_text or ''
                author = str(submission.author) if submission.author else '[deleted]'

                # Filter: only process posts that look like service requests
                if not is_service_request(title, body, flair):
                    continue

                posts.append({
                    'id': submission.id,
                    'title': title,
                    'body': body,
                    'url': f"https://reddit.com{submission.permalink}",
                    'author': f"u/{author}",
                    'posted_at': posted_at,
                    'flair': flair,
                    'subreddit': subreddit_name,
                    'score': submission.score,
                    'num_comments': submission.num_comments,
                })
            except Exception as e:
                logger.debug(f"Error parsing submission in r/{subreddit_name}: {e}")
                continue

    except Exception as e:
        logger.error(f"Error scanning r/{subreddit_name}: {e}")
        return []

    logger.info(f"Found {len(posts)} service requests in r/{subreddit_name}")
    return posts


def monitor_reddit(subreddits=None, sort='new', limit=50, max_age_hours=48):
    """
    Main monitoring function. Scans subreddits and processes matching posts as leads.

    Args:
        subreddits: List of subreddit names (default: DEFAULT_SUBREDDITS)
        sort: Sort order — 'new' or 'hot'
        limit: Max posts to scan per subreddit
        max_age_hours: Skip posts older than this

    Returns:
        dict with counts: scanned, created, duplicates, assigned
    """
    # Cooldown check
    from core.models.monitoring import MonitorRun
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='reddit', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=30):
            reason = f'reddit cooldown: {int((timedelta(minutes=30) - elapsed).total_seconds() / 60)}m remaining'
            logger.info(reason)
            return {'scanned': 0, 'created': 0, 'duplicates': 0, 'assigned': 0,
                    'errors': 0, 'skipped_reason': reason}

    reddit = get_reddit_client()
    if not reddit:
        logger.error("Reddit client unavailable — skipping monitor")
        return {'scanned': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    if subreddits is None:
        subreddits = DEFAULT_SUBREDDITS

    # Randomize scraping order
    subreddits = list(subreddits)
    random.shuffle(subreddits)

    stats = {'scanned': 0, 'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0}

    for sub_name in subreddits:
        logger.info(f"Scanning r/{sub_name}...")
        posts = scan_subreddit(reddit, sub_name, sort=sort, limit=limit, max_age_hours=max_age_hours)
        stats['scanned'] += len(posts)

        for post in posts:
            try:
                # Combine title + body as full content
                content = post['title']
                if post['body']:
                    content += f"\n\n{post['body']}"

                # Add subreddit context for location detection
                content += f"\n(Posted in r/{post['subreddit']})"

                lead, created, num_assigned = process_lead(
                    platform='reddit',
                    source_url=post['url'],
                    content=content,
                    author=post['author'],
                    posted_at=post['posted_at'],
                    raw_data={
                        'reddit_id': post['id'],
                        'subreddit': post['subreddit'],
                        'flair': post['flair'],
                        'score': post['score'],
                        'num_comments': post['num_comments'],
                    },
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except Exception as e:
                logger.error(f"Error processing post {post.get('url', '?')}: {e}")
                stats['errors'] += 1
                continue

    logger.info(f"Reddit monitor complete: {stats}")
    return stats
