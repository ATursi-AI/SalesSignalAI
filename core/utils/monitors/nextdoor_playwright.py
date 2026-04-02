"""
Nextdoor monitor using Playwright headless browser.

Logs into Nextdoor via headless Chromium, scrolls the feed and searches
for service-request posts, then processes them through the standard
lead pipeline.

Features:
- Cookie persistence for session reuse (browser_data/nextdoor_cookies.json)
- Anti-detection: random viewports, scroll speeds, mouse movements, delays
- Feed scrolling + keyword search
- Max 2 runs per day cooldown
- --remote flag for VPS ingestion
- --headed flag for debugging (visible browser)
"""
import json
import logging
import random
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote_plus

from django.conf import settings
from django.utils import timezone

from core.models.monitoring import MonitorRun
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(settings.BASE_DIR)
COOKIE_FILE = BASE_DIR / 'browser_data' / 'nextdoor_cookies.json'

# Cooldown: max 2 runs per day (12 hours between runs)
COOLDOWN_HOURS = 12

# Default search terms for finding service requests
DEFAULT_SEARCH_TERMS = [
    'recommend plumber',
    'need electrician',
    'looking for contractor',
    'handyman recommendation',
    'need a roofer',
    'hvac repair',
    'landscaper recommendation',
    'house cleaning',
    'painter needed',
    'pest control',
]

# Service signals for filtering posts (reuse from apify_nextdoor)
SERVICE_SIGNALS = [
    'looking for', 'need a', 'anyone know', 'recommend', 'recommendation',
    'who do you use', 'referral', 'reliable', 'affordable',
    'plumber', 'electrician', 'hvac', 'contractor', 'handyman',
    'landscap', 'cleaning', 'cleaner', 'roofer', 'painter',
    'flooring', 'drywall', 'pest control', 'exterminator',
    'mold', 'water damage', 'repair', 'renovation',
    'kitchen remodel', 'bathroom remodel', 'basement',
    'gutter', 'siding', 'fence', 'deck', 'patio',
    'tree service', 'tree removal', 'snow removal',
    'moving company', 'movers', 'junk removal',
    'locksmith', 'garage door', 'window replacement',
    'looking to hire', 'need help finding',
    'any suggestions', 'can anyone recommend',
    'does anyone have a good',
    'quote', 'estimate',
    'help needed', 'urgent', 'emergency',
]

# Random user agents for anti-detection
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
]


def _is_service_request(text):
    """Check if post text matches service-request signals."""
    text_lower = text.lower()
    return any(s in text_lower for s in SERVICE_SIGNALS)


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
    """Check if we're within the cooldown period. Returns remaining minutes or 0."""
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='nextdoor_playwright', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        cooldown = timedelta(hours=COOLDOWN_HOURS)
        if elapsed < cooldown:
            remaining = int((cooldown - elapsed).total_seconds() / 60)
            return remaining
    return 0


def _random_viewport():
    """Return a random but realistic viewport size."""
    viewports = [
        (1920, 1080), (1366, 768), (1536, 864),
        (1440, 900), (1280, 720), (1600, 900),
    ]
    return random.choice(viewports)


async def _human_scroll(page, scrolls=3):
    """Scroll the page like a human — variable speed, with pauses."""
    for _ in range(scrolls):
        scroll_amount = random.randint(300, 700)
        await page.mouse.wheel(0, scroll_amount)
        await page.wait_for_timeout(random.randint(800, 2500))
        # Occasional mouse movement
        if random.random() > 0.5:
            x = random.randint(200, 800)
            y = random.randint(200, 600)
            await page.mouse.move(x, y)
            await page.wait_for_timeout(random.randint(200, 600))


