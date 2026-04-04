#!/usr/bin/env python3
"""
GEO/AEO Audit Scanner — Automated technical checks for AI search readiness.

Usage:
    python geo_audit.py https://example.com [--output results.json]

Checks:
    1. robots.txt — AI bot permissions (GPTBot, ClaudeBot, PerplexityBot, etc.)
    2. llms.txt — LLM-specific content manifest
    3. sitemap.xml — Presence and basic validity
    4. Schema markup — JSON-LD structured data types
    5. Meta tags — Title, description, OG, Twitter Cards
    6. Content structure — Heading hierarchy, FAQ patterns
    7. Technical — HTTPS, viewport, canonical, page size
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import ssl
from html.parser import HTMLParser
from datetime import datetime


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AI_BOTS = [
    'GPTBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-Web',
    'PerplexityBot', 'Google-Extended', 'Bytespider', 'CCBot',
    'Applebot-Extended', 'cohere-ai', 'Amazonbot',
]

SCHEMA_TYPES_VALUABLE = [
    'Organization', 'LocalBusiness', 'FAQPage', 'Article', 'BlogPosting',
    'Product', 'Service', 'WebPage', 'WebSite', 'BreadcrumbList',
    'HowTo', 'Person', 'Review', 'AggregateRating', 'Event',
    'SoftwareApplication', 'VideoObject', 'Recipe', 'Course',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; GEOAuditBot/1.0; +https://salessignalai.com)',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# Allow self-signed certs for dev environments
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch(url, timeout=15):
    """Fetch a URL and return (status_code, headers, body_text)."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        body = resp.read(500_000).decode('utf-8', errors='replace')
        return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        return e.code, {}, ''
    except Exception as e:
        return 0, {}, str(e)


def normalize_url(url):
    """Ensure URL has scheme and no trailing whitespace."""
    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url
    return url


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class HeadingParser(HTMLParser):
    """Extract headings, meta tags, JSON-LD, and structural elements."""

    def __init__(self):
        super().__init__()
        self.headings = {f'h{i}': [] for i in range(1, 7)}
        self.meta_tags = {}
        self.og_tags = {}
        self.twitter_tags = {}
        self.json_ld = []
        self.has_viewport = False
        self.canonical = None
        self.title = ''
        self.faq_indicators = 0
        self._current_tag = None
        self._current_data = ''
        self._in_script = False
        self._script_type = ''
        self._script_content = ''

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag in self.headings:
            self._current_tag = tag
            self._current_data = ''

        if tag == 'title':
            self._current_tag = 'title'
            self._current_data = ''

        if tag == 'meta':
            name = attrs_dict.get('name', '').lower()
            prop = attrs_dict.get('property', '').lower()
            content = attrs_dict.get('content', '')

            if name == 'description':
                self.meta_tags['description'] = content
            elif name == 'robots':
                self.meta_tags['robots'] = content
            elif name == 'viewport':
                self.has_viewport = True
            elif name == 'author':
                self.meta_tags['author'] = content

            if prop.startswith('og:'):
                self.og_tags[prop] = content
            elif prop.startswith('twitter:') or name.startswith('twitter:'):
                key = prop or f'twitter:{name.split(":", 1)[-1]}'
                self.twitter_tags[key] = content

        if tag == 'link' and attrs_dict.get('rel') == 'canonical':
            self.canonical = attrs_dict.get('href', '')

        if tag == 'script' and attrs_dict.get('type') == 'application/ld+json':
            self._in_script = True
            self._script_content = ''

        # FAQ indicators
        if tag == 'details':
            self.faq_indicators += 1
        if tag in ('section', 'div'):
            id_val = attrs_dict.get('id', '').lower()
            class_val = attrs_dict.get('class', '').lower()
            if 'faq' in id_val or 'faq' in class_val:
                self.faq_indicators += 3

    def handle_data(self, data):
        if self._current_tag:
            self._current_data += data
        if self._in_script:
            self._script_content += data
        # Check for FAQ-like text patterns
        stripped = data.strip().lower()
        if stripped.startswith('q:') or stripped.startswith('question:'):
            self.faq_indicators += 1

    def handle_endtag(self, tag):
        if tag == self._current_tag:
            text = self._current_data.strip()
            if tag == 'title':
                self.title = text
            elif tag in self.headings:
                self.headings[tag].append(text)
            self._current_tag = None
            self._current_data = ''

        if tag == 'script' and self._in_script:
            self._in_script = False
            try:
                data = json.loads(self._script_content)
                if isinstance(data, list):
                    self.json_ld.extend(data)
                else:
                    self.json_ld.append(data)
            except (json.JSONDecodeError, ValueError):
                pass


# ---------------------------------------------------------------------------
# Audit Checks
# ---------------------------------------------------------------------------

