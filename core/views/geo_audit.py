"""
GEO Audit Tool — Standalone admin page for running AI-readiness audits
on any website URL. Generates a detailed report + downloadable PDF.
"""

import json
import hashlib
import re
import urllib.request
import urllib.error
import ssl
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.core.cache import cache

from core.services.geo_score import quick_geo_score


# ---------------------------------------------------------------------------
# Page view
# ---------------------------------------------------------------------------

@login_required
def geo_audit_tool(request):
    """Render the GEO Audit tool page."""
    if not (request.user.is_staff or request.user.is_superuser):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Admin access required.')
    return render(request, 'tools/geo_audit.html')


# ---------------------------------------------------------------------------
# AJAX endpoint — full audit
# ---------------------------------------------------------------------------

AI_BOTS = [
    'gptbot', 'chatgpt-user', 'claudebot', 'claude-web',
    'perplexitybot', 'google-extended', 'bytespider', 'ccbot',
    'applebot-extended', 'cohere-ai', 'amazonbot',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; SalesSignalAI/1.0)',
    'Accept': 'text/html,application/xhtml+xml,*/*',
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        body = resp.read(500_000).decode('utf-8', errors='replace')
        return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception:
        return 0, ''


@login_required
@require_POST
def geo_audit_api(request):
    """Run a detailed GEO audit on a URL and return JSON results."""
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Admin access required'}, status=403)

    data = json.loads(request.body)
    url = (data.get('url') or '').strip()
    if not url:
        return JsonResponse({'error': 'URL is required'}, status=400)

    if not url.startswith('http'):
        url = 'https://' + url
    base = url.rstrip('/')

    # Check cache (1 hour for full audits)
    ck = f'geo_audit_full:{hashlib.md5(base.encode()).hexdigest()}'
    cached = cache.get(ck)
    if cached:
        return JsonResponse({'ok': True, 'result': cached})

    result = _run_full_audit(base)
    cache.set(ck, result, 60 * 60)  # 1 hour cache
    return JsonResponse({'ok': True, 'result': result})


