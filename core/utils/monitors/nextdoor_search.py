"""
Nextdoor SEARCH-based monitor for SalesSignal AI.

Instead of scrolling the feed (which misses most posts), this monitor
searches for specific service-request keywords on Nextdoor using
Playwright. For each keyword, it:

  1. Navigates to Nextdoor search
  2. Types "{keyword} recommendations" in the search bar
  3. Clicks the "Posts" tab to filter to posts only
  4. Extracts: poster name, neighborhood, date, full post text, comment count
  5. Filters OUT response/recommendation posts — keeps REQUEST posts only
  6. Deduplicates by poster name + date

Requires: Playwright, saved Nextdoor cookies (same auth as
nextdoor_playwright.py), residential IP.

Usage:
    python manage.py monitor_nextdoor_search --days 7 --dry-run
    python manage.py monitor_nextdoor_search --keywords "plumber,electrician"
    python manage.py monitor_nextdoor_search --remote
"""
import json
import logging
import random
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote_plus

from django.conf import settings
from django.utils import timezone

from core.models.monitoring import MonitorRun
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Paths (shared with nextdoor_playwright)
BASE_DIR = Path(settings.BASE_DIR)
COOKIE_FILE = BASE_DIR / 'browser_data' / 'nextdoor_cookies.json'

# Cooldown: max 2 runs per day
COOLDOWN_HOURS = 12

# Full search keyword list — 24 service categories
DEFAULT_KEYWORDS = [
    'plumber', 'electrician', 'hvac', 'house cleaning', 'roofer',
    'handyman', 'painter', 'landscaper', 'pest control', 'locksmith',
    'mover', 'contractor', 'kitchen renovation', 'bathroom renovation',
    'flooring', 'windows', 'garage door', 'fencing', 'tree service',
    'gutter', 'power washing', 'paving', 'masonry',
]

# Phrases indicating a REQUEST post (keep these)
REQUEST_SIGNALS = [
    'looking for', 'anyone know', 'need a', 'need an', 'recommend a',
    'recommend an', 'any recommendations', 'can anyone recommend',
    'does anyone have', 'who do you use', 'who do you call',
    'anyone have a good', 'suggestions for', 'referral for',
    'looking to hire', 'need help finding', 'know a good',
    'reliable', 'affordable', 'in the area', 'near me',
    'quote for', 'estimate for', 'anyone used',
]

# Phrases indicating a RESPONSE post (filter these out)
RESPONSE_SIGNALS = [
    'i recommend', 'i highly recommend', 'try calling', 'we used',
    'we use', 'i use', 'i used', 'give them a call', 'check out',
    'they did a great', 'they were great', 'they are great',
    'highly recommend', 'you should try', 'call them',
    'here is their number', "here's their number",
    'their number is', 'contact them at', 'reach out to',
    'pm me', 'dm me', 'message me', 'i can help',
    'we offer', 'our company', 'my company', 'we specialize',
    'free estimate', 'call us', 'reach us at',
]

# User agents for anti-detection
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15',
]


def _is_request_post(text):
    """Return True if text is a service REQUEST (not a response/ad)."""
    text_lower = text.lower()

    # Must contain at least one request signal
    has_request = any(sig in text_lower for sig in REQUEST_SIGNALS)
    if not has_request:
        return False

    # Check if it's actually a response/self-promotion
    response_count = sum(1 for sig in RESPONSE_SIGNALS if sig in text_lower)
    request_count = sum(1 for sig in REQUEST_SIGNALS if sig in text_lower)

    # If more response signals than request signals, it's a response
    if response_count > request_count:
        return False

    return True


def _score_confidence(text, keyword):
    """Score confidence based on specificity of the request."""
    text_lower = text.lower()

    # HIGH: explicit job details (mentions specific work, quantities, rooms, etc.)
    high_patterns = [
        r'\d+\s*(bathroom|kitchen|room|floor|window|door|job)',
        r'(replace|install|repair|fix)\s+(my|the|our|a)\s+\w+',
        r'(need|looking for).*?asap',
        r'(need|looking for).*?(urgent|emergency)',
        r'\$\d+',  # mentions a budget
        r'(quote|estimate)\s+(for|on)',
    ]
    for pattern in high_patterns:
        if re.search(pattern, text_lower):
            return 'high'

    # MEDIUM: general request with the keyword
    if keyword.lower() in text_lower:
        return 'medium'

    return 'low'