async def _login(page, email, password):
    """Log into Nextdoor with email/password."""
    logger.info('Logging into Nextdoor...')

    await page.goto('https://nextdoor.com/login/', wait_until='domcontentloaded')
    await page.wait_for_timeout(random.randint(2000, 4000))

    # Try to find and fill email field
    email_selectors = [
        'input[name="email"]',
        'input[type="email"]',
        '#email',
        'input[id*="email"]',
        'input[placeholder*="Email"]',
        'input[placeholder*="email"]',
    ]
    email_input = None
    for sel in email_selectors:
        try:
            email_input = await page.wait_for_selector(sel, timeout=3000)
            if email_input:
                break
        except Exception:
            continue

    if not email_input:
        logger.error('Could not find email input field')
        return False

    # Type email with human-like delays
    await email_input.click()
    await page.wait_for_timeout(random.randint(200, 500))
    await email_input.type(email, delay=random.randint(50, 120))
    await page.wait_for_timeout(random.randint(300, 800))

    # Find and fill password field
    password_selectors = [
        'input[name="password"]',
        'input[type="password"]',
        '#password',
        'input[id*="password"]',
    ]
    password_input = None
    for sel in password_selectors:
        try:
            password_input = await page.wait_for_selector(sel, timeout=3000)
            if password_input:
                break
        except Exception:
            continue

    if not password_input:
        logger.error('Could not find password input field')
        return False

    await password_input.click()
    await page.wait_for_timeout(random.randint(200, 500))
    await password_input.type(password, delay=random.randint(50, 120))
    await page.wait_for_timeout(random.randint(500, 1000))

    # Find and click sign-in button
    signin_selectors = [
        'button[type="submit"]',
        'button[data-testid="submit-button"]',
        'button:has-text("Sign in")',
        'button:has-text("Log in")',
        'input[type="submit"]',
    ]
    for sel in signin_selectors:
        try:
            btn = await page.wait_for_selector(sel, timeout=2000)
            if btn:
                await btn.click()
                break
        except Exception:
            continue

    # Wait for navigation after login
    try:
        await page.wait_for_url('**/feed/**', timeout=15000)
        logger.info('Login successful — landed on feed')
        return True
    except Exception:
        pass

    # Check if we're on a page that indicates success
    await page.wait_for_timeout(3000)
    current_url = page.url
    if 'feed' in current_url or 'news_feed' in current_url:
        logger.info('Login successful — on feed page')
        return True
    if 'login' not in current_url and 'auth' not in current_url:
        logger.info(f'Login appears successful — redirected to {current_url}')
        return True

    logger.error(f'Login may have failed — current URL: {page.url}')
    return False


async def _check_session(page):
    """Check if saved cookies give us a valid session."""
    logger.info('Checking saved session...')
    await page.goto('https://nextdoor.com/feed/', wait_until='domcontentloaded')
    await page.wait_for_timeout(3000)

    current_url = page.url
    if 'login' in current_url or 'auth' in current_url:
        logger.info('Session expired — need to log in again')
        return False

    # If we're on feed, session is valid
    if 'feed' in current_url or 'news_feed' in current_url or 'neighborhood' in current_url:
        logger.info('Session valid — on feed page')
        return True

    logger.info('Session status unclear — will try to proceed')
    return True