def check_robots_txt(base_url):
    """Check robots.txt for AI bot permissions."""
    url = f'{base_url}/robots.txt'
    status, _, body = fetch(url)

    result = {
        'exists': status == 200,
        'status_code': status,
        'ai_bots': {},
        'has_sitemap_ref': False,
        'disallow_all': False,
    }

    if status != 200:
        return result

    lines = body.lower().split('\n')

    # Check for blanket disallow
    current_agent = ''
    for line in lines:
        line = line.strip()
        if line.startswith('user-agent:'):
            current_agent = line.split(':', 1)[1].strip()
        elif line.startswith('disallow: /') and line.strip() == 'disallow: /' and current_agent == '*':
            result['disallow_all'] = True

    # Check each AI bot
    body_lower = body.lower()
    for bot in AI_BOTS:
        bot_lower = bot.lower()
        if bot_lower in body_lower:
            # Find the section for this bot
            in_section = False
            bot_rules = {'mentioned': True, 'allowed': None, 'disallowed_paths': []}
            for line in lines:
                line = line.strip()
                if line.startswith('user-agent:') and bot_lower in line:
                    in_section = True
                elif line.startswith('user-agent:') and bot_lower not in line:
                    in_section = False
                elif in_section:
                    if line.startswith('allow:'):
                        bot_rules['allowed'] = True
                    elif line.startswith('disallow:'):
                        path = line.split(':', 1)[1].strip()
                        if path == '/' or path == '':
                            bot_rules['allowed'] = False
                        else:
                            bot_rules['disallowed_paths'].append(path)
            result['ai_bots'][bot] = bot_rules
        else:
            result['ai_bots'][bot] = {'mentioned': False, 'allowed': None}

    result['has_sitemap_ref'] = 'sitemap:' in body_lower

    return result


def check_llms_txt(base_url):
    """Check for llms.txt presence and quality."""
    url = f'{base_url}/llms.txt'
    status, _, body = fetch(url)

    result = {
        'exists': status == 200,
        'status_code': status,
        'word_count': 0,
        'has_description': False,
        'has_links': False,
        'has_sections': False,
    }

    if status == 200 and body:
        result['word_count'] = len(body.split())
        result['has_description'] = len(body) > 100
        result['has_links'] = 'http' in body.lower()
        result['has_sections'] = body.count('#') >= 2

    return result


def check_sitemap(base_url):
    """Check sitemap.xml presence and basic stats."""
    url = f'{base_url}/sitemap.xml'
    status, _, body = fetch(url)

    result = {
        'exists': status == 200,
        'status_code': status,
        'url_count': 0,
        'has_lastmod': False,
    }

    if status == 200 and body:
        result['url_count'] = body.lower().count('<loc>')
        result['has_lastmod'] = '<lastmod>' in body.lower()

    return result


def check_homepage(base_url):
    """Parse the homepage for meta tags, schema, headings, etc."""
    status, headers, body = fetch(base_url)

    result = {
        'status_code': status,
        'is_https': base_url.startswith('https'),
        'page_size_kb': round(len(body) / 1024, 1),
        'title': '',
        'meta_description': '',
        'has_viewport': False,
        'has_canonical': False,
        'canonical_url': '',
        'og_tags': {},
        'twitter_tags': {},
        'headings': {},
        'heading_counts': {},
        'json_ld_types': [],
        'json_ld_count': 0,
        'faq_indicators': 0,
        'has_author': False,
        'word_count': 0,
    }

    if status != 200:
        return result

    parser = HeadingParser()
    try:
        parser.feed(body)
    except Exception:
        pass

    result['title'] = parser.title
    result['meta_description'] = parser.meta_tags.get('description', '')
    result['has_viewport'] = parser.has_viewport
    result['has_canonical'] = bool(parser.canonical)
    result['canonical_url'] = parser.canonical or ''
    result['og_tags'] = parser.og_tags
    result['twitter_tags'] = parser.twitter_tags
    result['headings'] = {k: v for k, v in parser.headings.items() if v}
    result['heading_counts'] = {k: len(v) for k, v in parser.headings.items()}
    result['faq_indicators'] = parser.faq_indicators
    result['has_author'] = bool(parser.meta_tags.get('author'))

    # JSON-LD analysis
    ld_types = []
    for item in parser.json_ld:
        if isinstance(item, dict):
            t = item.get('@type', '')
            if isinstance(t, list):
                ld_types.extend(t)
            elif t:
                ld_types.append(t)
    result['json_ld_types'] = list(set(ld_types))
    result['json_ld_count'] = len(parser.json_ld)

    # Rough word count (strip tags)
    text = re.sub(r'<[^>]+>', ' ', body)
    text = re.sub(r'\s+', ' ', text)
    result['word_count'] = len(text.split())

    return result