def _load_cookies():
    """Load saved cookies from disk."""
    if COOKIE_FILE.exists():
        try:
            with open(COOKIE_FILE, 'r') as f:
                cookies = json.load(f)
            logger.info(f'Loaded {len(cookies)} cookies from {COOKIE_FILE}')
            return cookies
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f'Failed to load cookies: {e}')
    return None


def _save_cookies(cookies):
    """Save cookies to disk for session reuse."""
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIE_FILE, 'w') as f:
        json.dump(cookies, f, indent=2)
    logger.info(f'Saved {len(cookies)} cookies to {COOKIE_FILE}')


def _check_cooldown():
    """Return remaining cooldown minutes, or 0 if ready."""
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='nextdoor_search', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        cooldown = timedelta(hours=COOLDOWN_HOURS)
        if elapsed < cooldown:
            return int((cooldown - elapsed).total_seconds() / 60)
    return 0


def _random_viewport():
    """Return a random but realistic viewport size."""
    viewports = [
        (1920, 1080), (1366, 768), (1536, 864),
        (1440, 900), (1280, 720), (1600, 900),
    ]
    return random.choice(viewports)


async def _check_session(page):
    """Check if saved cookies give us a valid session."""
    logger.info('Checking saved session...')
    await page.goto('https://nextdoor.com/feed/', wait_until='domcontentloaded')
    await page.wait_for_timeout(3000)
    url = page.url
    if 'login' in url or 'auth' in url:
        logger.info('Session expired — need to log in again')
        return False
    if 'feed' in url or 'news_feed' in url or 'neighborhood' in url:
        logger.info('Session valid')
        return True
    logger.info('Session status unclear — will try to proceed')
    return True


async def _login(page, email, password):
    """Log into Nextdoor with email/password."""
    logger.info('Logging into Nextdoor...')
    await page.goto('https://nextdoor.com/login/', wait_until='domcontentloaded')
    await page.wait_for_timeout(random.randint(2000, 4000))

    # Find email input
    for sel in ['input[name="email"]', 'input[type="email"]', '#email',
                'input[id*="email"]', 'input[placeholder*="Email"]']:
        try:
            el = await page.wait_for_selector(sel, timeout=3000)
            if el:
                await el.click()
                await page.wait_for_timeout(random.randint(200, 500))
                await el.type(email, delay=random.randint(50, 120))
                break
        except Exception:
            continue
    else:
        logger.error('Could not find email input')
        return False

    await page.wait_for_timeout(random.randint(300, 800))

    # Find password input
    for sel in ['input[name="password"]', 'input[type="password"]', '#password']:
        try:
            el = await page.wait_for_selector(sel, timeout=3000)
            if el:
                await el.click()
                await page.wait_for_timeout(random.randint(200, 500))
                await el.type(password, delay=random.randint(50, 120))
                break
        except Exception:
            continue
    else:
        logger.error('Could not find password input')
        return False

    await page.wait_for_timeout(random.randint(500, 1000))

    # Click sign-in button
    for sel in ['button[type="submit"]', 'button:has-text("Sign in")',
                'button:has-text("Log in")', 'input[type="submit"]']:
        try:
            btn = await page.wait_for_selector(sel, timeout=2000)
            if btn:
                await btn.click()
                break
        except Exception:
            continue

    # Wait for navigation
    try:
        await page.wait_for_url('**/feed/**', timeout=15000)
        logger.info('Login successful')
        return True
    except Exception:
        pass

    await page.wait_for_timeout(3000)
    url = page.url
    if 'feed' in url or ('login' not in url and 'auth' not in url):
        logger.info(f'Login appears successful — {url}')
        return True

    logger.error(f'Login may have failed — {url}')
    return False


async def _human_delay(page, min_ms=800, max_ms=2500):
    """Random human-like delay."""
    await page.wait_for_timeout(random.randint(min_ms, max_ms))