async def _extract_posts_from_page(page, max_posts=20):
    """
    Extract posts from the current page using a multi-strategy approach.
    Tries specific selectors first, then falls back to grabbing text blocks
    from the main content area.
    """
    posts = []

    # Scroll to load more content
    await _human_scroll(page, scrolls=5)
    await page.wait_for_timeout(2000)

    # Strategy 1: Try specific post container selectors
    post_selectors = [
        # Common Nextdoor patterns
        'article',
        '[data-testid*="post"]',
        '[data-testid*="story"]',
        '[data-testid*="feed-item"]',
        # Class-based patterns
        'div[class*="Post"]',
        'div[class*="post-"]',
        'div[class*="story"]',
        'div[class*="FeedCard"]',
        'div[class*="feed-card"]',
        'div[class*="Posting"]',
        # Generic structural
        'main article',
        'main > div > div > div',
    ]

    post_elements = []
    winning_selector = None
    for sel in post_selectors:
        try:
            elements = await page.query_selector_all(sel)
            if elements and len(elements) >= 2:
                # Verify these are real content blocks (not tiny UI elements)
                sample_text = await elements[0].inner_text()
                if sample_text and len(sample_text.strip()) > 30:
                    post_elements = elements
                    winning_selector = sel
                    logger.info(f'Found {len(elements)} posts with selector: {sel}')
                    break
        except Exception:
            continue

    # Strategy 2: If no structured selectors work, find the main feed container
    # and split its content into post-sized chunks
    if not post_elements:
        logger.info('Specific selectors failed — trying main content container fallback')
        container_selectors = [
            'main',
            '#__next main',
            '[role="main"]',
            '#content',
            'div[class*="feed"]',
            'div[class*="Feed"]',
            'body',
        ]
        for sel in container_selectors:
            try:
                container = await page.query_selector(sel)
                if not container:
                    continue

                # Get all direct children that have substantial text
                children = await container.query_selector_all(':scope > div')
                if not children or len(children) < 2:
                    children = await container.query_selector_all('div')

                substantial = []
                for child in children:
                    try:
                        text = await child.inner_text()
                        if text and len(text.strip()) > 50 and len(text.strip()) < 5000:
                            substantial.append(child)
                    except Exception:
                        continue

                if len(substantial) >= 2:
                    post_elements = substantial
                    winning_selector = f'{sel} > div (fallback)'
                    logger.info(f'Fallback found {len(substantial)} content blocks via: {sel}')
                    break
            except Exception:
                continue

    # Strategy 3: Last resort — dump all page text and split by newline clusters
    if not post_elements:
        logger.info('Container fallback failed — extracting raw page text')
        try:
            page_text = await page.inner_text('body')
            if page_text:
                # Split on double-newlines (paragraph breaks)
                chunks = [c.strip() for c in page_text.split('\n\n') if len(c.strip()) > 50]
                for chunk in chunks[:max_posts]:
                    posts.append({
                        'text': chunk[:2000],
                        'author': '',
                        'neighborhood': '',
                        'url': page.url,
                        'timestamp': None,
                        'extraction': 'raw_text',
                    })
                logger.info(f'Raw text extraction: {len(posts)} chunks')
        except Exception as e:
            logger.warning(f'Raw text extraction failed: {e}')
        return posts

    # Extract data from found elements
    logger.info(f'Extracting from {len(post_elements[:max_posts])} elements ({winning_selector})')
    for element in post_elements[:max_posts]:
        try:
            post = await _extract_single_post(element, page)
            if post and post.get('text') and len(post['text']) >= 20:
                post['extraction'] = winning_selector
                posts.append(post)
        except Exception as e:
            logger.debug(f'Error extracting post: {e}')
            continue

    return posts


async def _extract_single_post(element, page):
    """Extract data from a single post element."""
    post = {
        'text': '',
        'author': '',
        'neighborhood': '',
        'url': '',
        'timestamp': None,
    }

    # Extract text — try structured first, fall back to full inner_text
    text_parts = []

    # Try title/subject
    for sel in ['h2', 'h3', 'h4', '[class*="title"]', '[class*="subject"]', '[class*="Title"]']:
        try:
            title_el = await element.query_selector(sel)
            if title_el:
                title_text = await title_el.inner_text()
                if title_text and title_text.strip():
                    text_parts.append(title_text.strip())
                break
        except Exception:
            continue

    # Try body content
    for sel in ['[class*="body"]', '[class*="Body"]', '[class*="content"]', '[class*="Content"]', 'p']:
        try:
            body_els = await element.query_selector_all(sel)
            for body_el in body_els[:3]:
                body_text = await body_el.inner_text()
                if body_text and body_text.strip() and len(body_text.strip()) > 10:
                    text_parts.append(body_text.strip())
        except Exception:
            continue

    if not text_parts:
        # Fallback: get all inner text from the element
        try:
            all_text = await element.inner_text()
            if all_text and all_text.strip():
                text_parts.append(all_text.strip())
        except Exception:
            return None

    if not text_parts:
        return None

    post['text'] = '\n'.join(text_parts)[:2000]

    # Extract author
    for sel in ['[class*="author"]', '[class*="Author"]', '[class*="name"]',
                '[class*="Name"]', '[class*="Actor"]', '[class*="actor"]']:
        try:
            author_el = await element.query_selector(sel)
            if author_el:
                author = await author_el.inner_text()
                if author and len(author) < 100:
                    post['author'] = author.strip().split('\n')[0]
                    break
        except Exception:
            continue

    # Extract neighborhood/location
    for sel in ['[class*="neighborhood"]', '[class*="Neighborhood"]',
                '[class*="location"]', '[class*="Location"]',
                '[class*="subtitle"]', '[class*="Subtitle"]',
                '[class*="byline"]', '[class*="Byline"]']:
        try:
            loc_el = await element.query_selector(sel)
            if loc_el:
                loc = await loc_el.inner_text()
                if loc and len(loc) < 200:
                    post['neighborhood'] = loc.strip().split('\n')[0]
                    break
        except Exception:
            continue

    # Extract URL — try multiple link patterns
    for link_sel in ['a[href*="/p/"]', 'a[href*="/post/"]', 'a[href*="/news/"]']:
        try:
            link = await element.query_selector(link_sel)
            if link:
                href = await link.get_attribute('href')
                if href:
                    if href.startswith('/'):
                        href = f'https://nextdoor.com{href}'
                    post['url'] = href
                    break
        except Exception:
            continue

    if not post['url']:
        post['url'] = page.url

    return post


