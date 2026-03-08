"""
Website crawler + email extraction for finding decision-maker contacts.
Crawls a business website's contact/about pages and uses regex + Claude API
to extract owner/manager email addresses.
"""
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger(__name__)

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

# Pages likely to contain contact info
CONTACT_PATHS = [
    '/contact', '/contact-us', '/about', '/about-us',
    '/team', '/our-team', '/staff', '/leadership',
    '/get-in-touch', '/reach-us',
]

# Email regex
EMAIL_PATTERN = re.compile(
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    re.IGNORECASE,
)

# Filter out generic/junk emails
JUNK_DOMAINS = {
    'example.com', 'test.com', 'sentry.io', 'wixpress.com',
    'googleapis.com', 'w3.org', 'schema.org', 'gravatar.com',
    'wordpress.org', 'jquery.com', 'fontawesome.com',
}

JUNK_PREFIXES = {
    'noreply', 'no-reply', 'donotreply', 'mailer-daemon',
    'postmaster', 'webmaster', 'admin', 'root', 'support',
    'info@example', 'test@',
}

REQUEST_DELAY = 1.5


def extract_emails_from_website(url, use_ai=True):
    """
    Crawl a business website to find contact email addresses.

    Args:
        url: Business website URL
        use_ai: Whether to use Claude API for extraction

    Returns:
        dict with:
            emails: list of found email addresses
            owner_name: extracted owner/contact name (if found)
            source_pages: list of pages where emails were found
    """
    if not url:
        return {'emails': [], 'owner_name': '', 'source_pages': []}

    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})

    parsed = urlparse(url)
    base_url = f'{parsed.scheme}://{parsed.netloc}'

    all_emails = set()
    owner_name = ''
    source_pages = []
    page_texts = {}

    # Crawl homepage + contact pages
    pages_to_check = [url]
    for path in CONTACT_PATHS:
        pages_to_check.append(urljoin(base_url, path))

    for page_url in pages_to_check:
        try:
            resp = session.get(page_url, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Remove script/style
            for tag in soup(['script', 'style', 'noscript']):
                tag.decompose()

            text = soup.get_text(separator=' ', strip=True)
            page_texts[page_url] = text

            # Extract emails via regex
            found = EMAIL_PATTERN.findall(text)
            # Also check href="mailto:"
            for a_tag in soup.select('a[href^="mailto:"]'):
                mailto = a_tag.get('href', '').replace('mailto:', '').split('?')[0].strip()
                if mailto and '@' in mailto:
                    found.append(mailto)

            for email in found:
                email = email.lower().strip()
                if _is_valid_email(email, base_url):
                    all_emails.add(email)
                    if page_url not in source_pages:
                        source_pages.append(page_url)

            time.sleep(REQUEST_DELAY)
        except requests.RequestException:
            continue

    # Try AI extraction if enabled and we found page text
    if use_ai and page_texts:
        ai_result = _ai_extract_contact(page_texts, base_url)
        if ai_result:
            for email in ai_result.get('emails', []):
                if _is_valid_email(email, base_url):
                    all_emails.add(email.lower())
            if ai_result.get('owner_name'):
                owner_name = ai_result['owner_name']

    # Rank emails: prefer owner/personal over generic info@
    ranked = _rank_emails(list(all_emails))

    return {
        'emails': ranked,
        'owner_name': owner_name,
        'source_pages': source_pages,
    }


def _is_valid_email(email, base_url=''):
    """Filter out junk/invalid emails."""
    email = email.lower().strip()
    if len(email) < 5 or len(email) > 254:
        return False

    domain = email.split('@')[1] if '@' in email else ''
    if domain in JUNK_DOMAINS:
        return False

    local = email.split('@')[0]
    if any(email.startswith(prefix) for prefix in JUNK_PREFIXES):
        return False

    # Filter image/asset filenames that look like emails
    if domain.endswith(('.png', '.jpg', '.gif', '.svg', '.css', '.js')):
        return False

    return True


def _rank_emails(emails):
    """Rank emails: personal/owner names first, generic last."""
    generic = {'info@', 'contact@', 'hello@', 'office@', 'service@', 'sales@', 'help@'}
    personal = []
    other = []

    for email in emails:
        local = email.split('@')[0]
        if any(email.startswith(g) for g in generic):
            other.append(email)
        else:
            personal.append(email)

    return personal + other


def _ai_extract_contact(page_texts, base_url):
    """Use Claude API to extract owner name and email from page text."""
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        return None

    # Combine page texts (truncated)
    combined = ''
    for url, text in page_texts.items():
        combined += f'\n--- Page: {url} ---\n{text[:2000]}\n'
    combined = combined[:6000]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            messages=[{
                'role': 'user',
                'content': (
                    f'Extract the business owner/manager name and their email address '
                    f'from this website content. Return JSON only: '
                    f'{{"owner_name": "...", "emails": ["..."]}}\n'
                    f'If not found, return {{"owner_name": "", "emails": []}}.\n\n'
                    f'Website: {base_url}\n\n{combined}'
                ),
            }],
        )

        import json
        text = message.content[0].text.strip()
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        logger.debug(f'AI email extraction failed for {base_url}: {e}')

    return None