async def _search_keyword(page, keyword, stats):
    """
    Search Nextdoor for "{keyword} recommendations" and extract posts.

    Flow:
      1. Navigate to search URL
      2. Click "Posts" tab to filter
      3. Extract posts from results
      4. Filter for requests only
    """
    search_query = f'{keyword} recommendations'
    search_url = f'https://nextdoor.com/search/?query={quote_plus(search_query)}'

    logger.info(f'  Searching: "{search_query}"')

    try:
        await page.goto(search_url, wait_until='domcontentloaded')
        await _human_delay(page, 3000, 5000)
    except Exception as e:
        logger.warning(f'  Navigation failed for "{keyword}": {e}')
        return []

    # Try to click "Posts" tab to filter results
    posts_tab_selectors = [
        'button:has-text("Posts")',
        'a:has-text("Posts")',
        '[role="tab"]:has-text("Posts")',
        '[data-testid*="posts"]',
        'div[class*="Tab"]:has-text("Posts")',
    ]
    for sel in posts_tab_selectors:
        try:
            tab = await page.wait_for_selector(sel, timeout=3000)
            if tab:
                await tab.click()
                await _human_delay(page, 2000, 3500)
                logger.info(f'  Clicked "Posts" tab')
                break
        except Exception:
            continue

    # Scroll to load more results
    for _ in range(3):
        scroll_amount = random.randint(400, 800)
        await page.mouse.wheel(0, scroll_amount)
        await _human_delay(page, 800, 1800)
        # Occasional mouse movement
        if random.random() > 0.5:
            await page.mouse.move(random.randint(200, 800), random.randint(200, 600))
            await _human_delay(page, 200, 500)

    # Extract posts from search results
    posts = []

    # Try structured selectors first
    post_selectors = [
        'article',
        '[data-testid*="post"]',
        '[data-testid*="story"]',
        '[data-testid*="search-result"]',
        'div[class*="Post"]',
        'div[class*="post-"]',
        'div[class*="story"]',
        'div[class*="SearchResult"]',
        'div[class*="FeedCard"]',
    ]

    elements = []
    for sel in post_selectors:
        try:
            found = await page.query_selector_all(sel)
            if found and len(found) >= 2:
                # Verify these are real content blocks
                sample = await found[0].inner_text()
                if sample and len(sample.strip()) > 30:
                    elements = found
                    logger.info(f'  Found {len(found)} elements via {sel}')
                    break
        except Exception:
            continue

    if not elements:
        # Fallback: grab text blocks from main content
        try:
            for sel in ['main', '[role="main"]', '#content', 'body']:
                container = await page.query_selector(sel)
                if not container:
                    continue
                children = await container.query_selector_all('div')
                substantial = []
                for child in children:
                    try:
                        text = await child.inner_text()
                        if text and 50 < len(text.strip()) < 3000:
                            substantial.append(child)
                    except Exception:
                        continue
                if len(substantial) >= 2:
                    elements = substantial[:15]
                    break
        except Exception:
            pass

    # Parse each element
    for element in elements[:15]:
        try:
            post = await _extract_post(element, page, keyword)
            if post:
                posts.append(post)
        except Exception:
            continue

    stats['items_scraped'] += len(posts)
    logger.info(f'  Extracted {len(posts)} posts for "{keyword}"')
    return posts


async def _extract_post(element, page, keyword):
    """Extract data from a single search result element."""
    # Get full text
    try:
        text = await element.inner_text()
    except Exception:
        return None

    if not text or len(text.strip()) < 20:
        return None

    text = text.strip()[:2000]

    # Extract author name
    author = ''
    for sel in ['[class*="author"]', '[class*="Author"]', '[class*="name"]',
                '[class*="Name"]', '[class*="Actor"]']:
        try:
            el = await element.query_selector(sel)
            if el:
                name = await el.inner_text()
                if name and len(name) < 100:
                    author = name.strip().split('\n')[0]
                    break
        except Exception:
            continue

    # Extract neighborhood/location
    neighborhood = ''
    for sel in ['[class*="neighborhood"]', '[class*="Neighborhood"]',
                '[class*="location"]', '[class*="Location"]',
                '[class*="subtitle"]', '[class*="byline"]']:
        try:
            el = await element.query_selector(sel)
            if el:
                loc = await el.inner_text()
                if loc and len(loc) < 200:
                    neighborhood = loc.strip().split('\n')[0]
                    break
        except Exception:
            continue

    # Extract post URL
    url = ''
    for link_sel in ['a[href*="/p/"]', 'a[href*="/post/"]', 'a[href*="/news/"]']:
        try:
            link = await element.query_selector(link_sel)
            if link:
                href = await link.get_attribute('href')
                if href:
                    url = f'https://nextdoor.com{href}' if href.startswith('/') else href
                    break
        except Exception:
            continue
    if not url:
        url = page.url

    # Extract comment count
    comment_count = 0
    for sel in ['[class*="comment"]', '[class*="Comment"]', '[class*="reply"]']:
        try:
            el = await element.query_selector(sel)
            if el:
                ct = await el.inner_text()
                # Look for a number
                m = re.search(r'(\d+)', ct or '')
                if m:
                    comment_count = int(m.group(1))
                    break
        except Exception:
            continue

    return {
        'text': text,
        'author': author,
        'neighborhood': neighborhood,
        'url': url,
        'comment_count': comment_count,
        'keyword': keyword,
    }