async def _search_posts(page, search_terms, max_posts=10):
    """Search Nextdoor for specific terms and extract matching posts."""
    posts = []

    for term in search_terms:
        if len(posts) >= max_posts:
            break

        try:
            logger.info(f'Searching Nextdoor for: "{term}"')

            # Navigate to search — use domcontentloaded instead of networkidle
            # to avoid timeout/CancelledError on long-polling connections
            search_url = f'https://nextdoor.com/search/?query={quote_plus(term)}'
            await page.goto(search_url, wait_until='domcontentloaded')
            await page.wait_for_timeout(random.randint(3000, 5000))

            # Scroll to load results
            await _human_scroll(page, scrolls=2)

            # Extract posts from search results
            search_posts = await _extract_posts_from_page(page, max_posts=5)
            for sp in search_posts:
                sp['search_term'] = term
                posts.append(sp)

            logger.info(f'  Found {len(search_posts)} posts for "{term}"')

            # Human-like delay between searches
            await page.wait_for_timeout(random.randint(3000, 6000))

        except Exception as e:
            logger.warning(f'Search failed for "{term}": {e}')
            continue

    return posts


async def _run_browser(email, password, search_terms, max_posts=20,
                       dry_run=False, headed=False):
    """
    Main browser automation flow:
    1. Launch browser with anti-detection
    2. Load cookies or login
    3. Scroll feed + search for keywords
    4. Extract and return posts
    """
    from playwright.async_api import async_playwright

    width, height = _random_viewport()
    user_agent = random.choice(USER_AGENTS)

    posts = []

    async with async_playwright() as p:
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
        ]

        browser = await p.chromium.launch(
            headless=not headed,
            args=launch_args,
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
        if saved_cookies:
            try:
                await context.add_cookies(saved_cookies)
                session_valid = await _check_session(page)
            except Exception as e:
                logger.warning(f'Error loading cookies: {e}')
                session_valid = False
        else:
            session_valid = False

        # Login if needed
        if not session_valid:
            if not email or not password:
                logger.error('No Nextdoor credentials configured — set NEXTDOOR_EMAIL and NEXTDOOR_PASSWORD')
                await browser.close()
                return []

            login_ok = await _login(page, email, password)
            if not login_ok:
                logger.error('Nextdoor login failed')
                await browser.close()
                return []

            # Save cookies for next run
            cookies = await context.cookies()
            _save_cookies(cookies)

        # Phase 1: Scroll feed for posts
        logger.info('Phase 1: Scrolling feed...')
        try:
            await page.goto('https://nextdoor.com/feed/', wait_until='domcontentloaded')
            await page.wait_for_timeout(random.randint(3000, 5000))
            feed_posts = await _extract_posts_from_page(page, max_posts=max_posts // 2)
            posts.extend(feed_posts)
            logger.info(f'Extracted {len(feed_posts)} posts from feed')
        except Exception as e:
            logger.warning(f'Feed extraction error: {e}')

        # Phase 2: Search for service keywords
        remaining = max_posts - len(posts)
        if remaining > 0 and search_terms:
            logger.info(f'Phase 2: Searching for {len(search_terms)} terms...')
            search_posts = await _search_posts(page, search_terms, max_posts=remaining)
            posts.extend(search_posts)
            logger.info(f'Extracted {len(search_posts)} posts from search')

        # Save updated cookies
        try:
            cookies = await context.cookies()
            _save_cookies(cookies)
        except Exception:
            pass

        await browser.close()

    return posts


def _post_lead_remote(ingest_url, api_key, lead_data):
    """POST a lead to a remote SalesSignal instance via the ingest API."""
    import requests as req

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = req.post(
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
    except Exception as e:
        logger.error(f'[Remote] POST failed: {e}')
        return False, 0, {'error': str(e)}


def monitor_nextdoor_playwright(
    search_terms=None,
    max_posts=20,
    dry_run=False,
    remote=False,
    force=False,
    headed=False,
):
    """
    Monitor Nextdoor for service request posts using Playwright.

    Args:
        search_terms: list of search queries (default: DEFAULT_SEARCH_TERMS)
        max_posts: maximum posts to extract per run (default: 20)
        dry_run: log matches without creating Lead records
        remote: POST leads to remote ingest API instead of saving locally
        force: bypass cooldown timer
        headed: launch visible browser for debugging

    Returns:
        dict with counts: posts_found, service_matches, created, duplicates,
                         assigned, errors, remote_sent, remote_failed
    """
    import asyncio

    stats = {
        'posts_found': 0,
        'service_matches': 0,
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
        reason = f'nextdoor_playwright cooldown: {remaining}m remaining'
        logger.info(reason)
        stats['skipped_reason'] = reason
        return stats

    # Get credentials
    email = getattr(settings, 'NEXTDOOR_EMAIL', '')
    password = getattr(settings, 'NEXTDOOR_PASSWORD', '')

    if not email or not password:
        logger.error('NEXTDOOR_EMAIL and NEXTDOOR_PASSWORD must be set in .env')
        stats['error'] = 'credentials_not_configured'
        return stats

    # Remote config
    ingest_url = None
    api_key = None
    if remote:
        ingest_url = getattr(settings, 'REMOTE_INGEST_URL', '')
        api_key = getattr(settings, 'INGEST_API_KEY', '')
        if not ingest_url or not api_key:
            logger.error('REMOTE_INGEST_URL and INGEST_API_KEY must be set for --remote')
            stats['error'] = 'remote_not_configured'
            return stats

    if search_terms is None:
        search_terms = DEFAULT_SEARCH_TERMS

    # Record monitor run start
    run = MonitorRun.objects.create(
        monitor_name='nextdoor_playwright',
        status='running',
        started_at=timezone.now(),
    )

    try:
        # Run the async browser automation — always use asyncio.run()
        # for a clean event loop (avoids CancelledError from stale loops)
        posts = asyncio.run(
            _run_browser(email, password, search_terms, max_posts, dry_run, headed)
        )

        stats['posts_found'] = len(posts)
        logger.info(f'Total posts extracted: {len(posts)}')

        # Deduplicate by text content
        seen_texts = set()
        unique_posts = []
        for post in posts:
            text_key = post['text'][:200].strip().lower()
            if text_key not in seen_texts:
                seen_texts.add(text_key)
                unique_posts.append(post)

        # Process posts
        for post in unique_posts:
            text = post.get('text', '')
            author = post.get('author', '')
            neighborhood = post.get('neighborhood', '')
            url = post.get('url', 'https://nextdoor.com')

            # Filter for service requests
            if not _is_service_request(text):
                continue

            stats['service_matches'] += 1

            content = text[:2000]
            if neighborhood:
                content += f'\n(Neighborhood: {neighborhood})'

            if dry_run:
                logger.info(f'[DRY RUN] Service request: {text[:100]}')
                stats['created'] += 1
                continue

            if remote:
                lead_data = {
                    'platform': 'nextdoor',
                    'source_url': url,
                    'source_content': content,
                    'author': author,
                }
                ok, status_code, body = _post_lead_remote(ingest_url, api_key, lead_data)
                if ok:
                    if status_code == 201:
                        stats['remote_sent'] += 1
                        logger.info(f'[Remote] Lead sent: {text[:60]}')
                    else:
                        stats['duplicates'] += 1
                else:
                    stats['remote_failed'] += 1
                    logger.warning(f'[Remote] Failed ({status_code}): {body}')
            else:
                try:
                    lead, created, num_assigned = process_lead(
                        platform='nextdoor',
                        source_url=url,
                        content=content,
                        author=author,
                        raw_data={
                            'neighborhood': neighborhood,
                            'source': 'playwright',
                            'search_term': post.get('search_term', ''),
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
        logger.error(f'Nextdoor Playwright monitor error: {e}')
        stats['error'] = str(e)
        run.status = 'error'
        run.finished_at = timezone.now()
        run.error_message = str(e)[:500]
        run.save()

    return stats
