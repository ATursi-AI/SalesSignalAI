"""
Facebook Groups monitor for SalesSignal AI.

Uses Playwright browser automation with a dedicated Facebook account to scrape
recent posts from joined groups that match service request keywords.

IMPORTANT: This monitor is inherently fragile. Facebook's DOM changes frequently.
Expect maintenance. Aggressive rate limiting is enforced to minimize detection risk.

Requirements:
    pip install playwright
    python -m playwright install chromium

Environment variables:
    FACEBOOK_EMAIL    — login email for the dedicated FB account
    FACEBOOK_PASSWORD — login password
"""
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from core.models.monitoring import MonitoredFacebookGroup
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# Path to persist browser cookies between runs
COOKIE_PATH = Path(settings.BASE_DIR) / '.fb_cookies.json'

# Aggressive rate limiting
MIN_DELAY = 10   # seconds
MAX_DELAY = 18   # seconds
PAGE_LOAD_WAIT = 5  # seconds after navigation
SCROLL_WAIT = 4     # seconds between scrolls
MAX_POSTS_PER_SESSION = 50
MAX_POSTS_PER_GROUP = 15

# Service request signals
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
]


def _random_delay():
    """Sleep a random interval to mimic human behaviour."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def is_service_request(text, extra_keywords=None):
    """Check if a post text matches service-request signals."""
    text_lower = text.lower()
    signals = SERVICE_SIGNALS
    if extra_keywords:
        signals = signals + [k.lower() for k in extra_keywords]
    return any(s in text_lower for s in signals)


def _save_cookies(context):
    """Persist browser cookies to disk so we can skip re-login."""
    try:
        cookies = context.cookies()
        COOKIE_PATH.write_text(json.dumps(cookies, indent=2))
        logger.info('Facebook cookies saved')
    except Exception as e:
        logger.warning(f'Failed to save cookies: {e}')


def _load_cookies(context):
    """Load previously saved cookies into the browser context."""
    if not COOKIE_PATH.exists():
        return False
    try:
        cookies = json.loads(COOKIE_PATH.read_text())
        context.add_cookies(cookies)
        logger.info('Facebook cookies loaded from disk')
        return True
    except Exception as e:
        logger.warning(f'Failed to load cookies: {e}')
        return False


def _login(page, email, password):
    """
    Log in to Facebook. Returns True on success.
    Handles the standard login form and the cookie-consent dialog.
    """
    logger.info('Navigating to Facebook login...')
    page.goto('https://www.facebook.com/', wait_until='domcontentloaded')
    time.sleep(PAGE_LOAD_WAIT)

    # Accept cookie banner if present
    try:
        accept_btn = page.locator(
            'button[data-cookiebanner="accept_button"], '
            'button[title="Allow all cookies"], '
            'button:has-text("Accept All")'
        )
        if accept_btn.count() > 0:
            accept_btn.first.click()
            time.sleep(2)
    except Exception:
        pass

    # Check if already logged in
    if page.url and '/login' not in page.url.lower():
        # Check for a logged-in indicator
        try:
            if page.locator('[aria-label="Your profile"], [aria-label="Account"]').count() > 0:
                logger.info('Already logged in via cookies')
                return True
        except Exception:
            pass

    logger.info('Performing Facebook login...')
    try:
        page.fill('input#email, input[name="email"]', email)
        time.sleep(1)
        page.fill('input#pass, input[name="pass"]', password)
        time.sleep(1)
        page.click('button[name="login"], button[type="submit"]')
        time.sleep(PAGE_LOAD_WAIT + 3)

        # Check for checkpoint / 2FA
        if 'checkpoint' in page.url.lower():
            logger.error('Facebook checkpoint/2FA detected — manual intervention required')
            return False

        if 'login' in page.url.lower():
            logger.error('Facebook login failed — still on login page')
            return False

        logger.info('Facebook login successful')
        return True
    except Exception as e:
        logger.error(f'Facebook login error: {e}')
        return False


def _scrape_group_posts(page, group_url, max_posts=MAX_POSTS_PER_GROUP):
    """
    Navigate to a Facebook group and extract recent post texts.
    Returns list of dicts: {text, author, url, timestamp}.
    """
    logger.info(f'Navigating to group: {group_url}')
    try:
        page.goto(group_url, wait_until='domcontentloaded')
    except Exception as e:
        logger.error(f'Failed to load group {group_url}: {e}')
        return []

    time.sleep(PAGE_LOAD_WAIT + random.uniform(1, 3))

    posts = []
    seen_texts = set()

    # Scroll to load posts (2-3 scrolls)
    for scroll_i in range(3):
        # Extract posts from the current page state
        try:
            # Facebook post containers — selectors change frequently
            post_elements = page.locator(
                '[role="article"], '
                'div[data-ad-preview="message"], '
                '.userContentWrapper, '
                'div.x1yztbdb'  # newer FB layout
            ).all()

            for el in post_elements:
                if len(posts) >= max_posts:
                    break
                try:
                    text = el.inner_text(timeout=3000)
                    if not text or len(text) < 20:
                        continue

                    # Deduplicate within this scrape
                    text_key = text[:100]
                    if text_key in seen_texts:
                        continue
                    seen_texts.add(text_key)

                    # Try to find the post permalink
                    post_url = ''
                    try:
                        link = el.locator(
                            'a[href*="/posts/"], '
                            'a[href*="/permalink/"], '
                            'a[href*="story_fbid"]'
                        ).first
                        if link.count() > 0:
                            post_url = link.get_attribute('href', timeout=2000) or ''
                            if post_url and not post_url.startswith('http'):
                                post_url = 'https://www.facebook.com' + post_url
                    except Exception:
                        pass

                    # Try to find author name
                    author = ''
                    try:
                        author_el = el.locator(
                            'strong a, '
                            'h3 a, '
                            'a[role="link"] span'
                        ).first
                        if author_el.count() > 0:
                            author = author_el.inner_text(timeout=2000)
                    except Exception:
                        pass

                    # Truncate long posts
                    clean_text = text[:2000]

                    posts.append({
                        'text': clean_text,
                        'author': author,
                        'url': post_url or group_url,
                        'timestamp': None,  # FB doesn't expose clean timestamps easily
                    })

                except Exception:
                    continue

        except Exception as e:
            logger.debug(f'Post extraction error on scroll {scroll_i}: {e}')

        if len(posts) >= max_posts:
            break

        # Scroll down
        try:
            page.evaluate('window.scrollBy(0, window.innerHeight * 2)')
            time.sleep(SCROLL_WAIT + random.uniform(0, 2))
        except Exception:
            break

    logger.info(f'Extracted {len(posts)} posts from group')
    return posts


def monitor_facebook_groups(group_ids=None, max_per_group=MAX_POSTS_PER_GROUP,
                             max_total=MAX_POSTS_PER_SESSION, dry_run=False):
    """
    Main monitoring function. Launches a Playwright browser, logs in to Facebook,
    and scrapes configured groups for service-request posts.

    Args:
        group_ids: list of MonitoredFacebookGroup IDs (default: all active)
        max_per_group: max posts to scrape per group
        max_total: max posts across entire session
        dry_run: log matches without creating Leads

    Returns:
        dict with counts: groups_checked, posts_scraped, created, duplicates, assigned, errors
    """
    # Cooldown check (60 min for Facebook)
    from core.models.monitoring import MonitorRun
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='facebook', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=60):
            reason = f'facebook cooldown: {int((timedelta(minutes=60) - elapsed).total_seconds() / 60)}m remaining'
            logger.info(reason)
            return {'groups_checked': 0, 'posts_scraped': 0, 'created': 0,
                    'duplicates': 0, 'assigned': 0, 'errors': 0,
                    'skipped_reason': reason}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            'playwright not installed. Install with: '
            'pip install playwright && python -m playwright install chromium'
        )
        return {'error': 'playwright_not_installed'}

    fb_email = os.environ.get('FACEBOOK_EMAIL', '') or getattr(settings, 'FACEBOOK_EMAIL', '')
    fb_password = os.environ.get('FACEBOOK_PASSWORD', '') or getattr(settings, 'FACEBOOK_PASSWORD', '')

    if not fb_email or not fb_password:
        logger.error('FACEBOOK_EMAIL and FACEBOOK_PASSWORD must be set')
        return {'error': 'credentials_not_configured'}

    groups = MonitoredFacebookGroup.objects.filter(is_active=True)
    if group_ids:
        groups = groups.filter(id__in=group_ids)

    if not groups.exists():
        logger.info('No active Facebook groups to monitor')
        return {'groups_checked': 0, 'posts_scraped': 0, 'created': 0,
                'duplicates': 0, 'assigned': 0, 'errors': 0}

    stats = {
        'groups_checked': 0,
        'posts_scraped': 0,
        'created': 0,
        'duplicates': 0,
        'assigned': 0,
        'errors': 0,
    }

    total_processed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ],
        )

        context = browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ),
            locale='en-US',
        )

        # Load saved cookies
        _load_cookies(context)

        page = context.new_page()

        # Login
        if not _login(page, fb_email, fb_password):
            browser.close()
            return {'error': 'login_failed'}

        # Save cookies after successful login
        _save_cookies(context)

        for group in groups:
            if total_processed >= max_total:
                logger.info(f'Session limit reached ({max_total} posts)')
                break

            stats['groups_checked'] += 1
            remaining = min(max_per_group, max_total - total_processed)

            logger.info(f'Scanning Facebook group: {group.name}')

            _random_delay()

            posts = _scrape_group_posts(page, group.url, max_posts=remaining)
            stats['posts_scraped'] += len(posts)
            total_processed += len(posts)

            group_leads = 0

            for post in posts:
                text = post['text']

                # Check against group-specific keywords + global signals
                if not is_service_request(text, extra_keywords=group.keywords):
                    continue

                content = f"[FB Group: {group.name}]\n{text}"
                source_url = post['url']
                author = post.get('author', '')

                if dry_run:
                    logger.info(f'[DRY RUN] Would create lead: {text[:80]}')
                    stats['created'] += 1
                    group_leads += 1
                    continue

                try:
                    lead, created, num_assigned = process_lead(
                        platform='facebook',
                        source_url=source_url,
                        content=content,
                        author=author,
                        posted_at=post.get('timestamp'),
                        raw_data={
                            'group_name': group.name,
                            'group_id': group.group_id,
                            'post_text': text[:500],
                        },
                    )

                    if created:
                        stats['created'] += 1
                        stats['assigned'] += num_assigned
                        group_leads += 1
                    else:
                        stats['duplicates'] += 1

                except Exception as e:
                    logger.error(f'Error processing FB post: {e}')
                    stats['errors'] += 1

            # Update group stats
            group.last_scraped = timezone.now()
            group.posts_scraped += len(posts)
            group.leads_created += group_leads
            group.save(update_fields=['last_scraped', 'posts_scraped', 'leads_created'])

        # Save cookies at end of session
        _save_cookies(context)
        browser.close()

    logger.info(f'Facebook Groups monitor complete: {stats}')
    return stats
