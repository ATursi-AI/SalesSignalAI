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
        'About': ['/about', '/about/', '/about-us'],
        'FAQ': ['/faq', '/faq/', '/faqs', '/frequently-asked-questions'],
        'Contact': ['/contact', '/contact/', '/contact-us'],
        'Blog': ['/blog', '/blog/', '/articles', '/news'],
        'Pricing': ['/pricing', '/pricing/', '/plans'],
    }
    for page_name, paths in page_checks.items():
        found = False
        for path in paths:
            s, _ = _fetch(f'{base_url}{path}', timeout=6)
            if s == 200:
                found = True
                break
        key_pages['pages_found'][page_name] = found
        if found:
            key_pages['score'] += 3
            key_pages['findings'].append(f'{page_name} page found')
        else:
            key_pages['issues'].append(f'No {page_name} page detected')

    key_pages['score'] = min(key_pages['max_score'], key_pages['score'])
    audit['categories']['key_pages'] = key_pages

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

@login_required
@require_POST
def geo_audit_pdf(request):
    """Generate a professional PDF from audit results."""
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Admin access required'}, status=403)

    data = json.loads(request.body)
    audit = data.get('audit', {})

    if not audit:
        return JsonResponse({'error': 'No audit data provided'}, status=400)

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.lib.colors import HexColor
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, HRFlowable,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        return JsonResponse({'error': 'reportlab not installed on server'}, status=500)

    import io
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )

    # Colors
    TEAL = HexColor('#0D9488')
    DARK = HexColor('#1E293B')
    MUTED = HexColor('#64748B')
    GREEN = HexColor('#059669')
    AMBER = HexColor('#F59E0B')
    RED = HexColor('#EF4444')
    PURPLE = HexColor('#7C3AED')
    LIGHT_BG = HexColor('#F8FAFC')

    # Styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'ReportTitle', parent=styles['Title'],
        fontSize=28, textColor=DARK, spaceAfter=6,
        fontName='Helvetica-Bold',
    ))
    styles.add(ParagraphStyle(
        'ReportSubtitle', parent=styles['Normal'],
        fontSize=12, textColor=MUTED, spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        'SectionHead', parent=styles['Heading2'],
        fontSize=16, textColor=TEAL, spaceBefore=20, spaceAfter=8,
        fontName='Helvetica-Bold',
    ))
    styles.add(ParagraphStyle(
        'Finding', parent=styles['Normal'],
        fontSize=10, textColor=GREEN, leftIndent=15, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        'Issue', parent=styles['Normal'],
        fontSize=10, textColor=RED, leftIndent=15, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        'RecTitle', parent=styles['Normal'],
        fontSize=11, textColor=DARK, fontName='Helvetica-Bold', spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        'RecDetail', parent=styles['Normal'],
        fontSize=10, textColor=MUTED, leftIndent=15, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        'BodyText10', parent=styles['Normal'],
        fontSize=10, textColor=DARK, spaceAfter=6,
    ))

    story = []

    # ── Title Page ──
    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph('GEO/AEO Audit Report', styles['ReportTitle']))
    story.append(Paragraph(
        f'{audit.get("url", "Unknown")}',
        styles['ReportSubtitle']
    ))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f'Audit Date: {audit.get("audit_date", "N/A")}',
        styles['ReportSubtitle']
    ))
    story.append(Spacer(1, 30))

    # Score display
    score = audit.get('overall_score', 0)
    grade = audit.get('grade', '?')
    grade_color = GREEN if score >= 60 else AMBER if score >= 40 else RED

    score_data = [[
        Paragraph(f'<font size="36" color="{grade_color.hexval()}">{score}</font><font size="14" color="{MUTED.hexval()}">/100</font>', styles['Normal']),
        Paragraph(f'<font size="36" color="{grade_color.hexval()}">Grade {grade}</font>', styles['Normal']),
    ]]
    score_table = Table(score_data, colWidths=[3 * inch, 3 * inch])
    score_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 20),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 20),
    ]))
    story.append(score_table)

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width='100%', color=TEAL, thickness=2))
    story.append(Spacer(1, 10))

    # Score interpretation
    if score >= 80:
        interp = 'AI-Optimized — This site is well-positioned for AI search visibility.'
    elif score >= 60:
        interp = 'Partially Optimized — Good foundation but significant gaps remain.'
    elif score >= 40:
        interp = 'Needs Work — Missing key elements for AI search discoverability.'
    else:
        interp = 'Not AI-Ready — Major improvements needed to be visible in AI search.'
    story.append(Paragraph(interp, styles['BodyText10']))

    story.append(Spacer(1, 15))
    story.append(Paragraph(
        'Powered by SalesSignalAI — salessignalai.com',
        ParagraphStyle('Footer', parent=styles['Normal'], fontSize=9, textColor=MUTED, alignment=TA_CENTER)
    ))

    story.append(PageBreak())

    # ── Score Breakdown Table ──
    story.append(Paragraph('Score Breakdown', styles['SectionHead']))

    cat_labels = {
        'robots_txt': 'AI Crawlability (robots.txt)',
        'llms_txt': 'LLM Manifest (llms.txt)',
        'sitemap': 'Sitemap',
        'homepage': 'Homepage Analysis',
        'key_pages': 'Key Pages',
    }

    breakdown_data = [['Category', 'Score', 'Status']]
    for key, label in cat_labels.items():
        cat = audit.get('categories', {}).get(key, {})
        s = cat.get('score', 0)
        mx = cat.get('max_score', 10)
        pct = round((s / mx) * 100) if mx > 0 else 0
        status_text = 'Good' if pct >= 70 else 'Fair' if pct >= 40 else 'Poor'
        status_color = GREEN.hexval() if pct >= 70 else AMBER.hexval() if pct >= 40 else RED.hexval()
        breakdown_data.append([
            label,
            f'{s}/{mx}',
            Paragraph(f'<font color="{status_color}">{status_text}</font>', styles['Normal']),
        ])

    breakdown_table = Table(breakdown_data, colWidths=[3.5 * inch, 1.2 * inch, 1.5 * inch])
    breakdown_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TEAL),
        ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#FFFFFF')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (2, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#FFFFFF'), LIGHT_BG]),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(breakdown_table)
    story.append(Spacer(1, 20))

    # ── Detailed Findings ──
    story.append(Paragraph('Detailed Findings', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=HexColor('#E2E8F0'), thickness=1))
    story.append(Spacer(1, 8))

    for key, label in cat_labels.items():
        cat = audit.get('categories', {}).get(key, {})
        s = cat.get('score', 0)
        mx = cat.get('max_score', 10)
        story.append(Paragraph(f'{label} ({s}/{mx})', styles['RecTitle']))

        for finding in cat.get('findings', []):
            story.append(Paragraph(f'+ {finding}', styles['Finding']))
        for issue in cat.get('issues', []):
            story.append(Paragraph(f'- {issue}', styles['Issue']))
        story.append(Spacer(1, 10))

    story.append(PageBreak())

    # ── Recommendations ──
    story.append(Paragraph('Priority Recommendations', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=HexColor('#E2E8F0'), thickness=1))
    story.append(Spacer(1, 8))

    for i, rec in enumerate(audit.get('recommendations', []), 1):
        priority = rec.get('priority', 'MEDIUM')
        pcolor = RED.hexval() if priority == 'HIGH' else AMBER.hexval()
        story.append(Paragraph(
            f'{i}. <font color="{pcolor}">[{priority}]</font> {rec.get("title", "")}',
            styles['RecTitle']
        ))
        story.append(Paragraph(rec.get('detail', ''), styles['RecDetail']))
        story.append(Paragraph(
            f'Estimated effort: {rec.get("effort", "Unknown")}',
            ParagraphStyle('Effort', parent=styles['Normal'], fontSize=9, textColor=PURPLE, leftIndent=15, spaceAfter=12)
        ))

    # ── Quick Wins ──
    story.append(Spacer(1, 15))
    story.append(Paragraph('Quick Wins (Do This Week)', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=HexColor('#E2E8F0'), thickness=1))
    story.append(Spacer(1, 8))

    for win in audit.get('quick_wins', []):
        story.append(Paragraph(f'>> {win}', styles['BodyText10']))

    # ── Footer ──
    story.append(Spacer(1, 40))
    story.append(HRFlowable(width='100%', color=TEAL, thickness=2))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        'This report was generated by SalesSignalAI\'s GEO Audit Tool. '
        'For implementation help, contact us at salessignalai.com',
        ParagraphStyle('FooterNote', parent=styles['Normal'], fontSize=9, textColor=MUTED, alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)

    # Generate filename
    domain = audit.get('url', 'unknown').replace('https://', '').replace('http://', '').replace('/', '_')
    filename = f'GEO_Audit_{domain}_{datetime.now().strftime("%Y%m%d")}.pdf'

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