def _run_full_audit(base_url):
    """Run all audit checks and return structured results."""
    audit = {
        'url': base_url,
        'audit_date': datetime.now().strftime('%B %d, %Y'),
        'categories': {},
        'overall_score': 0,
        'grade': 'F',
        'recommendations': [],
        'quick_wins': [],
    }

    # ── 1. Robots.txt ──
    status, body = _fetch(f'{base_url}/robots.txt')
    robots = {
        'exists': status == 200,
        'score': 0,
        'max_score': 15,
        'findings': [],
        'issues': [],
    }
    if status == 200:
        robots['score'] += 3
        robots['findings'].append('robots.txt exists and is accessible')
        body_lower = body.lower()

        bots_allowed = []
        bots_blocked = []
        bots_missing = []
        for bot in AI_BOTS:
            if bot in body_lower:
                # Check if explicitly blocked
                lines = body_lower.split('\n')
                blocked = False
                in_section = False
                for line in lines:
                    line = line.strip()
                    if f'user-agent: {bot}' in line:
                        in_section = True
                    elif line.startswith('user-agent:'):
                        in_section = False
                    elif in_section and line.startswith('disallow: /') and line.strip() == 'disallow: /':
                        blocked = True
                if blocked:
                    bots_blocked.append(bot)
                else:
                    bots_allowed.append(bot)
            else:
                bots_missing.append(bot)

        if bots_allowed:
            robots['score'] += min(7, len(bots_allowed) * 1.5)
            robots['findings'].append(f'AI bots with access: {", ".join(bots_allowed)}')
        if bots_blocked:
            robots['issues'].append(f'AI bots BLOCKED: {", ".join(bots_blocked)}')
        if len(bots_missing) > 5:
            robots['issues'].append(f'No explicit rules for most AI crawlers ({len(bots_missing)} missing)')

        if 'sitemap:' in body_lower:
            robots['score'] += 2
            robots['findings'].append('Sitemap reference found in robots.txt')
        else:
            robots['issues'].append('No sitemap reference in robots.txt')

        # Check for blanket disallow
        if 'user-agent: *' in body_lower:
            for line in body_lower.split('\n'):
                if line.strip() == 'disallow: /':
                    robots['issues'].append('WARNING: Blanket Disallow: / found for all user agents')
                    robots['score'] = max(0, robots['score'] - 5)
                    break
    else:
        robots['issues'].append('robots.txt not found — AI crawlers have no guidance')

    robots['score'] = min(robots['max_score'], robots['score'])
    audit['categories']['robots_txt'] = robots

    # ── 2. llms.txt ──
    status, body = _fetch(f'{base_url}/llms.txt')
    llms = {
        'exists': status == 200,
        'score': 0,
        'max_score': 15,
        'findings': [],
        'issues': [],
    }
    if status == 200 and len(body) > 30:
        llms['score'] += 6
        llms['findings'].append(f'llms.txt exists ({len(body.split())} words)')
        if body.count('#') >= 2:
            llms['score'] += 3
            llms['findings'].append('Has structured sections with headings')
        else:
            llms['issues'].append('llms.txt lacks structured headings')
        if 'http' in body.lower():
            llms['score'] += 3
            llms['findings'].append('Contains links to key pages')
        else:
            llms['issues'].append('No links to key pages — AI can\'t navigate the site')
        if len(body.split()) > 100:
            llms['score'] += 3
            llms['findings'].append('Comprehensive content for AI context')
        else:
            llms['issues'].append('Content is thin — add more detail about services and differentiators')
    else:
        llms['issues'].append('llms.txt not found — this is a key file for AI search engines like ChatGPT and Perplexity')

    llms['score'] = min(llms['max_score'], llms['score'])
    audit['categories']['llms_txt'] = llms

    # ── 3. Sitemap ──
    status, body = _fetch(f'{base_url}/sitemap.xml')
    sitemap = {
        'exists': status == 200,
        'score': 0,
        'max_score': 10,
        'findings': [],
        'issues': [],
    }
    if status == 200:
        url_count = body.lower().count('<loc>')
        sitemap['score'] += 4
        sitemap['findings'].append(f'sitemap.xml exists with {url_count} URLs')
        if url_count > 5:
            sitemap['score'] += 2
        if '<lastmod>' in body.lower():
            sitemap['score'] += 2
            sitemap['findings'].append('Contains lastmod dates (helps AI know content freshness)')
        else:
            sitemap['issues'].append('Missing lastmod dates — AI can\'t assess content freshness')
        if '<changefreq>' in body.lower():
            sitemap['score'] += 2
            sitemap['findings'].append('Contains change frequency hints')
    else:
        sitemap['issues'].append('sitemap.xml not found — essential for both SEO and AI discoverability')

    sitemap['score'] = min(sitemap['max_score'], sitemap['score'])
    audit['categories']['sitemap'] = sitemap

    # ── 4. Homepage Analysis ──
    status, body = _fetch(base_url)
    homepage = {
        'score': 0,
        'max_score': 30,
        'findings': [],
        'issues': [],
        'schema_types': [],
    }

    if status == 200:
        # Title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', body, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ''
        if title:
            homepage['score'] += 2
            homepage['findings'].append(f'Title tag: "{title[:80]}"')
            if len(title) > 10 and len(title) < 70:
                homepage['score'] += 1
                homepage['findings'].append('Title length is optimal for search')
        else:
            homepage['issues'].append('Missing title tag — critical for all search engines')

        # Meta description
        desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', body, re.IGNORECASE)
        if not desc_match:
            desc_match = re.search(r'<meta[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']description["\']', body, re.IGNORECASE)
        if desc_match:
            desc = desc_match.group(1)
            homepage['score'] += 2
            homepage['findings'].append(f'Meta description present ({len(desc)} chars)')
            if len(desc) > 120 and len(desc) < 160:
                homepage['score'] += 1
        else:
            homepage['issues'].append('Missing meta description — AI uses this to understand your page')

        # HTTPS
        if base_url.startswith('https'):
            homepage['score'] += 2
            homepage['findings'].append('HTTPS enabled (trust signal)')
        else:
            homepage['issues'].append('Not using HTTPS — major trust issue for AI and users')

        # Viewport
        if re.search(r'name=["\']viewport["\']', body, re.IGNORECASE):
            homepage['score'] += 1
            homepage['findings'].append('Mobile viewport configured')
        else:
            homepage['issues'].append('Missing viewport meta tag — poor mobile experience')

        # Canonical
        if re.search(r'rel=["\']canonical["\']', body, re.IGNORECASE):
            homepage['score'] += 1
            homepage['findings'].append('Canonical URL set')

        # Open Graph
        og_count = len(re.findall(r'property=["\']og:', body, re.IGNORECASE))
        if og_count >= 3:
            homepage['score'] += 2
            homepage['findings'].append(f'Open Graph tags present ({og_count} tags)')
        elif og_count > 0:
            homepage['score'] += 1
            homepage['issues'].append(f'Only {og_count} Open Graph tags — add og:title, og:description, og:image at minimum')
        else:
            homepage['issues'].append('No Open Graph tags — poor social/AI preview')

        # Headings
        h1s = re.findall(r'<h1[^>]*>(.*?)</h1>', body, re.IGNORECASE | re.DOTALL)
        h2s = re.findall(r'<h2[^>]*>(.*?)</h2>', body, re.IGNORECASE | re.DOTALL)
        h3s = re.findall(r'<h3[^>]*>(.*?)</h3>', body, re.IGNORECASE | re.DOTALL)
        if len(h1s) == 1:
            homepage['score'] += 2
            homepage['findings'].append(f'Single H1 tag (good hierarchy)')
        elif len(h1s) > 1:
            homepage['score'] += 1
            homepage['issues'].append(f'Multiple H1 tags ({len(h1s)}) — should have exactly one')
        else:
            homepage['issues'].append('No H1 tag — critical for content hierarchy')
        if h2s:
            homepage['score'] += 1
            homepage['findings'].append(f'{len(h2s)} H2 tags provide content structure')
        if h3s:
            homepage['score'] += 0.5

        # JSON-LD Schema
        ld_blocks = re.findall(
            r'<script\s+type=["\']application/ld\+json["\']>(.*?)</script>',
            body, re.DOTALL | re.IGNORECASE
        )
        schema_types = []
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

        schema_types = list(set(schema_types))
        homepage['schema_types'] = schema_types

        if schema_types:
            homepage['score'] += 3
            homepage['findings'].append(f'JSON-LD structured data found: {", ".join(schema_types)}')
            valuable = ['Organization', 'LocalBusiness', 'FAQPage', 'Service',
                        'Product', 'Article', 'BlogPosting', 'BreadcrumbList',
                        'WebPage', 'WebSite', 'SoftwareApplication', 'HowTo']
            found_valuable = [t for t in schema_types if t in valuable]
            if found_valuable:
                homepage['score'] += min(5, len(found_valuable) * 1.5)
                homepage['findings'].append(f'Valuable schema types: {", ".join(found_valuable)}')
            missing_essential = []
            if 'Organization' not in schema_types and 'LocalBusiness' not in schema_types:
                missing_essential.append('Organization or LocalBusiness')
            if 'FAQPage' not in schema_types:
                missing_essential.append('FAQPage')
            if 'BreadcrumbList' not in schema_types:
                missing_essential.append('BreadcrumbList')
            if missing_essential:
                homepage['issues'].append(f'Missing recommended schema: {", ".join(missing_essential)}')
        else:
            homepage['issues'].append('No JSON-LD structured data — AI search engines rely heavily on this')

        # FAQ indicators
        faq_pattern = body.lower().count('faq') + body.lower().count('frequently asked')
        if faq_pattern > 0:
            homepage['score'] += 1
            homepage['findings'].append('FAQ content detected on page')

        # Word count
        text_only = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL | re.IGNORECASE)
        text_only = re.sub(r'<style[^>]*>.*?</style>', '', text_only, flags=re.DOTALL | re.IGNORECASE)
        text_only = re.sub(r'<[^>]+>', ' ', text_only)
        text_only = re.sub(r'\s+', ' ', text_only)
        word_count = len(text_only.split())
        homepage['findings'].append(f'Page word count: ~{word_count}')
        if word_count > 500:
            homepage['score'] += 2
        elif word_count > 200:
            homepage['score'] += 1
        else:
            homepage['issues'].append('Very thin content — AI needs substantial text to understand your business')

    else:
        homepage['issues'].append(f'Homepage returned status {status} — site may be down or blocking requests')

    homepage['score'] = min(homepage['max_score'], round(homepage['score']))
    audit['categories']['homepage'] = homepage

    # ── 5. Key Pages ──
    key_pages = {
        'score': 0,
        'max_score': 15,
        'findings': [],
        'issues': [],
        'pages_found': {},
    }
    page_checks = {
        'About': ['/about', '/about/', '/about-us', '/about-us/', '/who-we-are', '/our-story'],
        'FAQ': ['/faq', '/faq/', '/faqs', '/faqs/', '/frequently-asked-questions', '/frequently-asked-questions/', '/help', '/help/', '/knowledge-base'],
        'Contact': ['/contact', '/contact/', '/contact-us', '/contact-us/', '/support', '/support/', '/get-in-touch', '/reach-us', '/help-center'],
        'Blog': ['/blog', '/blog/', '/articles', '/articles/', '/news', '/news/', '/insights', '/resources', '/learn'],
        'Pricing': ['/pricing', '/pricing/', '/plans', '/plans/', '/packages', '/services', '/services/', '/courses', '/products'],
    }
    for page_name, paths in page_checks.items():
        found = False
        found_path = ''
        for path in paths:
            s, _ = _fetch(f'{base_url}{path}', timeout=6)
            if s == 200:
                found = True
                found_path = path
                break
        key_pages['pages_found'][page_name] = found
        if found:
            key_pages['score'] += 3
            key_pages['findings'].append(f'{page_name} page found ({found_path})')
        else:
            key_pages['issues'].append(f'No {page_name} page detected')

    key_pages['score'] = min(key_pages['max_score'], key_pages['score'])
    audit['categories']['key_pages'] = key_pages

    # ── 6. Traditional SEO ──
    # Re-use the homepage body we already fetched
    trad_seo = {
        'score': 0,
        'max_score': 25,
        'findings': [],
        'issues': [],
    }

    if status == 200 and body:
        hp = body  # homepage body already fetched above

        # Image alt tags
        imgs = re.findall(r'<img\s[^>]*>', hp, re.IGNORECASE)
        imgs_with_alt = [i for i in imgs if re.search(r'alt=["\'][^"\']+["\']', i, re.IGNORECASE)]
        if imgs:
            alt_pct = round((len(imgs_with_alt) / len(imgs)) * 100)
            if alt_pct >= 80:
                trad_seo['score'] += 3
                trad_seo['findings'].append(f'Image alt tags: {alt_pct}% of {len(imgs)} images have alt text')
            elif alt_pct >= 50:
                trad_seo['score'] += 1.5
                trad_seo['issues'].append(f'Only {alt_pct}% of images have alt text ({len(imgs_with_alt)}/{len(imgs)})')
            else:
                trad_seo['issues'].append(f'Poor alt text coverage: {alt_pct}% ({len(imgs_with_alt)}/{len(imgs)} images)')
        else:
            trad_seo['findings'].append('No images on homepage (not necessarily bad)')
            trad_seo['score'] += 1

        # Internal links
        internal_links = re.findall(r'<a\s[^>]*href=["\'](?:/[^"\']*|' + re.escape(base_url) + r'[^"\']*)["\']', hp, re.IGNORECASE)
        ext_links = re.findall(r'<a\s[^>]*href=["\']https?://(?!' + re.escape(base_url.replace('https://', '').replace('http://', '')) + r')[^"\']*["\']', hp, re.IGNORECASE)
        if len(internal_links) >= 5:
            trad_seo['score'] += 2
            trad_seo['findings'].append(f'{len(internal_links)} internal links (good site structure)')
        elif len(internal_links) >= 1:
            trad_seo['score'] += 1
            trad_seo['issues'].append(f'Only {len(internal_links)} internal links — more helps SEO')
        else:
            trad_seo['issues'].append('No internal links found — critical for SEO crawlability')
        if ext_links:
            trad_seo['findings'].append(f'{len(ext_links)} external links (authority signals)')

        # Page size / load weight
        page_kb = round(len(hp) / 1024, 1)
        if page_kb < 200:
            trad_seo['score'] += 2
            trad_seo['findings'].append(f'Page size: {page_kb}KB (lightweight)')
        elif page_kb < 500:
            trad_seo['score'] += 1
            trad_seo['findings'].append(f'Page size: {page_kb}KB (acceptable)')
        else:
            trad_seo['issues'].append(f'Page size: {page_kb}KB — heavy page may load slowly')

        # Check for lazy loading
        if 'loading="lazy"' in hp.lower() or 'data-src' in hp.lower():
            trad_seo['score'] += 1
            trad_seo['findings'].append('Lazy loading detected (good for performance)')

        # CSS/JS optimization signals
        inline_styles = len(re.findall(r'<style[^>]*>', hp, re.IGNORECASE))
        inline_scripts = len(re.findall(r'<script(?![^>]*type=["\']application/ld\+json)[^>]*>', hp, re.IGNORECASE))
        if inline_styles > 5:
            trad_seo['issues'].append(f'{inline_styles} inline style blocks — consider consolidating CSS')
        if inline_scripts > 10:
            trad_seo['issues'].append(f'{inline_scripts} script tags — may slow page load')

        # Twitter Card
        twitter_tags = len(re.findall(r'name=["\']twitter:', hp, re.IGNORECASE))
        if twitter_tags >= 2:
            trad_seo['score'] += 1
            trad_seo['findings'].append(f'Twitter Card tags present ({twitter_tags} tags)')
        else:
            trad_seo['issues'].append('Missing Twitter Card tags')

        # Robots meta tag check
        robots_meta = re.search(r'name=["\']robots["\'][^>]*content=["\']([^"\']*)["\']', hp, re.IGNORECASE)
        if robots_meta:
            content = robots_meta.group(1).lower()
            if 'noindex' in content:
                trad_seo['issues'].append('WARNING: Page has noindex — Google will NOT index this page')
                trad_seo['score'] -= 3
            elif 'nofollow' in content:
                trad_seo['issues'].append('Page has nofollow — links on this page pass no SEO value')
            else:
                trad_seo['score'] += 1
                trad_seo['findings'].append('Robots meta allows indexing')

        # Language attribute
        if re.search(r'<html[^>]*lang=', hp, re.IGNORECASE):
            trad_seo['score'] += 1
            trad_seo['findings'].append('HTML lang attribute set (internationalization signal)')
        else:
            trad_seo['issues'].append('Missing HTML lang attribute')

        # Charset
        if re.search(r'charset=', hp, re.IGNORECASE):
            trad_seo['score'] += 1
            trad_seo['findings'].append('Character encoding declared')

        # Check for minification signals
        if len(hp) > 1000:
            newline_ratio = hp.count('\n') / len(hp) * 1000
            if newline_ratio < 2:
                trad_seo['score'] += 1
                trad_seo['findings'].append('HTML appears minified (good for performance)')

        # Favicon
        if re.search(r'rel=["\'](?:shortcut )?icon["\']', hp, re.IGNORECASE):
            trad_seo['score'] += 1
            trad_seo['findings'].append('Favicon detected')
        else:
            trad_seo['issues'].append('No favicon link tag — minor but affects professionalism')

        # Check for Google Analytics / Tag Manager
        if 'google-analytics' in hp.lower() or 'gtag' in hp.lower() or 'googletagmanager' in hp.lower():
            trad_seo['score'] += 1
            trad_seo['findings'].append('Google Analytics/Tag Manager detected')

        # Check for hreflang (international SEO)
        if 'hreflang' in hp.lower():
            trad_seo['score'] += 1
            trad_seo['findings'].append('hreflang tags detected (international SEO)')

        # Noopener/noreferrer on external links
        ext_with_rel = len(re.findall(r'rel=["\'][^"\']*noopener', hp, re.IGNORECASE))
        if ext_links and ext_with_rel:
            trad_seo['score'] += 1
            trad_seo['findings'].append('External links use rel="noopener" (security best practice)')

    else:
        trad_seo['issues'].append('Could not analyze homepage for traditional SEO')

    trad_seo['score'] = min(trad_seo['max_score'], max(0, round(trad_seo['score'])))
    audit['categories']['traditional_seo'] = trad_seo

    # ── Calculate Overall Score ──
    total_score = sum(cat['score'] for cat in audit['categories'].values())
    total_max = sum(cat['max_score'] for cat in audit['categories'].values())
    overall = round((total_score / total_max) * 100) if total_max > 0 else 0
    audit['overall_score'] = min(100, max(0, overall))

    if overall >= 80:
        audit['grade'] = 'A'
    elif overall >= 60:
        audit['grade'] = 'B'
    elif overall >= 40:
        audit['grade'] = 'C'
    elif overall >= 20:
        audit['grade'] = 'D'
    else:
        audit['grade'] = 'F'

    # ── Generate Recommendations ──
    recs = []
    quick = []

    # Priority recommendations based on findings
    if not audit['categories']['llms_txt']['exists']:
        recs.append({
            'priority': 'HIGH',
            'title': 'Create an llms.txt file',
            'detail': 'This is the single highest-impact change for AI search visibility. It tells AI crawlers exactly what your business does, what pages matter, and how to describe you.',
            'effort': 'Low (30 minutes)',
        })
        quick.append('Create llms.txt with business description, key pages, and services')

    if not audit['categories']['robots_txt']['exists']:
        recs.append({
            'priority': 'HIGH',
            'title': 'Create robots.txt with AI bot permissions',
            'detail': 'Without robots.txt, AI crawlers may skip your site entirely. Add explicit Allow rules for GPTBot, ClaudeBot, PerplexityBot, and other AI user agents.',
            'effort': 'Low (15 minutes)',
        })
        quick.append('Create robots.txt with AI crawler permissions')

    if not audit['categories'].get('homepage', {}).get('schema_types'):
        recs.append({
            'priority': 'HIGH',
            'title': 'Add JSON-LD structured data',
            'detail': 'Structured data (schema.org) helps AI understand your business type, services, location, and FAQ. Start with Organization/LocalBusiness and FAQPage schemas.',
            'effort': 'Medium (1-2 hours)',
        })
        quick.append('Add Organization or LocalBusiness JSON-LD to homepage')

    if not audit['categories']['sitemap']['exists']:
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Add sitemap.xml',
            'detail': 'A sitemap helps all search engines (traditional and AI) discover your important pages.',
            'effort': 'Low (15 minutes)',
        })

    if not key_pages['pages_found'].get('FAQ'):
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Create an FAQ page with FAQPage schema',
            'detail': 'FAQ pages are gold for AI search — they directly answer the questions people ask AI assistants. Add FAQPage JSON-LD schema so AI can parse the Q&A format.',
            'effort': 'Medium (1-2 hours)',
        })

    if not key_pages['pages_found'].get('About'):
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Create an About page',
            'detail': 'AI search engines use About pages to verify business legitimacy and understand the team\'s expertise. This is a key E-E-A-T signal.',
            'effort': 'Medium (1 hour)',
        })

    # Traditional SEO recommendations
    trad = audit['categories'].get('traditional_seo', {})
    trad_issues = trad.get('issues', [])
    if any('alt text' in i.lower() for i in trad_issues):
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Add alt text to images',
            'detail': 'Image alt text helps Google image search AND helps AI understand your visual content. Every image should have descriptive alt text.',
            'effort': 'Low (30 minutes)',
        })
        quick.append('Add descriptive alt text to all homepage images')
    if any('internal link' in i.lower() for i in trad_issues):
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Improve internal linking',
            'detail': 'Internal links help search engines discover all your pages and understand site structure. Link from your homepage to key service and content pages.',
            'effort': 'Low (30 minutes)',
        })
    if any('noindex' in i.lower() for i in trad_issues):
        recs.append({
            'priority': 'HIGH',
            'title': 'Remove noindex directive',
            'detail': 'Your homepage has a noindex tag which tells Google NOT to include it in search results. This is almost certainly a mistake and must be fixed immediately.',
            'effort': 'Low (5 minutes)',
        })
        quick.append('URGENT: Remove noindex from homepage meta robots tag')
    if any('lang attribute' in i.lower() for i in trad_issues):
        quick.append('Add lang="en" to your <html> tag')

    if not quick:
        if audit['overall_score'] >= 60:
            quick.append('Your fundamentals are strong — focus on content depth and FAQ expansion')
        else:
            quick.append('Start with robots.txt and llms.txt — these are free, fast wins')

    audit['recommendations'] = recs
    audit['quick_wins'] = quick

    return audit


