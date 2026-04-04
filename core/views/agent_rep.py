"""
Agent REP — Reputation Intelligence Report
Scrapes public review platforms (Google, Yelp, BBB, Trustpilot, Angi)
for a business and generates a branded reputation analysis PDF.
Zero API cost — all public data scraping.
"""

import json
import re
import urllib.request
import urllib.error
import ssl
import hashlib
from datetime import datetime
from urllib.parse import quote_plus

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.core.cache import cache


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url, timeout=12):
    """Fetch a URL and return (status_code, body_text)."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        body = resp.read(800_000).decode('utf-8', errors='replace')
        return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception:
        return 0, ''


# ---------------------------------------------------------------------------
# Page view
# ---------------------------------------------------------------------------

@login_required
def agent_rep_tool(request):
    """Render the Agent REP reputation intelligence page."""
    if not (request.user.is_staff or request.user.is_superuser):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Admin access required.')
    return render(request, 'tools/agent_rep.html')


# ---------------------------------------------------------------------------
# AJAX endpoint — full reputation scan
# ---------------------------------------------------------------------------

@login_required
@require_POST
def agent_rep_api(request):
    """Run a reputation scan on a business name + location."""
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Admin access required'}, status=403)

    data = json.loads(request.body)
    business_name = (data.get('business_name') or '').strip()
    location = (data.get('location') or '').strip()

    if not business_name:
        return JsonResponse({'error': 'Business name is required'}, status=400)

    # Cache key
    ck = f'agent_rep:{hashlib.md5(f"{business_name}|{location}".lower().encode()).hexdigest()}'
    cached = cache.get(ck)
    if cached:
        return JsonResponse({'ok': True, 'result': cached})

    result = _run_reputation_scan(business_name, location)
    cache.set(ck, result, 60 * 60)  # 1 hour
    return JsonResponse({'ok': True, 'result': result})


# ---------------------------------------------------------------------------
# Reputation Scan Engine
# ---------------------------------------------------------------------------

def _run_reputation_scan(business_name, location):
    """
    Scrape public review platforms for business reputation data.
    Returns structured reputation report dict.
    """
    report = {
        'business_name': business_name,
        'location': location or 'Not specified',
        'scan_date': datetime.now().strftime('%B %d, %Y'),
        'platforms': {},
        'overall_rating': 0,
        'total_reviews': 0,
        'reputation_score': 0,
        'grade': 'F',
        'sentiment': {'positive': 0, 'neutral': 0, 'negative': 0},
        'recommendations': [],
        'quick_wins': [],
        'response_rate_estimate': 'Unknown',
        'key_themes': [],
    }

    search_query = f"{business_name} {location}".strip()

    # ── Google Reviews (via Google search scrape) ──
    google_data = _scrape_google_reviews(search_query)
    if google_data:
        report['platforms']['google'] = google_data

    # ── Yelp ──
    yelp_data = _scrape_yelp(business_name, location)
    if yelp_data:
        report['platforms']['yelp'] = yelp_data

    # ── BBB ──
    bbb_data = _scrape_bbb(business_name, location)
    if bbb_data:
        report['platforms']['bbb'] = bbb_data

    # ── Trustpilot ──
    trustpilot_data = _scrape_trustpilot(business_name)
    if trustpilot_data:
        report['platforms']['trustpilot'] = trustpilot_data

    # ── Calculate aggregates ──
    _calculate_aggregates(report)

    # ── Generate recommendations ──
    _generate_recommendations(report)

    return report


def _scrape_google_reviews(query):
    """Scrape Google search for business rating/review count."""
    encoded = quote_plus(query + ' reviews')
    url = f'https://www.google.com/search?q={encoded}'
    status, body = _fetch(url)

    if status != 200 or not body:
        return None

    result = {
        'platform': 'Google',
        'rating': 0,
        'review_count': 0,
        'url': f'https://www.google.com/search?q={encoded}',
        'found': False,
        'highlights': [],
    }

    # Try to extract rating from Google's knowledge panel
    # Pattern: "4.5 stars" or "Rating: 4.5"
    rating_match = re.search(
        r'(?:rating|rated)\s*[:\s]*(\d+\.?\d*)\s*/?\s*5|'
        r'(\d+\.?\d*)\s*(?:out of 5|stars?|star rating)',
        body, re.IGNORECASE
    )
    if rating_match:
        r = float(rating_match.group(1) or rating_match.group(2))
        if 1.0 <= r <= 5.0:
            result['rating'] = r
            result['found'] = True

    # Try to get review count
    count_match = re.search(
        r'(\d[\d,]*)\s*(?:reviews?|Google reviews?|ratings?)',
        body, re.IGNORECASE
    )
    if count_match:
        result['review_count'] = int(count_match.group(1).replace(',', ''))
        result['found'] = True

    # Extract snippet highlights
    snippets = re.findall(r'<span[^>]*>([^<]{30,200})</span>', body)
    review_snippets = [s for s in snippets if any(w in s.lower() for w in
                       ['great', 'terrible', 'excellent', 'worst', 'best', 'recommend',
                        'friendly', 'professional', 'rude', 'slow', 'fast', 'quality'])]
    result['highlights'] = review_snippets[:5]

    return result if result['found'] else None


def _scrape_yelp(business_name, location):
    """Scrape Yelp search results for business info."""
    name_encoded = quote_plus(business_name)
    loc_encoded = quote_plus(location) if location else ''
    url = f'https://www.yelp.com/search?find_desc={name_encoded}&find_loc={loc_encoded}'
    status, body = _fetch(url)

    if status != 200 or not body:
        return None

    result = {
        'platform': 'Yelp',
        'rating': 0,
        'review_count': 0,
        'url': url,
        'found': False,
        'highlights': [],
        'categories': [],
        'price_range': '',
    }

    # Yelp embeds JSON-LD or structured data
    # Look for rating in aria-label or structured data
    rating_match = re.search(
        r'(\d+\.?\d*)\s*star rating|'
        r'aria-label="(\d+\.?\d*)\s*star|'
        r'"ratingValue"\s*:\s*"?(\d+\.?\d*)',
        body, re.IGNORECASE
    )
    if rating_match:
        r = float(rating_match.group(1) or rating_match.group(2) or rating_match.group(3))
        if 1.0 <= r <= 5.0:
            result['rating'] = r
            result['found'] = True

    count_match = re.search(
        r'(\d[\d,]*)\s*reviews?|'
        r'"reviewCount"\s*:\s*"?(\d+)',
        body, re.IGNORECASE
    )
    if count_match:
        c = count_match.group(1) or count_match.group(2)
        result['review_count'] = int(c.replace(',', ''))
        result['found'] = True

    # Price range
    price_match = re.search(r'(\${1,4})', body)
    if price_match:
        result['price_range'] = price_match.group(1)

    return result if result['found'] else None


def _scrape_bbb(business_name, location):
    """Scrape BBB for business rating."""
    encoded = quote_plus(f"{business_name} {location}".strip())
    url = f'https://www.bbb.org/search?find_text={encoded}'
    status, body = _fetch(url)

    if status != 200 or not body:
        return None

    result = {
        'platform': 'BBB',
        'rating': 0,
        'review_count': 0,
        'bbb_rating': '',
        'accredited': False,
        'url': url,
        'found': False,
        'complaints': 0,
        'highlights': [],
    }

    # BBB letter grade (A+, A, B+, etc.)
    grade_match = re.search(
        r'BBB Rating:\s*([A-F][+\-]?)|'
        r'rating["\s:]+([A-F][+\-]?)',
        body, re.IGNORECASE
    )
    if grade_match:
        result['bbb_rating'] = (grade_match.group(1) or grade_match.group(2)).upper()
        result['found'] = True

    # Check accreditation
    if re.search(r'accredited|BBB Accredited', body, re.IGNORECASE):
        result['accredited'] = True
        result['found'] = True

    # Complaint count
    complaint_match = re.search(r'(\d+)\s*complaints?\s*(?:closed|filed)?', body, re.IGNORECASE)
    if complaint_match:
        result['complaints'] = int(complaint_match.group(1))

    # Customer review rating on BBB
    review_rating = re.search(r'Customer Reviews?\s*(\d+\.?\d*)\s*/\s*5', body, re.IGNORECASE)
    if review_rating:
        result['rating'] = float(review_rating.group(1))

    review_count = re.search(r'(\d+)\s*Customer Reviews?', body, re.IGNORECASE)
    if review_count:
        result['review_count'] = int(review_count.group(1))

    return result if result['found'] else None


def _scrape_trustpilot(business_name):
    """Scrape Trustpilot for business reviews."""
    slug = re.sub(r'[^a-z0-9]+', '-', business_name.lower()).strip('-')
    # Try common domain patterns
    for domain_suffix in ['.com', '.net', '.org', '']:
        url = f'https://www.trustpilot.com/review/{slug}{domain_suffix}'
        status, body = _fetch(url)
        if status == 200 and body and 'TrustScore' in body:
            break
    else:
        # Try search
        search_url = f'https://www.trustpilot.com/search?query={quote_plus(business_name)}'
        status, body = _fetch(search_url)
        url = search_url

    if status != 200 or not body:
        return None

    result = {
        'platform': 'Trustpilot',
        'rating': 0,
        'review_count': 0,
        'url': url,
        'found': False,
        'trust_score': 0,
        'highlights': [],
    }

    # TrustScore
    score_match = re.search(
        r'TrustScore\s*(\d+\.?\d*)|'
        r'"ratingValue"\s*:\s*"?(\d+\.?\d*)',
        body, re.IGNORECASE
    )
    if score_match:
        s = float(score_match.group(1) or score_match.group(2))
        if 0 < s <= 5:
            result['rating'] = s
            result['trust_score'] = s
            result['found'] = True

    count_match = re.search(
        r'(\d[\d,]*)\s*(?:total\s+)?reviews?|'
        r'"reviewCount"\s*:\s*"?(\d[\d,]*)',
        body, re.IGNORECASE
    )
    if count_match:
        c = count_match.group(1) or count_match.group(2)
        result['review_count'] = int(c.replace(',', ''))
        result['found'] = True

    return result if result['found'] else None


# ---------------------------------------------------------------------------
# Aggregate & Scoring
# ---------------------------------------------------------------------------

def _calculate_aggregates(report):
    """Calculate overall reputation score from platform data."""
    total_weighted_rating = 0
    total_weight = 0
    total_reviews = 0

    # Platform weights (Google is most important)
    weights = {'google': 4, 'yelp': 3, 'bbb': 2, 'trustpilot': 2, 'angi': 1}

    for key, platform in report['platforms'].items():
        w = weights.get(key, 1)
        rating = platform.get('rating', 0)
        count = platform.get('review_count', 0)

        if rating > 0:
            total_weighted_rating += rating * w
            total_weight += w
        total_reviews += count

        # Sentiment estimation from rating
        if rating >= 4.0:
            report['sentiment']['positive'] += count
        elif rating >= 3.0:
            report['sentiment']['neutral'] += count
        else:
            report['sentiment']['negative'] += count

    report['total_reviews'] = total_reviews

    if total_weight > 0:
        avg_rating = total_weighted_rating / total_weight
        report['overall_rating'] = round(avg_rating, 1)

        # Reputation score (0-100)
        # Base: rating maps to 0-100 (1 star = 0, 5 stars = 100)
        base_score = ((avg_rating - 1) / 4) * 70  # Up to 70 points from rating

        # Bonus for review volume (up to 15 points)
        if total_reviews >= 200:
            volume_bonus = 15
        elif total_reviews >= 50:
            volume_bonus = 10
        elif total_reviews >= 10:
            volume_bonus = 5
        else:
            volume_bonus = 0

        # Bonus for platform coverage (up to 15 points)
        platform_count = len(report['platforms'])
        coverage_bonus = min(platform_count * 3, 15)

        report['reputation_score'] = min(100, round(base_score + volume_bonus + coverage_bonus))
    else:
        report['reputation_score'] = 0
        report['overall_rating'] = 0

    # Grade
    s = report['reputation_score']
    if s >= 90:
        report['grade'] = 'A'
    elif s >= 80:
        report['grade'] = 'B'
    elif s >= 65:
        report['grade'] = 'C'
    elif s >= 50:
        report['grade'] = 'D'
    else:
        report['grade'] = 'F'


def _generate_recommendations(report):
    """Generate actionable recommendations based on reputation data."""
    recs = []
    quick = []
    score = report['reputation_score']
    platforms = report['platforms']

    # Missing platforms
    all_platforms = ['google', 'yelp', 'bbb', 'trustpilot']
    missing = [p for p in all_platforms if p not in platforms]

    if 'google' not in platforms:
        recs.append({
            'priority': 'HIGH',
            'title': 'Claim and optimize Google Business Profile',
            'detail': 'Google is the #1 platform customers check before calling. Without a claimed profile, '
                      'you\'re invisible to 70%+ of local searchers. Claim at business.google.com.',
            'effort': 'Low (30 minutes)',
            'impact': 'Critical',
        })
        quick.append('Claim your Google Business Profile at business.google.com')

    if 'yelp' not in platforms:
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Claim Yelp business listing',
            'detail': 'Yelp is the second most-checked review platform. Claiming your listing lets you '
                      'respond to reviews and update business information.',
            'effort': 'Low (20 minutes)',
            'impact': 'High',
        })

    # Low rating recommendations
    for key, platform in platforms.items():
        rating = platform.get('rating', 0)
        if 0 < rating < 3.5:
            recs.append({
                'priority': 'HIGH',
                'title': f'Address low rating on {platform["platform"]} ({rating}/5)',
                'detail': f'Your {platform["platform"]} rating of {rating}/5 is below the 4.0 threshold '
                          'customers expect. Respond to negative reviews professionally and ask satisfied '
                          'customers to leave reviews.',
                'effort': 'Ongoing',
                'impact': 'Critical',
            })
            quick.append(f'Respond to your most recent negative reviews on {platform["platform"]}')

    # Low review volume
    if report['total_reviews'] < 20:
        recs.append({
            'priority': 'HIGH',
            'title': 'Launch a review generation campaign',
            'detail': f'With only {report["total_reviews"]} total reviews across all platforms, you lack '
                      'social proof. Businesses with 50+ reviews get 4x more clicks. '
                      'Start asking every satisfied customer for a review.',
            'effort': 'Medium (set up automated asks)',
            'impact': 'High',
        })
        quick.append('Send a review request to your 10 most recent happy customers')
    elif report['total_reviews'] < 50:
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Increase review volume',
            'detail': f'You have {report["total_reviews"]} reviews — good start but competitors likely have '
                      'more. Aim for 100+ reviews on Google alone to dominate local search.',
            'effort': 'Ongoing',
            'impact': 'Medium',
        })

    # BBB specific
    bbb = platforms.get('bbb', {})
    if bbb and not bbb.get('accredited', False):
        recs.append({
            'priority': 'MEDIUM',
            'title': 'Consider BBB Accreditation',
            'detail': 'BBB Accreditation adds a trust signal that older demographics value heavily. '
                      'It\'s especially important for home services and contractors.',
            'effort': 'Medium (application + annual fee)',
            'impact': 'Medium',
        })

    if bbb and bbb.get('complaints', 0) > 5:
        recs.append({
            'priority': 'HIGH',
            'title': f'Resolve {bbb["complaints"]} BBB complaints',
            'detail': 'Unresolved BBB complaints are a red flag. Each resolved complaint can improve '
                      'your BBB rating. Contact the BBB to work through the resolution process.',
            'effort': 'Medium (depends on complaint complexity)',
            'impact': 'High',
        })

    # Negative sentiment
    neg = report['sentiment']['negative']
    total = report['total_reviews']
    if total > 0 and neg / total > 0.3:
        recs.append({
            'priority': 'HIGH',
            'title': 'High negative sentiment — address root causes',
            'detail': f'Approximately {round(neg/total*100)}% of your reviews appear negative. '
                      'Identify recurring complaints and fix the operational issues causing them '
                      'before investing in marketing.',
            'effort': 'High (operational changes)',
            'impact': 'Critical',
        })

    # Score-based fallback
    if not recs:
        if score >= 80:
            recs.append({
                'priority': 'MEDIUM',
                'title': 'Maintain and expand your strong reputation',
                'detail': 'Your reputation is strong. Focus on maintaining review velocity '
                          'and expanding to new platforms like Trustpilot or industry-specific sites.',
                'effort': 'Low (ongoing)',
                'impact': 'Maintenance',
            })
            quick.append('Keep asking happy customers for reviews — consistency is key')
        else:
            quick.append('Start with responding to your most recent reviews on Google')

    if not quick:
        quick.append('Reply to your 3 most recent reviews on Google (positive and negative)')

    report['recommendations'] = recs
    report['quick_wins'] = quick


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _generate_rep_pdf(report):
    """Generate a branded Reputation Intelligence PDF. Returns (pdf_bytes, filename)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable, KeepTogether,
    )
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
    ORANGE = HexColor('#EA580C')

    buffer = io.BytesIO()
    page_w, page_h = letter

    def _header_footer(canvas, doc):
        canvas.saveState()
        # ── Header bar ──
        canvas.setFillColor(SLATE)
        canvas.rect(0, page_h - 50, page_w, 50, fill=1, stroke=0)
        canvas.setFillColor(ORANGE)
        canvas.rect(0, page_h - 54, page_w, 4, fill=1, stroke=0)
        # Brand
        canvas.setFillColor(WHITE)
        canvas.setFont('Helvetica-Bold', 16)
        canvas.drawString(54, page_h - 34, 'SALESSIGNAL')
        canvas.setFillColor(ORANGE)
        canvas.setFont('Helvetica-Bold', 16)
        canvas.drawString(54 + canvas.stringWidth('SALESSIGNAL', 'Helvetica-Bold', 16), page_h - 34, 'AI')
        # Right side
        canvas.setFillColor(HexColor('#94A3B8'))
        canvas.setFont('Helvetica-Bold', 10)
        canvas.drawRightString(page_w - 54, page_h - 34, 'AGENT REP — REPUTATION INTELLIGENCE')

        # ── Footer ──
        canvas.setFillColor(SLATE)
        canvas.rect(0, 0, page_w, 44, fill=1, stroke=0)
        canvas.setFillColor(ORANGE)
        canvas.rect(0, 44, page_w, 3, fill=1, stroke=0)
        canvas.setFillColor(HexColor('#CBD5E1'))
        canvas.setFont('Helvetica-Bold', 9)
        canvas.drawString(54, 18, 'salessignalai.com  |  (959) 247-2537')
        canvas.drawRightString(page_w - 54, 18, f'Page {doc.page}')
        canvas.setFillColor(ORANGE)
        canvas.setFont('Helvetica', 8)
        canvas.drawCentredString(page_w / 2, 18, 'Confidential — Prepared exclusively for the recipient')
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=80, bottomMargin=60,
        leftMargin=54, rightMargin=54,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('CoverTitle', fontName='Helvetica-Bold', fontSize=32, textColor=DARK, spaceAfter=4, leading=38))
    styles.add(ParagraphStyle('CoverSub', fontName='Helvetica', fontSize=13, textColor=MUTED, spaceAfter=6))
    styles.add(ParagraphStyle('CoverCompany', fontName='Helvetica-Bold', fontSize=20, textColor=DARK, spaceAfter=6))
    styles.add(ParagraphStyle('CoverLocation', fontName='Helvetica', fontSize=12, textColor=TEAL_DARK, spaceAfter=8))
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
    score = report.get('reputation_score', 0)
    grade = report.get('grade', '?')
    gc = GREEN if score >= 70 else AMBER if score >= 50 else RED

    # ════════════════════════════════════════
    # PAGE 1 — COVER
    # ════════════════════════════════════════
    story.append(Spacer(1, 0.5 * inch))
    story.append(Paragraph('Reputation<br/>Intelligence Report', styles['CoverTitle']))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width='40%', color=ORANGE, thickness=3, hAlign='LEFT'))
    story.append(Spacer(1, 14))

    story.append(Paragraph(f'Prepared for {report.get("business_name", "Unknown")}', styles['CoverCompany']))
    story.append(Paragraph(report.get('location', ''), styles['CoverLocation']))
    story.append(Paragraph(f'Scanned: {report.get("scan_date", "N/A")}', styles['CoverSub']))
    story.append(Spacer(1, 24))

    # Score box
    interp_map = {
        'A': 'Excellent Reputation', 'B': 'Strong Reputation',
        'C': 'Average Reputation', 'D': 'Needs Improvement', 'F': 'Reputation At Risk',
    }
    interp = interp_map.get(grade, 'Unknown')

    score_cell = Paragraph(
        f'<font size="56" color="{gc.hexval()}"><b>{score}</b></font>'
        f'<font size="18" color="{MUTED.hexval()}"> /100</font>',
        ParagraphStyle('sc', alignment=TA_CENTER, leading=60),
    )
    grade_cell = Paragraph(
        f'<font size="30" color="{gc.hexval()}"><b>Grade {grade}</b></font><br/>'
        f'<font size="12" color="{MUTED.hexval()}">{interp}</font>',
        ParagraphStyle('gr', alignment=TA_CENTER, leading=24),
    )

    score_box = Table(
        [[score_cell, grade_cell]],
        colWidths=[2.8 * inch, 3.5 * inch],
        rowHeights=[1.3 * inch],
    )
    score_box.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (0, 0), LIGHT_BG),
        ('BACKGROUND', (1, 0), (1, 0), HexColor('#FAFAFA')),
        ('BOX', (0, 0), (-1, -1), 2, HexColor('#E2E8F0')),
        ('LINEAFTER', (0, 0), (0, -1), 1, HexColor('#E2E8F0')),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.append(score_box)

    story.append(Spacer(1, 20))

    # Summary stats row
    overall_rating = report.get('overall_rating', 0)
    total_reviews = report.get('total_reviews', 0)
    platform_count = len(report.get('platforms', {}))

    stats_data = [[
        Paragraph(f'<font size="20" color="{gc.hexval()}"><b>{overall_rating}</b></font><br/>'
                  f'<font size="8" color="{MUTED.hexval()}">Avg Rating (out of 5)</font>',
                  ParagraphStyle('stat', alignment=TA_CENTER, leading=14)),
        Paragraph(f'<font size="20" color="{TEAL_DARK.hexval()}"><b>{total_reviews:,}</b></font><br/>'
                  f'<font size="8" color="{MUTED.hexval()}">Total Reviews</font>',
                  ParagraphStyle('stat', alignment=TA_CENTER, leading=14)),
        Paragraph(f'<font size="20" color="{PURPLE.hexval()}"><b>{platform_count}</b></font><br/>'
                  f'<font size="8" color="{MUTED.hexval()}">Platforms Found</font>',
                  ParagraphStyle('stat', alignment=TA_CENTER, leading=14)),
    ]]
    stats_table = Table(stats_data, colWidths=[2.1 * inch, 2.1 * inch, 2.1 * inch])
    stats_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, -1), CARD_BG),
        ('BOX', (0, 0), (-1, -1), 1, HexColor('#E2E8F0')),
        ('LINEBEFORE', (1, 0), (1, -1), 0.5, HexColor('#E2E8F0')),
        ('LINEBEFORE', (2, 0), (2, -1), 0.5, HexColor('#E2E8F0')),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
    ]))
    story.append(stats_table)

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        'This report analyzes your business reputation across major review platforms. '
        'It identifies what potential customers see when they search for your business online '
        'and provides actionable recommendations to improve your online reputation.',
        styles['Body10']
    ))

    story.append(Spacer(1, 0.3 * inch))
    story.append(HRFlowable(width='100%', color=ORANGE, thickness=1.5))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        'Powered by <b>Agent REP</b> from <b>SalesSignalAI</b>  |  salessignalai.com  |  (959) AI-SALES  |  (959) 247-2537',
        styles['FooterLine']
    ))

    story.append(PageBreak())

    # ════════════════════════════════════════
    # PAGE 2 — PLATFORM BREAKDOWN
    # ════════════════════════════════════════
    story.append(Paragraph('Platform-by-Platform Breakdown', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=ORANGE, thickness=1.5))
    story.append(Spacer(1, 10))

    platform_labels = {
        'google': ('Google Reviews', 'The most important review platform for local businesses'),
        'yelp': ('Yelp', 'Second most-checked review platform'),
        'bbb': ('Better Business Bureau', 'Trust signal for older demographics and B2B'),
        'trustpilot': ('Trustpilot', 'Growing global review platform'),
    }

    # Platform table
    pd_header = [
        Paragraph('<b>Platform</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE)),
        Paragraph('<b>Rating</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE, alignment=TA_CENTER)),
        Paragraph('<b>Reviews</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE, alignment=TA_CENTER)),
        Paragraph('<b>Status</b>', ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=9, textColor=WHITE, alignment=TA_CENTER)),
    ]
    pd_data = [pd_header]

    for key, (label, desc) in platform_labels.items():
        plat = report.get('platforms', {}).get(key)
        if plat:
            r = plat.get('rating', 0)
            rc = plat.get('review_count', 0)
            rc_color = GREEN if r >= 4.0 else AMBER if r >= 3.0 else RED
            status = 'STRONG' if r >= 4.0 else 'FAIR' if r >= 3.0 else 'WEAK'
            rating_str = f'{r}/5' if r > 0 else 'N/A'
        else:
            rc_color = RED
            status = 'NOT FOUND'
            rating_str = '—'
            rc = 0

        pd_data.append([
            Paragraph(f'<b>{label}</b>', ParagraphStyle('td', fontName='Helvetica-Bold', fontSize=9, textColor=SLATE)),
            Paragraph(f'<b>{rating_str}</b>', ParagraphStyle('td', fontName='Courier-Bold', fontSize=9.5, textColor=rc_color, alignment=TA_CENTER)),
            Paragraph(f'{rc:,}' if rc else '—', ParagraphStyle('td', fontName='Helvetica', fontSize=9, textColor=MUTED, alignment=TA_CENTER)),
            Paragraph(f'<b>{status}</b>', ParagraphStyle('td', fontName='Helvetica-Bold', fontSize=8, textColor=rc_color, alignment=TA_CENTER)),
        ])

    pd_table = Table(pd_data, colWidths=[2.2 * inch, 1.0 * inch, 1.0 * inch, 2.0 * inch])
    pd_table.setStyle(TableStyle([
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
    story.append(pd_table)

    # Sentiment breakdown
    story.append(Spacer(1, 20))
    story.append(Paragraph('Sentiment Estimate', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=ORANGE, thickness=1.5))
    story.append(Spacer(1, 6))

    sent = report.get('sentiment', {})
    pos = sent.get('positive', 0)
    neu = sent.get('neutral', 0)
    neg = sent.get('negative', 0)
    total_sent = pos + neu + neg or 1

    sent_data = [[
        Paragraph(f'<font size="16" color="{GREEN.hexval()}"><b>{round(pos/total_sent*100)}%</b></font><br/>'
                  f'<font size="8" color="{MUTED.hexval()}">Positive ({pos:,})</font>',
                  ParagraphStyle('s', alignment=TA_CENTER, leading=14)),
        Paragraph(f'<font size="16" color="{AMBER.hexval()}"><b>{round(neu/total_sent*100)}%</b></font><br/>'
                  f'<font size="8" color="{MUTED.hexval()}">Neutral ({neu:,})</font>',
                  ParagraphStyle('s', alignment=TA_CENTER, leading=14)),
        Paragraph(f'<font size="16" color="{RED.hexval()}"><b>{round(neg/total_sent*100)}%</b></font><br/>'
                  f'<font size="8" color="{MUTED.hexval()}">Negative ({neg:,})</font>',
                  ParagraphStyle('s', alignment=TA_CENTER, leading=14)),
    ]]
    sent_table = Table(sent_data, colWidths=[2.1 * inch, 2.1 * inch, 2.1 * inch])
    sent_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, -1), CARD_BG),
        ('BOX', (0, 0), (-1, -1), 1, HexColor('#E2E8F0')),
        ('LINEBEFORE', (1, 0), (1, -1), 0.5, HexColor('#E2E8F0')),
        ('LINEBEFORE', (2, 0), (2, -1), 0.5, HexColor('#E2E8F0')),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
    ]))
    story.append(sent_table)

    story.append(PageBreak())

    # ════════════════════════════════════════
    # PAGE 3 — RECOMMENDATIONS
    # ════════════════════════════════════════
    story.append(Paragraph('Priority Recommendations', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=ORANGE, thickness=1.5))
    story.append(Spacer(1, 8))

    for idx, rec in enumerate(report.get('recommendations', []), 1):
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

    # Quick Wins
    story.append(Spacer(1, 10))
    story.append(Paragraph('Quick Wins (Do This Week)', styles['SectionHead']))
    story.append(HRFlowable(width='100%', color=ORANGE, thickness=1.5))
    story.append(Spacer(1, 6))
    for win in report.get('quick_wins', []):
        story.append(Paragraph(f'>> {win}', styles['Body10']))

    # CTA
    story.append(Spacer(1, 30))
    cta_data = [[Paragraph(
        '<b>Ready to fix your reputation?</b><br/>'
        '<font size="9" color="#64748B">Our team can implement review campaigns, response management, '
        'and reputation monitoring. Contact us to get started.</font><br/><br/>'
        '<font size="10" color="#EA580C"><b>salessignalai.com</b>  |  (959) AI-SALES  |  (959) 247-2537</font><br/>'
        '<font size="8" color="#64748B">support@salessignalai.com</font>',
        ParagraphStyle('cta', fontName='Helvetica', fontSize=11, textColor=SLATE, alignment=TA_CENTER, leading=16),
    )]]
    cta_table = Table(cta_data, colWidths=[6.2 * inch])
    cta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), CARD_BG),
        ('BOX', (0, 0), (-1, -1), 1.5, ORANGE),
        ('TOPPADDING', (0, 0), (-1, -1), 18),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 18),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
    ]))
    story.append(cta_table)

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buffer.seek(0)

    biz = re.sub(r'[^a-zA-Z0-9]+', '_', report.get('business_name', 'unknown')).strip('_')
    filename = f'Reputation_Report_{biz}_{datetime.now().strftime("%Y%m%d")}.pdf'

    return buffer.getvalue(), filename