def check_key_pages(base_url):
    """Check for common important pages."""
    pages = {
        'about': ['/about', '/about/', '/about-us', '/about-us/', '/who-we-are', '/our-story'],
        'faq': ['/faq', '/faq/', '/faqs', '/faqs/', '/frequently-asked-questions', '/frequently-asked-questions/', '/help', '/help/', '/knowledge-base'],
        'contact': ['/contact', '/contact/', '/contact-us', '/contact-us/', '/support', '/support/', '/get-in-touch', '/reach-us', '/help-center'],
        'pricing': ['/pricing', '/pricing/', '/plans', '/plans/', '/packages', '/services', '/services/', '/courses', '/products'],
        'blog': ['/blog', '/blog/', '/articles', '/articles/', '/news', '/news/', '/insights', '/resources', '/learn'],
    }

    results = {}
    for page_type, paths in pages.items():
        found = False
        for path in paths:
            status, _, _ = fetch(f'{base_url}{path}', timeout=8)
            if status == 200:
                found = True
                break
        results[page_type] = found

    return results


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def calculate_scores(robots, llms, sitemap, homepage, key_pages):
    """Calculate GEO scores per category."""

    scores = {}

    # 1. AI Crawlability (15%)
    crawl_score = 0
    if robots['exists']:
        crawl_score += 2
        if not robots['disallow_all']:
            crawl_score += 1
        # Points for AI bot permissions
        bots_allowed = sum(
            1 for bot, info in robots['ai_bots'].items()
            if info.get('mentioned') and info.get('allowed') is not False
        )
        crawl_score += min(3, bots_allowed)  # up to 3 points for AI bot access
    if llms['exists']:
        crawl_score += 2
        if llms['has_sections'] and llms['word_count'] > 50:
            crawl_score += 1
    if sitemap['exists']:
        crawl_score += 1
        if sitemap['url_count'] > 5:
            crawl_score += 0.5
        if sitemap['has_lastmod']:
            crawl_score += 0.5
    scores['ai_crawlability'] = min(10, crawl_score)

    # 2. Schema & Structured Data (20%)
    schema_score = 0
    ld_types = homepage.get('json_ld_types', [])
    if homepage['json_ld_count'] > 0:
        schema_score += 2
    valuable_found = [t for t in ld_types if t in SCHEMA_TYPES_VALUABLE]
    schema_score += min(4, len(valuable_found) * 1.0)
    # Bonus for key types
    if 'Organization' in ld_types or 'LocalBusiness' in ld_types:
        schema_score += 1.5
    if 'FAQPage' in ld_types:
        schema_score += 1
    if 'BreadcrumbList' in ld_types:
        schema_score += 0.5
    if 'Article' in ld_types or 'BlogPosting' in ld_types:
        schema_score += 0.5
    scores['schema_structured_data'] = min(10, schema_score)

    # 3. Content Structure (15%)
    content_score = 0
    h1_count = homepage['heading_counts'].get('h1', 0)
    h2_count = homepage['heading_counts'].get('h2', 0)
    h3_count = homepage['heading_counts'].get('h3', 0)
    if h1_count == 1:
        content_score += 2
    elif h1_count > 1:
        content_score += 1
    if h2_count >= 2:
        content_score += 2
    elif h2_count == 1:
        content_score += 1
    if h3_count >= 1:
        content_score += 1
    if homepage['faq_indicators'] >= 2:
        content_score += 2
    if homepage['word_count'] > 500:
        content_score += 1.5
    elif homepage['word_count'] > 200:
        content_score += 0.75
    if key_pages.get('faq'):
        content_score += 1
    if key_pages.get('blog'):
        content_score += 0.5
    scores['content_structure'] = min(10, content_score)

    # 4. Entity Clarity (15%) — partial automation
    entity_score = 0
    if homepage['title']:
        entity_score += 1.5
    if homepage['meta_description'] and len(homepage['meta_description']) > 50:
        entity_score += 1.5
    if key_pages.get('about'):
        entity_score += 2
    if key_pages.get('contact'):
        entity_score += 1
    if homepage['has_author']:
        entity_score += 1
    if 'Organization' in ld_types or 'LocalBusiness' in ld_types:
        entity_score += 1.5
    # Note: remaining points require manual content review
    scores['entity_clarity'] = min(10, entity_score)
    scores['entity_clarity_note'] = 'Partial — manual content review needed for full score'

    # 5. Quotability (15%) — mostly manual, give baseline
    quote_score = 0
    if homepage['word_count'] > 300:
        quote_score += 1
    if key_pages.get('blog'):
        quote_score += 1.5
    if key_pages.get('about'):
        quote_score += 1
    scores['quotability_citations'] = min(10, quote_score)
    scores['quotability_note'] = 'Requires manual content review for full assessment'

    # 6. E-E-A-T (10%) — mostly manual
    eeat_score = 0
    if homepage['has_author']:
        eeat_score += 2
    if key_pages.get('about'):
        eeat_score += 2
    if key_pages.get('contact'):
        eeat_score += 1.5
    scores['eeat_signals'] = min(10, eeat_score)
    scores['eeat_note'] = 'Requires manual review of credentials, testimonials, certifications'

    # 7. Technical Foundation (10%)
    tech_score = 0
    if homepage['is_https']:
        tech_score += 2
    if homepage['has_viewport']:
        tech_score += 1.5
    if homepage['has_canonical']:
        tech_score += 1
    if homepage['og_tags']:
        tech_score += 1.5
    if homepage['twitter_tags']:
        tech_score += 1
    if homepage['status_code'] == 200:
        tech_score += 1
    if homepage['page_size_kb'] < 500:
        tech_score += 1
    if key_pages.get('pricing'):
        tech_score += 0.5
    scores['technical_foundation'] = min(10, tech_score)

    # Weighted overall
    weights = {
        'ai_crawlability': 0.15,
        'schema_structured_data': 0.20,
        'content_structure': 0.15,
        'entity_clarity': 0.15,
        'quotability_citations': 0.15,
        'eeat_signals': 0.10,
        'technical_foundation': 0.10,
    }

    overall = sum(
        scores.get(cat, 0) * weight * 10
        for cat, weight in weights.items()
    )
    scores['overall_geo_score'] = round(overall, 1)

    # Interpretation
    if overall >= 80:
        scores['interpretation'] = 'AI-Optimized'
    elif overall >= 60:
        scores['interpretation'] = 'Partially Optimized'
    elif overall >= 40:
        scores['interpretation'] = 'Needs Work'
    else:
        scores['interpretation'] = 'Not AI-Ready'

    return scores


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_audit(url):
    """Run the full automated audit and return results dict."""
    base_url = normalize_url(url).rstrip('/')

    print(f'[GEO Audit] Starting audit for: {base_url}')
    print(f'[GEO Audit] Timestamp: {datetime.now().isoformat()}')
    print()

    results = {
        'url': base_url,
        'audit_date': datetime.now().isoformat(),
        'checks': {},
        'scores': {},
    }

    # Run checks
    print('[1/5] Checking robots.txt...')
    results['checks']['robots_txt'] = check_robots_txt(base_url)

    print('[2/5] Checking llms.txt...')
    results['checks']['llms_txt'] = check_llms_txt(base_url)

    print('[3/5] Checking sitemap.xml...')
    results['checks']['sitemap'] = check_sitemap(base_url)

    print('[4/5] Analyzing homepage...')
    results['checks']['homepage'] = check_homepage(base_url)

    print('[5/5] Checking key pages...')
    results['checks']['key_pages'] = check_key_pages(base_url)

    # Calculate scores
    print()
    print('[GEO Audit] Calculating scores...')
    results['scores'] = calculate_scores(
        results['checks']['robots_txt'],
        results['checks']['llms_txt'],
        results['checks']['sitemap'],
        results['checks']['homepage'],
        results['checks']['key_pages'],
    )

    # Print summary
    print()
    print('=' * 60)
    print(f'  GEO AUDIT RESULTS — {base_url}')
    print('=' * 60)
    print()
    s = results['scores']
    print(f'  Overall GEO Score:      {s["overall_geo_score"]}/100  ({s["interpretation"]})')
    print()
    print(f'  AI Crawlability:        {s["ai_crawlability"]:.1f}/10')
    print(f'  Schema & Structured:    {s["schema_structured_data"]:.1f}/10')
    print(f'  Content Structure:      {s["content_structure"]:.1f}/10')
    print(f'  Entity Clarity:         {s["entity_clarity"]:.1f}/10  *')
    print(f'  Quotability:            {s["quotability_citations"]:.1f}/10  *')
    print(f'  E-E-A-T Signals:        {s["eeat_signals"]:.1f}/10  *')
    print(f'  Technical Foundation:   {s["technical_foundation"]:.1f}/10')
    print()
    print('  * = partial score, manual review recommended')
    print('=' * 60)

    return results


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python geo_audit.py <url> [--output results.json]')
        sys.exit(1)

    target_url = sys.argv[1]
    output_file = None

    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    audit_results = run_audit(target_url)

    # Save results
    if not output_file:
        output_file = 'geo_audit_results.json'

    with open(output_file, 'w') as f:
        json.dump(audit_results, f, indent=2)

    print(f'\nResults saved to: {output_file}')
