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
import asyncio
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


def _print(msg):
    """Print with safe encoding for Windows console."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', errors='replace').decode('ascii'))

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
    'recommendations for', 'recommendation for', 'recommendations?',
    'any suggestion', 'who should i call', 'who can i call',
    'help me find', 'trying to find', 'searching for',
    'any good', 'know of any', 'know any', 'anyone recommend',
    'please share', 'please suggest', 'who would you',
    'who do you recommend', 'need to find', 'need to get',
    'need to hire', 'can someone recommend', 'can you recommend',
    'needs to be', 'needs replacing', 'needs repair',
    'who has a good', 'does anyone know',
    'in need of', 'we need', 'i need',
    'need licensed', 'need certified',
    'need someone to', 'need somebody to',
    'need to replace', 'need to install', 'need to fix', 'need to repair',
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
    'i work cleaning', 'i offer', 'i provide', 'my name is',
    'we provide', 'i am a', "i'm a licensed", "i'm a certified",
    'years of experience', 'contact me', 'text me',
    'book now', 'book your', 'schedule your', 'schedule a',
    'visit our', 'visit us', 'check us out',
    'hire me', 'hire us', 'affordable rates', 'competitive rates',
    'satisfaction guaranteed', 'licensed and insured', 'fully insured',
]

# User agents for anti-detection
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15',
]


def _is_request_post(text, keyword='', verbose=False):
    """Return True if text is a service REQUEST (not a response/ad).

    When verbose=True, returns (bool, reason_str) tuple instead.
    """
    text_lower = text.lower()

    # Find matching signals
    matched_request = [sig for sig in REQUEST_SIGNALS if sig in text_lower]
    matched_response = [sig for sig in RESPONSE_SIGNALS if sig in text_lower]

    # If dominated by response/self-promo signals, reject immediately
    if len(matched_response) >= 2 and len(matched_response) > len(matched_request):
        reason = (
            f'REJECTED: self-promo/response ({len(matched_response)}: {matched_response[:3]}) '
            f'vs request ({len(matched_request)}: {matched_request[:3]})'
        )
        if verbose:
            return False, reason
        return False

    # If any response signal but zero request signals, it's a response/ad
    if matched_response and not matched_request:
        reason = f'REJECTED: response-only post. Response signals: {matched_response[:3]}'
        if verbose:
            return False, reason
        return False

    # Accept if has at least one request signal
    if matched_request:
        reason = f'ACCEPTED: request signals={matched_request[:3]}, response signals={matched_response[:3]}'
        if verbose:
            return True, reason
        return True

    # No signals at all — reject
    reason = 'REJECTED: no request or response signals found'
    if verbose:
        return False, reason
    return False


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
      1. Navigate to about:blank to reset SPA state
      2. Navigate to search URL (full page load)
      3. Click "Posts" tab to filter
      4. Scroll and extract posts
    """
    search_query = f'{keyword} recommendations'
    search_url = f'https://nextdoor.com/search/?query={quote_plus(search_query)}'

    logger.info(f'  Searching: "{search_query}"')

    # ── Reset SPA state by navigating to blank page first ──
    try:
        await page.goto('about:blank', wait_until='domcontentloaded')
        await page.wait_for_timeout(500)
    except Exception:
        pass

    # ── Navigate to search URL (forces full page load after blank) ──
    try:
        await page.goto(search_url, wait_until='domcontentloaded', timeout=20000)
    except Exception as e:
        logger.warning(f'  Navigation failed for "{keyword}": {e}')
        return []
    await _human_delay(page, 3000, 5000)

    current_url = page.url
    _print(f'  [{keyword}] Page URL: {current_url}')

    # ── Wait for search results to render, then click "Posts" tab ──
    posts_tab_clicked = False
    # Wait for any dwell-tracker or tab to appear (sign that search rendered)
    try:
        await page.wait_for_selector(
            '[data-testid="tab-posts"], [data-testid^="dwell-tracker"]',
            timeout=10000
        )
    except Exception:
        _print(f'  [{keyword}] Search results slow to render — reloading page...')
        # Force a full page reload to re-trigger server-side rendering
        try:
            await page.reload(wait_until='domcontentloaded', timeout=15000)
            await _human_delay(page, 3000, 5000)
            await page.wait_for_selector(
                '[data-testid="tab-posts"], [data-testid^="dwell-tracker"]',
                timeout=10000
            )
        except Exception:
            _print(f'  [{keyword}] Still no results after reload')
            await _human_delay(page, 2000, 3000)

    posts_tab_selectors = [
        '[data-testid="tab-posts"]',
        'button:has-text("Posts")',
        'a:has-text("Posts")',
        '[role="tab"]:has-text("Posts")',
        'div[class*="Tab"]:has-text("Posts")',
    ]
    for sel in posts_tab_selectors:
        try:
            tab = await page.wait_for_selector(sel, timeout=3000)
            if tab:
                await tab.click()
                await _human_delay(page, 2000, 3500)
                _print(f'  [{keyword}] Clicked "Posts" tab via {sel}')
                posts_tab_clicked = True
                break
        except Exception:
            continue
    if not posts_tab_clicked:
        _print(f'  [{keyword}] WARNING: Could not click Posts tab')

    # ── Scroll to load more results (infinite scroll) ──
    # Count dwell-tracker items before/after scrolling to detect new content
    count_js = """
        () => document.querySelectorAll('[data-testid^="dwell-tracker-searchFeedItem"]').length
    """

    prev_count = await page.evaluate(count_js)
    _print(f'  [{keyword}] Posts before scroll: {prev_count}')

    for scroll_i in range(5):
        # Use keyboard End key — works regardless of which element is the scroll container
        await page.keyboard.press('End')
        # Wait for new content to load
        await _human_delay(page, 2000, 3000)
        # Also try mouse wheel as backup
        await page.mouse.wheel(0, 3000)
        await _human_delay(page, 1500, 2500)
        # Occasional mouse movement for anti-detection
        if random.random() > 0.5:
            await page.mouse.move(random.randint(200, 800), random.randint(200, 600))

        new_count = await page.evaluate(count_js)
        _print(f'  [{keyword}] Scroll {scroll_i+1}/5: posts {prev_count} -> {new_count}')
        if new_count == prev_count:
            # No new content — wait longer and try once more
            await _human_delay(page, 2500, 3500)
            await page.keyboard.press('End')
            await _human_delay(page, 2000, 3000)
            new_count = await page.evaluate(count_js)
            if new_count == prev_count:
                _print(f'  [{keyword}]   No more content')
                break
        prev_count = new_count

    # ── DOM inspection: find all post containers ──
    # Dump counts for all candidate selectors so we can see what works
    post_selectors = [
        'article',
        '[data-testid*="post"]',
        '[data-testid*="story"]',
        '[data-testid*="search-result"]',
        '[data-testid*="result"]',
        'div[class*="Post"]',
        'div[class*="post-"]',
        'div[class*="story"]',
        'div[class*="Story"]',
        'div[class*="SearchResult"]',
        'div[class*="search-result"]',
        'div[class*="FeedCard"]',
        'div[class*="feed-card"]',
        'div[class*="ContentCard"]',
        'div[class*="content-card"]',
        'div[class*="Card"]',
    ]

    _print(f'  [{keyword}] DOM selector counts:')

    # Best selector: dwell-tracker items are the actual search result cards
    dwell_selectors = await page.query_selector_all('[data-testid^="dwell-tracker-searchFeedItem"]')
    _print(f'    dwell-tracker-searchFeedItem: {len(dwell_selectors)}')

    elements = []
    winning_sel = None

    if len(dwell_selectors) >= 2:
        elements = dwell_selectors
        winning_sel = 'dwell-tracker-searchFeedItem'
    else:
        # Try generic selectors
        for sel in post_selectors:
            try:
                found = await page.query_selector_all(sel)
                count = len(found) if found else 0
                if count > 0:
                    _print(f'    {sel}: {count}')
                # Pick the best: at least 2 elements with real content
                if found and count >= 2 and not elements:
                    real_count = 0
                    for sample_el in found[:5]:
                        try:
                            sample = await sample_el.inner_text()
                            if sample and len(sample.strip()) > 50:
                                real_count += 1
                        except Exception:
                            continue
                    if real_count >= 2:
                        elements = found
                        winning_sel = sel
            except Exception:
                continue

    if elements:
        _print(f'  [{keyword}] Using selector: {winning_sel} ({len(elements)} elements)')
    else:
        # Fallback: inspect the actual DOM structure
        _print(f'  [{keyword}] No structured selectors matched — trying DOM walk')

        # Dump top-level data-testid values to discover Nextdoor's schema
        try:
            testids = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('[data-testid]');
                    const ids = new Set();
                    els.forEach(el => ids.add(el.getAttribute('data-testid')));
                    return Array.from(ids).slice(0, 30);
                }
            """)
            if testids:
                _print(f'  [{keyword}] data-testid values on page: {testids}')
        except Exception:
            pass

        # Dump class names that appear on many elements (likely post containers)
        try:
            class_counts = await page.evaluate("""
                () => {
                    const counts = {};
                    document.querySelectorAll('div[class]').forEach(el => {
                        el.classList.forEach(cls => {
                            if (cls.length > 3 && cls.length < 50) {
                                counts[cls] = (counts[cls] || 0) + 1;
                            }
                        });
                    });
                    // Return classes with 3-30 occurrences (likely repeated post containers)
                    return Object.entries(counts)
                        .filter(([_, c]) => c >= 3 && c <= 50)
                        .sort((a, b) => b[1] - a[1])
                        .slice(0, 20)
                        .map(([cls, c]) => `${cls}: ${c}`);
                }
            """)
            if class_counts:
                _print(f'  [{keyword}] Repeated CSS classes (3-50 occurrences):')
                for cc in class_counts:
                    _print(f'    {cc}')
        except Exception:
            pass

        # Fallback: walk main content for text blocks
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
                    elements = substantial
                    _print(f'  [{keyword}] Fallback: {len(elements)} text blocks via {sel}')
                    break
        except Exception:
            pass

    # ── Junk patterns — navigation, menus, browser warnings ──
    JUNK_PATTERNS = [
        'you\'re using an old browser',
        'upgrade to one of the supported',
        'home for sale & free local news',
        'most relevant distance all time',
        'all posts businesses pages',
        'post settings help center',
        'invite neighbors',
        'sign up free',
        'download the app',
        'cookie policy',
        'privacy policy',
        'terms of service',
    ]

    # ── Parse each element ──
    posts = []
    for element in elements[:40]:
        try:
            post = await _extract_post(element, page, keyword)
            if not post:
                continue
            # Filter out navigation/UI elements
            text_lower = post['text'].lower()
            if any(junk in text_lower for junk in JUNK_PATTERNS):
                continue
            # Must have at least 100 chars of real content
            if len(post['text'].strip()) < 100:
                continue
            posts.append(post)
        except Exception:
            continue

    _print(f'  [{keyword}] Final: {len(posts)} valid posts from {len(elements)} elements')
    stats['items_scraped'] += len(posts)
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

        # Search each keyword — reuse the same page with about:blank reset
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

        # Process posts — verbose logging for first 10
        for idx, post in enumerate(unique_posts):
            text = post.get('text', '')
            author = post.get('author', '')
            neighborhood = post.get('neighborhood', '')
            url = post.get('url', 'https://nextdoor.com')
            keyword = post.get('keyword', '')
            comment_count = post.get('comment_count', 0)

            # Filter: keep only REQUEST posts (verbose for first 10)
            is_request, reason = _is_request_post(text, keyword=keyword, verbose=True)

            if idx < 10:
                preview = text.replace('\n', ' ')[:200]
                _print(f'\n--- POST {idx+1} [{keyword}] ---')
                _print(f'  Author: {author or "(none)"}')
                _print(f'  Text: {preview}')
                _print(f'  Verdict: {reason}')

            if not is_request:
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