# ---------------------------------------------------------------------------
# PDF download endpoint
# ---------------------------------------------------------------------------

@login_required
@require_POST
def agent_rep_pdf(request):
    """Generate and download a Reputation Intelligence PDF."""
    import logging
    logger = logging.getLogger(__name__)

    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Admin access required'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({'error': f'Invalid JSON: {str(e)}'}, status=400)

    report = data.get('report', {})
    if not report:
        return JsonResponse({'error': 'No report data provided'}, status=400)

    try:
        pdf_bytes, filename = _generate_rep_pdf(report)
    except ImportError:
        return JsonResponse({'error': 'reportlab not installed on server'}, status=500)
    except Exception as e:
        logger.exception('REP PDF generation failed')
        return JsonResponse({'error': f'PDF generation failed: {str(e)}'}, status=500)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Email report endpoint
# ---------------------------------------------------------------------------

@login_required
@require_POST
def agent_rep_email(request):
    """Generate PDF and email it with a branded HTML template."""
    import logging
    logger = logging.getLogger(__name__)

    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Admin access required'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({'error': f'Invalid JSON: {str(e)}'}, status=400)

    report = data.get('report', {})
    to_email = (data.get('to_email') or '').strip()
    recipient_name = (data.get('recipient_name') or '').strip()

    if not report:
        return JsonResponse({'error': 'No report data provided'}, status=400)
    if not to_email or '@' not in to_email:
        return JsonResponse({'error': 'Valid email address required'}, status=400)

    try:
        pdf_bytes, filename = _generate_rep_pdf(report)
    except ImportError:
        return JsonResponse({'error': 'reportlab not installed on server'}, status=500)
    except Exception as e:
        logger.exception('REP PDF generation failed for email')
        return JsonResponse({'error': f'PDF generation failed: {str(e)}'}, status=500)

    score = report.get('reputation_score', 0)
    grade = report.get('grade', '?')
    biz = report.get('business_name', 'your business')
    grade_color = '#059669' if score >= 70 else '#D97706' if score >= 50 else '#DC2626'

    rec_html = ''
    for rec in report.get('recommendations', [])[:3]:
        rec_html += f'<li style="margin-bottom:8px;color:#334155;font-size:14px;">{rec.get("title", "")}</li>'

    greeting = f'Hi {recipient_name},' if recipient_name else 'Hi there,'

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
<td><span style="font-size:18px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">SALESSIGNAL</span><span style="font-size:18px;font-weight:700;color:#EA580C;">AI</span></td>
<td align="right"><span style="font-size:11px;color:#94A3B8;letter-spacing:1px;">AGENT REP — REPUTATION INTELLIGENCE</span></td>
</tr>
</table>
</td></tr>

