"""
Lightweight GEO Score — Quick AI-readiness check for a website.
No AI tokens. Just 3 fast HTTP checks: robots.txt, llms.txt, schema.
Returns a score out of 100 and a letter grade.
"""

import hashlib
import json
import re
import urllib.request
import urllib.error
import ssl

from django.core.cache import cache

AI_BOTS = [
    'gptbot', 'chatgpt-user', 'claudebot', 'claude-web',
    'perplexitybot', 'google-extended', 'bytespider', 'ccbot',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; SalesSignalAI/1.0)',
    'Accept': 'text/html,application/xhtml+xml,*/*',
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url, timeout=8):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        body = resp.read(200_000).decode('utf-8', errors='replace')
        return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception:
        return 0, ''


def quick_geo_score(website_url):
    """
    Run a fast GEO check on a website URL.
    Returns dict with score, grade, and breakdown.
    Cached for 7 days per domain.
    """
    if not website_url:
        return {'score': 0, 'grade': '?', 'error': 'No website URL'}

    # Normalize
    url = website_url.strip()
    if not url.startswith('http'):
        url = 'https://' + url
    base = url.rstrip('/')

    # Cache check
    ck = f'geo_score:{hashlib.md5(base.encode()).hexdigest()}'
    cached = cache.get(ck)
    if cached:
        return cached

    points = 0
    max_points = 50
    breakdown = {}

    # 1. Check robots.txt for AI bots (max 15 pts)
    status, body = _fetch(f'{base}/robots.txt')
    robots_score = 0
    if status == 200:
        robots_score += 3  # exists
        body_lower = body.lower()
        bots_found = 0
        for bot in AI_BOTS:
            if bot in body_lower:
                bots_found += 1
        if bots_found >= 3:
            robots_score += 7
        elif bots_found >= 1:
            robots_score += 4
        # Check for blanket disallow
        if 'disallow: /' in body_lower and 'user-agent: *' in body_lower:
            # Could be blocking everything — deduct
            robots_score = max(0, robots_score - 3)
        if 'sitemap:' in body_lower:
            robots_score += 2
    breakdown['robots_txt'] = {'score': min(15, robots_score), 'max': 15, 'exists': status == 200}
    points += min(15, robots_score)

    # 2. Check llms.txt (max 15 pts)
    status, body = _fetch(f'{base}/llms.txt')
    llms_score = 0
    if status == 200 and len(body) > 50:
        llms_score += 8  # exists and has content
        if body.count('#') >= 2:
            llms_score += 4  # has structure
        if 'http' in body:
            llms_score += 3  # has links
    breakdown['llms_txt'] = {'score': min(15, llms_score), 'max': 15, 'exists': status == 200}
    points += min(15, llms_score)

    # 3. Check homepage for schema markup (max 20 pts)
    status, body = _fetch(base)
    schema_score = 0
    schema_types = []
    if status == 200:
        # Find JSON-LD blocks
        ld_blocks = re.findall(
            r'<script\s+type=["\']application/ld\+json["\']>(.*?)</script>',
            body, re.DOTALL | re.IGNORECASE
        )
        for block in ld_blocks:
            try:
                data = json.loads(block.strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and '@type' in item:
                        t = item['@type']
                        if isinstance(t, list):
                            schema_types.extend(t)
                        else:
                            schema_types.append(t)
            except (json.JSONDecodeError, ValueError):
                pass

        if schema_types:
            schema_score += 5  # has any schema
            valuable = ['Organization', 'LocalBusiness', 'FAQPage', 'Service',
                        'Product', 'Article', 'BlogPosting', 'BreadcrumbList',
                        'WebPage', 'WebSite', 'SoftwareApplication', 'HowTo']
            found_valuable = [t for t in schema_types if t in valuable]
            schema_score += min(10, len(found_valuable) * 2.5)
            if 'FAQPage' in schema_types:
                schema_score += 3
            if 'Organization' in schema_types or 'LocalBusiness' in schema_types:
                schema_score += 2

    breakdown['schema'] = {
        'score': min(20, schema_score),
        'max': 20,
        'types_found': list(set(schema_types)),
    }
    points += min(20, schema_score)

    # Calculate final score (0-100)
    score = round((points / max_points) * 100)
    score = min(100, max(0, score))

    # Grade
    if score >= 80:
        grade = 'A'
    elif score >= 60:
        grade = 'B'
    elif score >= 40:
        grade = 'C'
    elif score >= 20:
        grade = 'D'
    else:
        grade = 'F'

    result = {
        'score': score,
        'grade': grade,
        'breakdown': breakdown,
        'website': base,
    }

    cache.set(ck, result, 60 * 60 * 24 * 7)  # 7 days
    return result