async def _run_search(email, password, keywords, headed=False):
    """
    Main browser automation:
    1. Launch with anti-detection
    2. Load cookies or login
    3. Search for each keyword
    4. Return all extracted posts
    """
    from playwright.async_api import async_playwright

    width, height = _random_viewport()
    user_agent = random.choice(USER_AGENTS)
    all_posts = []
    stats = {'items_scraped': 0}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headed,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
            ],
        )

        context = await browser.new_context(
            viewport={'width': width, 'height': height},
            user_agent=user_agent,
            locale='en-US',
            timezone_id='America/New_York',
        )

        # Mask automation indicators
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = {runtime: {}};
        """)

        page = await context.new_page()

        # Load saved cookies
        saved_cookies = _load_cookies()
        session_valid = False
        if saved_cookies:
            try:
                await context.add_cookies(saved_cookies)
                session_valid = await _check_session(page)
            except Exception as e:
                logger.warning(f'Error loading cookies: {e}')

        # Login if needed
        if not session_valid:
            if not email or not password:
                logger.error('No Nextdoor credentials — set NEXTDOOR_EMAIL/NEXTDOOR_PASSWORD')
                await browser.close()
                return [], stats

            login_ok = await _login(page, email, password)
            if not login_ok:
                logger.error('Nextdoor login failed')
                await browser.close()
                return [], stats

            cookies = await context.cookies()
            _save_cookies(cookies)

        # Search each keyword
        for i, keyword in enumerate(keywords):
            logger.info(f'[{i+1}/{len(keywords)}] Keyword: {keyword}')
            try:
                posts = await _search_keyword(page, keyword, stats)
                all_posts.extend(posts)
            except Exception as e:
                logger.warning(f'Search failed for "{keyword}": {e}')

            # Random 3-5 second delay between searches (anti-detection)
            if i < len(keywords) - 1:
                delay = random.randint(3000, 5000)
                logger.info(f'  Waiting {delay/1000:.1f}s before next search...')
                await page.wait_for_timeout(delay)

        # Save updated cookies
        try:
            cookies = await context.cookies()
            _save_cookies(cookies)
        except Exception:
            pass

        await browser.close()

    return all_posts, stats


def _post_lead_remote(ingest_url, api_key, lead_data):
    """POST a lead to a remote SalesSignal instance."""
    import requests as req

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = req.post(ingest_url, data=json.dumps(lead_data),
                        headers=headers, timeout=15)
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = {'raw': resp.text[:200]}
        return resp.status_code in (201, 409), resp.status_code, body
    except Exception as e:
        logger.error(f'[Remote] POST failed: {e}')
        return False, 0, {'error': str(e)}


def monitor_nextdoor_search(
    keywords=None,
    days=7,
    dry_run=False,
    remote=False,
    force=False,
    headed=False,
):
    """
    Search Nextdoor for service request posts using Playwright.

    Searches for each keyword + "recommendations", filters for REQUEST
    posts only (not responses/ads), and creates leads.

    Args:
        keywords: list of search keywords (default: DEFAULT_KEYWORDS)
        days: only include posts from last N days (used for display, not filtering)
        dry_run: log matches without creating Lead records
        remote: POST leads to remote ingest API
        force: bypass cooldown timer
        headed: launch visible browser for debugging

    Returns:
        dict with stats
    """
    import asyncio

    stats = {
        'keywords_searched': 0,
        'posts_found': 0,
        'requests_found': 0,
        'responses_filtered': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
        'remote_sent': 0,
        'remote_failed': 0,
    }

    # Cooldown check
    remaining = _check_cooldown()
    if remaining > 0 and not force:
        reason = f'nextdoor_search cooldown: {remaining}m remaining'
        logger.info(reason)
        stats['skipped_reason'] = reason
        return stats

    # Get credentials
    email = getattr(settings, 'NEXTDOOR_EMAIL', '')
    password = getattr(settings, 'NEXTDOOR_PASSWORD', '')

    if not email or not password:
        logger.error('NEXTDOOR_EMAIL and NEXTDOOR_PASSWORD must be set')
        stats['error'] = 'credentials_not_configured'
        return stats

    # Remote config
    ingest_url = None
    api_key = None
    if remote:
        ingest_url = getattr(settings, 'REMOTE_INGEST_URL', '')
        api_key = getattr(settings, 'INGEST_API_KEY', '')
        if not ingest_url or not api_key:
            logger.error('REMOTE_INGEST_URL and INGEST_API_KEY required for --remote')
            stats['error'] = 'remote_not_configured'
            return stats

    if keywords is None:
        keywords = DEFAULT_KEYWORDS

    stats['keywords_searched'] = len(keywords)

    # Record monitor run
    run = MonitorRun.objects.create(
        monitor_name='nextdoor_search',
        status='running',
        started_at=timezone.now(),
    )

    try:
        # Run async browser automation
        all_posts, scrape_stats = asyncio.run(
            _run_search(email, password, keywords, headed)
        )

        stats['posts_found'] = len(all_posts)
        logger.info(f'Total posts extracted: {len(all_posts)}')

        # Deduplicate by (author + first 100 chars of text)
        seen = set()
        unique_posts = []
        for post in all_posts:
            dedup_key = (
                post.get('author', '').strip().lower()
                + '|'
                + post['text'][:100].strip().lower()
            )
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique_posts.append(post)

        logger.info(f'Unique posts after dedup: {len(unique_posts)}')

        # Process posts
        for post in unique_posts:
            text = post.get('text', '')
            author = post.get('author', '')
            neighborhood = post.get('neighborhood', '')
            url = post.get('url', 'https://nextdoor.com')
            keyword = post.get('keyword', '')
            comment_count = post.get('comment_count', 0)

            # Filter: keep only REQUEST posts
            if not _is_request_post(text):
                stats['responses_filtered'] += 1
                continue

            stats['requests_found'] += 1
            confidence = _score_confidence(text, keyword)

            # Build content
            content = text[:2000]
            if neighborhood:
                content += f'\n(Neighborhood: {neighborhood})'
            if comment_count:
                content += f'\n({comment_count} comments)'

            if dry_run:
                logger.info(
                    f'[DRY RUN] [{confidence.upper()}] {keyword}: '
                    f'{author or "Anonymous"} — '
                    f'{text[:120]}...'
                )
                stats['created'] += 1
                continue

            if remote:
                lead_data = {
                    'platform': 'nextdoor',
                    'source_url': url,
                    'source_content': content,
                    'author': author,
                    'confidence': confidence,
                }
                ok, status_code, body = _post_lead_remote(
                    ingest_url, api_key, lead_data
                )
                if ok:
                    if status_code == 201:
                        stats['remote_sent'] += 1
                    else:
                        stats['duplicates'] += 1
                else:
                    stats['remote_failed'] += 1
            else:
                try:
                    lead, created, num_assigned = process_lead(
                        platform='nextdoor',
                        source_url=url,
                        content=content,
                        author=author,
                        raw_data={
                            'neighborhood': neighborhood,
                            'source': 'nextdoor_search',
                            'search_keyword': keyword,
                            'comment_count': comment_count,
                            'confidence': confidence,
                        },
                        source_group='social_media',
                        source_type='nextdoor',
                    )
                    if created:
                        stats['created'] += 1
                        stats['assigned'] += num_assigned
                    else:
                        stats['duplicates'] += 1
                except Exception as e:
                    logger.error(f'Error processing post: {e}')
                    stats['errors'] += 1

        # Update monitor run
        run.status = 'success'
        run.finished_at = timezone.now()
        run.results = stats
        run.save()

    except Exception as e:
        logger.error(f'Nextdoor search monitor error: {e}')
        stats['error'] = str(e)
        run.status = 'error'
        run.finished_at = timezone.now()
        run.error_message = str(e)[:500]
        run.save()

    return stats