<!-- Orange accent -->
<tr><td style="background:linear-gradient(90deg,#EA580C,#DC2626);height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>

<!-- Score Banner -->
<tr><td style="padding:32px 32px 24px;text-align:center;">
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td align="center">
<div style="display:inline-block;background:{grade_color}10;border:3px solid {grade_color};border-radius:50%;width:90px;height:90px;line-height:90px;text-align:center;">
<span style="font-size:36px;font-weight:800;color:{grade_color};font-family:'Courier New',monospace;">{score}</span>
</div>
<div style="margin-top:8px;font-size:20px;font-weight:700;color:{grade_color};">Grade {grade}</div>
<div style="margin-top:4px;font-size:13px;color:#64748B;">{biz}</div>
</td>
</tr></table>
</td></tr>

<tr><td style="padding:0 32px;"><div style="border-top:1px solid #E2E8F0;"></div></td></tr>

<!-- Body -->
<tr><td style="padding:24px 32px;">
<p style="font-size:15px;color:#1E293B;line-height:1.6;margin:0 0 16px;">{greeting}</p>
<p style="font-size:14px;color:#334155;line-height:1.6;margin:0 0 16px;">
We ran a comprehensive reputation scan on <strong>{biz}</strong> across major review platforms. Your overall reputation score is <strong style="color:{grade_color};">{score}/100</strong>.
</p>
<p style="font-size:14px;color:#334155;line-height:1.6;margin:0 0 8px;"><strong>Top priorities:</strong></p>
<ul style="padding-left:20px;margin:0 0 20px;">{rec_html if rec_html else '<li style="color:#059669;font-size:14px;">Looking good! Your reputation is strong.</li>'}</ul>
<p style="font-size:14px;color:#334155;line-height:1.6;margin:0 0 24px;">
The full report is attached as a PDF with platform-by-platform analysis and recommendations.
</p>

<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<a href="https://salessignalai.com" style="display:inline-block;background:#EA580C;color:#FFFFFF;font-size:14px;font-weight:700;padding:14px 36px;border-radius:8px;text-decoration:none;letter-spacing:0.3px;">Learn How We Can Help</a>
</td></tr></table>
</td></tr>

<!-- Footer -->
<tr><td style="background:#F8FAFC;padding:20px 32px;border-top:1px solid #E2E8F0;">
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td>
<span style="font-size:12px;font-weight:700;color:#0F172A;">SALESSIGNAL</span><span style="font-size:12px;font-weight:700;color:#EA580C;">AI</span>
<div style="font-size:11px;color:#94A3B8;margin-top:4px;">Agent REP — Reputation Intelligence</div>
</td>
<td align="right" style="font-size:11px;color:#64748B;line-height:1.6;">
salessignalai.com<br/>
(959) 247-2537
</td>
</tr></table>
</td></tr>

</table>
</td></tr></table>
</body></html>"""

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

        message = Mail(
            from_email=Email(from_email, 'SalesSignalAI — Agent REP'),
            to_emails=To(to_email),
            subject=f'Reputation Report — {biz} ({score}/100)',
        )
        message.add_content(Content('text/html', html_body))

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