# ---------------------------------------------------------------------------
# PDF generation endpoint
# ---------------------------------------------------------------------------

def _generate_audit_pdf(audit):
    """
    Generate a professional branded PDF from audit results.
    Returns (pdf_bytes, filename).
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable, KeepTogether,
    )
    from reportlab.pdfgen import canvas as pdfcanvas

    import io

    # Brand colors
    TEAL = HexColor('#0D9488')
    TEAL_DARK = HexColor('#0F766E')
    DARK = HexColor('#0F172A')
    SLATE = HexColor('#1E293B')
    MUTED = HexColor('#64748B')
    GREEN = HexColor('#059669')
    AMBER = HexColor('#D97706')
    RED = HexColor('#DC2626')
    PURPLE = HexColor('#7C3AED')
    LIGHT_BG = HexColor('#F1F5F9')
    CARD_BG = HexColor('#F8FAFC')
    WHITE = HexColor('#FFFFFF')

    buffer = io.BytesIO()
    page_w, page_h = letter

    # Custom page template with branded header/footer
    def _header_footer(canvas, doc):
        canvas.saveState()
        # ── Header bar ──
        canvas.setFillColor(SLATE)
        canvas.rect(0, page_h - 42, page_w, 42, fill=1, stroke=0)
        # Teal accent line under header
        canvas.setFillColor(TEAL)
        canvas.rect(0, page_h - 45, page_w, 3, fill=1, stroke=0)
        # Brand text
        canvas.setFillColor(WHITE)
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawString(54, page_h - 28, 'SALESSIGNAL')
        canvas.setFillColor(TEAL)
        canvas.drawString(54 + canvas.stringWidth('SALESSIGNAL', 'Helvetica-Bold', 11), page_h - 28, 'AI')
        # Right side — report type
        canvas.setFillColor(HexColor('#94A3B8'))
        canvas.setFont('Helvetica', 8)
        canvas.drawRightString(page_w - 54, page_h - 28, 'SEO + AI READINESS AUDIT')

        # ── Footer ──
        canvas.setFillColor(SLATE)
        canvas.rect(0, 0, page_w, 36, fill=1, stroke=0)
        canvas.setFillColor(TEAL)
        canvas.rect(0, 36, page_w, 2, fill=1, stroke=0)
        # Footer text — left: brand, center: confidential, right: page number
        canvas.setFillColor(HexColor('#94A3B8'))
        canvas.setFont('Helvetica', 6.5)
        canvas.drawString(54, 14, 'salessignalai.com')
        canvas.drawRightString(page_w - 54, 14, f'Page {doc.page}')
        canvas.setFillColor(TEAL)
        canvas.setFont('Helvetica', 6.5)
        canvas.drawCentredString(page_w / 2, 14, 'Confidential — Prepared exclusively for the recipient')

        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=60, bottomMargin=52,
        leftMargin=54, rightMargin=54,
    )

    styles = getSampleStyleSheet()
    # Custom styles
    styles.add(ParagraphStyle('CoverTitle', fontName='Helvetica-Bold', fontSize=32, textColor=DARK, spaceAfter=4, leading=38))
    styles.add(ParagraphStyle('CoverSub', fontName='Helvetica', fontSize=13, textColor=MUTED, spaceAfter=6))
    styles.add(ParagraphStyle('CoverURL', fontName='Courier', fontSize=12, textColor=TEAL, spaceAfter=24))
    styles.add(ParagraphStyle('SectionHead', fontName='Helvetica-Bold', fontSize=15, textColor=TEAL_DARK, spaceBefore=18, spaceAfter=6))
    styles.add(ParagraphStyle('SubHead', fontName='Helvetica-Bold', fontSize=11, textColor=SLATE, spaceBefore=10, spaceAfter=3))
    styles.add(ParagraphStyle('Body10', fontName='Helvetica', fontSize=9.5, textColor=SLATE, spaceAfter=4, leading=14))
    styles.add(ParagraphStyle('FindingGood', fontName='Helvetica', fontSize=9, textColor=GREEN, leftIndent=12, spaceAfter=2, leading=13))
    styles.add(ParagraphStyle('FindingBad', fontName='Helvetica', fontSize=9, textColor=RED, leftIndent=12, spaceAfter=2, leading=13))
    styles.add(ParagraphStyle('RecTitle', fontName='Helvetica-Bold', fontSize=10, textColor=SLATE, spaceAfter=1))
    styles.add(ParagraphStyle('RecBody', fontName='Helvetica', fontSize=9, textColor=MUTED, leftIndent=12, spaceAfter=6, leading=13))
    styles.add(ParagraphStyle('FooterLine', fontName='Helvetica', fontSize=8, textColor=MUTED, alignment=TA_CENTER))
    styles.add(ParagraphStyle('CenterBig', fontName='Helvetica-Bold', fontSize=48, textColor=DARK, alignment=TA_CENTER))

    story = []
    score = audit.get('overall_score', 0)
    grade = audit.get('grade', '?')
    gc = GREEN if score >= 60 else AMBER if score >= 40 else RED

    # ════════════════════════════════════════
    # PAGE 1 — COVER
    # ════════════════════════════════════════
    story.append(Spacer(1, 1.2 * inch))
    story.append(Paragraph('Digital Presence<br/>Audit Report', styles['CoverTitle']))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width='40%', color=TEAL, thickness=3, hAlign='LEFT'))
    story.append(Spacer(1, 14))
    story.append(Paragraph(audit.get('url', 'Unknown'), styles['CoverURL']))
    story.append(Paragraph(f'Prepared: {audit.get("audit_date", "N/A")}', styles['CoverSub']))
    story.append(Spacer(1, 40))

    # Big score display
    interp_map = {
        'A': 'AI-Optimized', 'B': 'Partially Optimized',
        'C': 'Needs Work', 'D': 'Not AI-Ready', 'F': 'Not AI-Ready',
    }
    interp = interp_map.get(grade, 'Unknown')

    score_box = Table(
        [[
            Paragraph(f'<font size="52" color="{gc.hexval()}">{score}</font><font size="16" color="{MUTED.hexval()}">/100</font>', ParagraphStyle('x', alignment=TA_CENTER)),
            Paragraph(f'<font size="28" color="{gc.hexval()}">Grade {grade}</font><br/><font size="11" color="{MUTED.hexval()}">{interp}</font>', ParagraphStyle('x', alignment=TA_CENTER, leading=20)),
        ]],
        colWidths=[2.8 * inch, 3.5 * inch],
        rowHeights=[1.1 * inch],
    )
    score_box.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (0, 0), LIGHT_BG),
        ('BOX', (0, 0), (-1, -1), 1.5, HexColor('#E2E8F0')),
        ('LINEAFTER', (0, 0), (0, -1), 1, HexColor('#E2E8F0')),
    ]))
    story.append(score_box)

    story.append(Spacer(1, 40))
    story.append(Paragraph(
        'This report analyzes your website across traditional SEO fundamentals and '
        'AI search readiness (GEO/AEO). It identifies what search engines and AI assistants '
        'like ChatGPT, Perplexity, and Google AI Overviews can see about your business.',
        styles['Body10']
    ))

    story.append(Spacer(1, 0.6 * inch))
    story.append(HRFlowable(width='100%', color=TEAL, thickness=1.5))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        'Courtesy of <b>SalesSignalAI</b>  |  salessignalai.com  |  support@salessignalai.com',
        styles['FooterLine']
    ))

    story.append(PageBreak())

    # ════════════════════════════════════════
    # PAGE 2 — SCORE BREAKDOWN
    # ════════════════════════════════════════
    story.append(Paragraph('Score Breakdown', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=TEAL, thickness=1.5))
    story.append(Spacer(1, 10))

    cat_labels = {
        'robots_txt': ('AI Crawlability', 'Can AI crawlers access your site?'),
        'llms_txt': ('LLM Manifest', 'Do you have a guide for AI models?'),
        'sitemap': ('Sitemap', 'Can search engines discover all your pages?'),
        'homepage': ('Homepage Quality', 'Meta tags, schema, content structure'),
        'key_pages': ('Key Pages', 'About, FAQ, Contact, Blog, Pricing'),
        'traditional_seo': ('Traditional SEO', 'Alt tags, links, performance, meta'),
    }

    # Build table rows
    bd_data = [[
        Paragraph('<b>Category</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE)),
        Paragraph('<b>Score</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE, alignment=TA_CENTER)),
        Paragraph('<b>Status</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE, alignment=TA_CENTER)),
        Paragraph('<b>What It Checks</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE)),
    ]]
    for key, (label, desc) in cat_labels.items():
        cat = audit.get('categories', {}).get(key, {})
        s = cat.get('score', 0)
        mx = cat.get('max_score', 10)
        pct = round((s / mx) * 100) if mx > 0 else 0
        st = 'PASS' if pct >= 70 else 'FAIR' if pct >= 40 else 'FAIL'
        sc = GREEN if pct >= 70 else AMBER if pct >= 40 else RED
        bd_data.append([
            Paragraph(f'<b>{label}</b>', ParagraphStyle('td', fontName='Helvetica-Bold', fontSize=9, textColor=SLATE)),
            Paragraph(f'<b>{s}/{mx}</b>', ParagraphStyle('td', fontName='Courier-Bold', fontSize=9.5, textColor=sc, alignment=TA_CENTER)),
            Paragraph(f'<b>{st}</b>', ParagraphStyle('td', fontName='Helvetica-Bold', fontSize=8, textColor=sc, alignment=TA_CENTER)),
            Paragraph(desc, ParagraphStyle('td', fontName='Helvetica', fontSize=8, textColor=MUTED)),
        ])

    bd_table = Table(bd_data, colWidths=[1.6 * inch, 0.8 * inch, 0.7 * inch, 3.1 * inch])
    bd_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TEAL_DARK),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, CARD_BG]),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#E2E8F0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(bd_table)

    # ════════════════════════════════════════
    # DETAILED FINDINGS
    # ════════════════════════════════════════
    story.append(Spacer(1, 16))
    story.append(Paragraph('Detailed Findings', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=TEAL, thickness=1.5))
    story.append(Spacer(1, 6))

    for key, (label, _) in cat_labels.items():
        cat = audit.get('categories', {}).get(key, {})
        s = cat.get('score', 0)
        mx = cat.get('max_score', 10)
        pct = round((s / mx) * 100) if mx > 0 else 0
        sc = GREEN if pct >= 70 else AMBER if pct >= 40 else RED

        items = []
        items.append(Paragraph(
            f'<font color="{sc.hexval()}">{s}/{mx}</font>  {label}',
            styles['SubHead']
        ))
        for f in cat.get('findings', []):
            items.append(Paragraph(f'+ {f}', styles['FindingGood']))
        for i in cat.get('issues', []):
            items.append(Paragraph(f'- {i}', styles['FindingBad']))
        items.append(Spacer(1, 6))
        story.append(KeepTogether(items))

    story.append(Spacer(1, 12))

    # ════════════════════════════════════════
    # RECOMMENDATIONS (flows naturally, no forced page break)
    # ════════════════════════════════════════
    story.append(Paragraph('Priority Recommendations', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=TEAL, thickness=1.5))
    story.append(Spacer(1, 8))

    for idx, rec in enumerate(audit.get('recommendations', []), 1):
        priority = rec.get('priority', 'MEDIUM')
        pc = RED.hexval() if priority == 'HIGH' else AMBER.hexval()
        story.append(KeepTogether([
            Paragraph(
                f'{idx}. <font color="{pc}"><b>[{priority}]</b></font>  {rec.get("title", "")}',
                styles['RecTitle']
            ),
            Paragraph(rec.get('detail', ''), styles['RecBody']),
            Paragraph(
                f'<font color="{PURPLE.hexval()}">Estimated effort: {rec.get("effort", "Unknown")}</font>',
                ParagraphStyle('eff', fontName='Helvetica', fontSize=8, leftIndent=12, spaceAfter=10, textColor=PURPLE)
            ),
        ]))

    # ── Quick Wins ──
    story.append(Spacer(1, 10))
    story.append(Paragraph('Quick Wins (Do This Week)', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=TEAL, thickness=1.5))
    story.append(Spacer(1, 6))
    for win in audit.get('quick_wins', []):
        story.append(Paragraph(f'>> {win}', styles['Body10']))

    # ── CTA Footer ──
    story.append(Spacer(1, 30))
    cta_data = [[Paragraph(
        '<b>Ready to improve your score?</b><br/>'
        '<font size="9" color="#64748B">Our team can implement every recommendation in this report. '
        'Contact us to get started.</font><br/><br/>'
        '<font size="10" color="#0D9488"><b>salessignalai.com</b>  |  support@salessignalai.com</font>',
        ParagraphStyle('cta', fontName='Helvetica', fontSize=11, textColor=SLATE, alignment=TA_CENTER, leading=16),
    )]]
    cta_table = Table(cta_data, colWidths=[6.2 * inch])
    cta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), CARD_BG),
        ('BOX', (0, 0), (-1, -1), 1.5, TEAL),
        ('TOPPADDING', (0, 0), (-1, -1), 18),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 18),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
    ]))
    story.append(cta_table)

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buffer.seek(0)

    domain = audit.get('url', 'unknown').replace('https://', '').replace('http://', '').replace('/', '_').rstrip('_')
    filename = f'Digital_Presence_Audit_{domain}_{datetime.now().strftime("%Y%m%d")}.pdf'

    return buffer.getvalue(), filename


@login_required
@require_POST
def geo_audit_pdf(request):
    """Generate and download a professional PDF from audit results."""
    import logging
    logger = logging.getLogger(__name__)

    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Admin access required'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({'error': f'Invalid JSON: {str(e)}'}, status=400)

    audit = data.get('audit', {})
    if not audit:
        return JsonResponse({'error': 'No audit data provided'}, status=400)

    try:
        pdf_bytes, filename = _generate_audit_pdf(audit)
    except ImportError:
        return JsonResponse({'error': 'reportlab not installed on server'}, status=500)
    except Exception as e:
        logger.exception('PDF generation failed')
        return JsonResponse({'error': f'PDF generation failed: {str(e)}'}, status=500)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Email report endpoint
# ---------------------------------------------------------------------------

@login_required
@require_POST
def geo_audit_email(request):
    """Generate PDF and email it with a branded HTML template."""
    import logging
    logger = logging.getLogger(__name__)

    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Admin access required'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({'error': f'Invalid JSON: {str(e)}'}, status=400)

    audit = data.get('audit', {})
    to_email = (data.get('to_email') or '').strip()
    recipient_name = (data.get('recipient_name') or data.get('sender_name') or '').strip()

    if not audit:
        return JsonResponse({'error': 'No audit data provided'}, status=400)
    if not to_email or '@' not in to_email:
        return JsonResponse({'error': 'Valid email address required'}, status=400)

    # Generate PDF
    try:
        pdf_bytes, filename = _generate_audit_pdf(audit)
    except ImportError:
        return JsonResponse({'error': 'reportlab not installed on server'}, status=500)
    except Exception as e:
        logger.exception('PDF generation failed for email')
        return JsonResponse({'error': f'PDF generation failed: {str(e)}'}, status=500)

    score = audit.get('overall_score', 0)
    grade = audit.get('grade', '?')
    url = audit.get('url', 'your website')
    grade_color = '#059669' if score >= 60 else '#D97706' if score >= 40 else '#DC2626'

    # Recommendations summary for email body
    rec_html = ''
    for rec in audit.get('recommendations', [])[:3]:
        rec_html += f'<li style="margin-bottom:8px;color:#334155;font-size:14px;">{rec.get("title", "")}</li>'

    greeting = f'Hi {recipient_name},' if recipient_name else 'Hi there,'

    # ── Branded HTML email ──
    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5F9;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

<!-- Header -->
<tr><td style="background:#0F172A;padding:20px 32px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td><span style="font-size:18px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">SALESSIGNAL</span><span style="font-size:18px;font-weight:700;color:#0D9488;">AI</span></td>
<td align="right"><span style="font-size:11px;color:#94A3B8;letter-spacing:1px;">DIGITAL PRESENCE AUDIT</span></td>
</tr>
</table>
</td></tr>

<!-- Teal accent -->
<tr><td style="background:linear-gradient(90deg,#0D9488,#0F766E);height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>

<!-- Score Banner -->
<tr><td style="padding:32px 32px 24px;text-align:center;">
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td align="center">
<div style="display:inline-block;background:{grade_color}10;border:3px solid {grade_color};border-radius:50%;width:90px;height:90px;line-height:90px;text-align:center;">
<span style="font-size:36px;font-weight:800;color:{grade_color};font-family:'Courier New',monospace;">{score}</span>
</div>
<div style="margin-top:8px;font-size:20px;font-weight:700;color:{grade_color};">Grade {grade}</div>
<div style="margin-top:4px;font-size:13px;color:#64748B;">{url}</div>
</td>
</tr></table>
</td></tr>

<!-- Divider -->
<tr><td style="padding:0 32px;"><div style="border-top:1px solid #E2E8F0;"></div></td></tr>

<!-- Body -->
<tr><td style="padding:24px 32px;">
<p style="font-size:15px;color:#1E293B;line-height:1.6;margin:0 0 16px;">{greeting}</p>
<p style="font-size:14px;color:#334155;line-height:1.6;margin:0 0 16px;">
We ran a comprehensive audit on <strong>{url}</strong> covering traditional SEO, AI search readiness, and digital presence signals. Your overall score is <strong style="color:{grade_color};">{score}/100</strong>.
</p>
<p style="font-size:14px;color:#334155;line-height:1.6;margin:0 0 8px;"><strong>Top priorities:</strong></p>
<ul style="padding-left:20px;margin:0 0 20px;">{rec_html if rec_html else '<li style="color:#059669;font-size:14px;">Looking good! No critical issues found.</li>'}</ul>
<p style="font-size:14px;color:#334155;line-height:1.6;margin:0 0 24px;">
The full report is attached as a PDF with detailed findings and implementation recommendations.
</p>

<!-- CTA Button -->
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<a href="https://salessignalai.com" style="display:inline-block;background:#0D9488;color:#FFFFFF;font-size:14px;font-weight:700;padding:14px 36px;border-radius:8px;text-decoration:none;letter-spacing:0.3px;">Learn How We Can Help</a>
</td></tr></table>
</td></tr>

<!-- Footer -->
<tr><td style="background:#F8FAFC;padding:20px 32px;border-top:1px solid #E2E8F0;">
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td>
<span style="font-size:12px;font-weight:700;color:#0F172A;">SALESSIGNAL</span><span style="font-size:12px;font-weight:700;color:#0D9488;">AI</span>
<div style="font-size:11px;color:#94A3B8;margin-top:4px;">Multi-State Lead Intelligence</div>
</td>
<td align="right" style="font-size:11px;color:#64748B;line-height:1.6;">
salessignalai.com<br/>
support@salessignalai.com
</td>
</tr></table>
</td></tr>

</table>
</td></tr></table>
</body></html>"""

    # Send via SendGrid with attachment
    from django.conf import settings as djsettings
    api_key = getattr(djsettings, 'SENDGRID_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'SendGrid API key not configured'}, status=500)

    try:
        import sendgrid
        from sendgrid.helpers.mail import (
            Mail, Email, To, Content, Attachment,
            FileContent, FileName, FileType, Disposition,
        )
        import base64

        sg = sendgrid.SendGridAPIClient(api_key)

        from_email = getattr(djsettings, 'ALERT_FROM_EMAIL', 'reports@salessignal.ai')
        domain = url.replace('https://', '').replace('http://', '').rstrip('/')

        message = Mail(
            from_email=Email(from_email, 'SalesSignalAI'),
            to_emails=To(to_email),
            subject=f'Your Digital Presence Audit — {domain} ({score}/100)',
        )
        message.add_content(Content('text/html', html_body))

        # Attach PDF
        encoded_pdf = base64.b64encode(pdf_bytes).decode('ascii')
        attachment = Attachment(
            FileContent(encoded_pdf),
            FileName(filename),
            FileType('application/pdf'),
            Disposition('attachment'),
        )
        message.add_attachment(attachment)

        response = sg.send(message)

        if response.status_code in (200, 201, 202):
            return JsonResponse({
                'ok': True,
                'message': f'Report emailed to {to_email}',
                'message_id': response.headers.get('X-Message-Id', ''),
            })
        else:
            return JsonResponse({'error': f'SendGrid error: status {response.status_code}'}, status=500)

    except ImportError:
        return JsonResponse({'error': 'sendgrid package not installed'}, status=500)
    except Exception as e:
        return JsonResponse({'error': f'Email failed: {str(e)}'}, status=500)
