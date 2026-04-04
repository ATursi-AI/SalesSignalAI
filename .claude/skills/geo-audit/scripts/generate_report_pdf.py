#!/usr/bin/env python3
"""
GEO/AEO Audit Report — PDF Generator (Branded)
Produces a beautiful, branded, client-ready PDF report from audit JSON results.

Usage:
    python generate_report_pdf.py audit_results.json [--output report.pdf] [--business "Business Name"]

Branding config is loaded from brand_config.json if present, or uses defaults.
"""

import json
import sys
import os
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white, black, Color
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether, Flowable
)
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing, Rect, String, Circle, Line, Group
from reportlab.graphics.charts.barcharts import VerticalBarChart


# ---------------------------------------------------------------------------
# Brand Config (loaded from brand_config.json or defaults)
# ---------------------------------------------------------------------------
DEFAULT_BRAND = {
    "company_name": "SalesSignal AI",
    "email": "hello@salessignalai.com",
    "phone": "(555) 123-4567",
    "website": "salessignalai.com",
    "tagline": "AI-Powered Sales Intelligence",
    "primary_color": "#1a56db",
    "dark_color": "#0f172a",
    "accent_color": "#f59e0b",
}


def load_brand_config(script_dir):
    """Load brand config from JSON file next to this script, or from project root."""
    paths_to_try = [
        os.path.join(script_dir, 'brand_config.json'),
        os.path.join(script_dir, '..', 'brand_config.json'),
        os.path.join(script_dir, '..', '..', '..', '..', 'brand_config.json'),
    ]
    for p in paths_to_try:
        if os.path.exists(p):
            with open(p) as f:
                cfg = json.load(f)
                merged = {**DEFAULT_BRAND, **cfg}
                return merged
    return DEFAULT_BRAND


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BRAND = load_brand_config(SCRIPT_DIR)


# ---------------------------------------------------------------------------
# Colors (derived from brand config)
# ---------------------------------------------------------------------------
BRAND_PRIMARY = HexColor(BRAND['primary_color'])
BRAND_DARK = HexColor(BRAND['dark_color'])
BRAND_ACCENT = HexColor(BRAND['accent_color'])
BRAND_LIGHT = HexColor('#f8fafc')
BRAND_LIGHT2 = HexColor('#f1f5f9')
BRAND_SUCCESS = HexColor('#059669')
BRAND_WARNING = HexColor('#d97706')
BRAND_DANGER = HexColor('#dc2626')
BRAND_MUTED = HexColor('#64748b')
BRAND_BORDER = HexColor('#e2e8f0')
WHITE = white
BLACK = black


# ---------------------------------------------------------------------------
# Custom Flowables
# ---------------------------------------------------------------------------
class BrandedHeader(Flowable):
    """Full-width branded header bar with company name and contact info."""

    def __init__(self, width=None):
        Flowable.__init__(self)
        self.width = width or (letter[0] - 1.5 * inch)
        self.height = 60

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return (self.width, self.height)

    def draw(self):
        c = self.canv
        # Dark background bar
        c.setFillColor(BRAND_DARK)
        c.roundRect(-10, 0, self.width + 20, self.height, 6, fill=1, stroke=0)

        # Company name (left)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 18)
        c.drawString(16, 22, BRAND['company_name'])

        # Contact info (right)
        c.setFont('Helvetica', 9)
        c.setFillColor(HexColor('#94a3b8'))
        contact_right = self.width - 16
        c.drawRightString(contact_right, 38, BRAND['email'])
        c.drawRightString(contact_right, 24, BRAND['phone'])
        c.drawRightString(contact_right, 10, BRAND['website'])


class ScoreGauge(Flowable):
    """Large circular score gauge with color ring."""

    def __init__(self, score, interpretation, size=180):
        Flowable.__init__(self)
        self.score = score
        self.interpretation = interpretation
        self.size = size
        self.width = size
        self.height = size

    def wrap(self, availWidth, availHeight):
        return (self.size, self.size)

    def draw(self):
        c = self.canv
        cx = self.size / 2
        cy = self.size / 2
        r_outer = self.size / 2 - 4
        r_inner = r_outer - 12

        color = _interp_color(self.interpretation)

        # Outer colored ring
        c.setStrokeColor(color)
        c.setLineWidth(10)
        c.circle(cx, cy, r_outer - 5, fill=0, stroke=1)

        # Inner white fill
        c.setFillColor(WHITE)
        c.setStrokeColor(BRAND_BORDER)
        c.setLineWidth(0.5)
        c.circle(cx, cy, r_inner, fill=1, stroke=1)

        # Score number
        c.setFillColor(BRAND_DARK)
        c.setFont('Helvetica-Bold', 40)
        c.drawCentredString(cx, cy + 4, str(int(self.score)))

        # "/100"
        c.setFillColor(BRAND_MUTED)
        c.setFont('Helvetica', 12)
        c.drawCentredString(cx, cy - 18, '/100')


class ColorBlock(Flowable):
    """A colored block with text inside — used for priority badges."""

    def __init__(self, text, bg_color, text_color=WHITE, width=60, height=20):
        Flowable.__init__(self)
        self.text = text
        self.bg_color = bg_color
        self.text_color = text_color
        self._width = width
        self._height = height

    def wrap(self, availWidth, availHeight):
        return (self._width, self._height)

    def draw(self):
        c = self.canv
        c.setFillColor(self.bg_color)
        c.roundRect(0, 0, self._width, self._height, 3, fill=1, stroke=0)
        c.setFillColor(self.text_color)
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(self._width / 2, 6, self.text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _score_color(score, max_score=10):
    pct = (score / max_score) * 100 if max_score > 0 else 0
    if pct >= 70:
        return BRAND_SUCCESS
    elif pct >= 40:
        return BRAND_WARNING
    else:
        return BRAND_DANGER


def _interp_color(interp):
    return {
        'AI-Optimized': BRAND_SUCCESS,
        'Partially Optimized': BRAND_WARNING,
        'Needs Work': BRAND_DANGER,
        'Not AI-Ready': BRAND_DANGER,
    }.get(interp, BRAND_MUTED)


# ---------------------------------------------------------------------------
# Score Bar Drawing
# ---------------------------------------------------------------------------
def make_score_bar(label, score, max_score=10, width=470, height=30):
    d = Drawing(width, height)

    bar_x = 190
    bar_width = width - 240
    bar_height = 12
    bar_y = (height - bar_height) / 2

    # Background
    d.add(Rect(bar_x, bar_y, bar_width, bar_height,
               fillColor=BRAND_LIGHT2, strokeColor=None, rx=6, ry=6))

    # Fill
    fill_w = (score / max_score) * bar_width if max_score > 0 else 0
    color = _score_color(score, max_score)
    if fill_w > 1:
        d.add(Rect(bar_x, bar_y, fill_w, bar_height,
                    fillColor=color, strokeColor=None, rx=6, ry=6))

    # Label
    d.add(String(0, bar_y + 1, label,
                 fontName='Helvetica', fontSize=10, fillColor=BRAND_DARK))

    # Score
    d.add(String(width - 30, bar_y + 1, f'{score:.1f}/10',
                 fontName='Helvetica-Bold', fontSize=10, fillColor=color))

    return d


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle('ReportTitle', parent=styles['Title'],
        fontSize=30, leading=36, textColor=BRAND_DARK, spaceAfter=4, alignment=TA_LEFT,
        fontName='Helvetica-Bold'))

    styles.add(ParagraphStyle('ReportSubtitle', parent=styles['Normal'],
        fontSize=13, leading=17, textColor=BRAND_MUTED, spaceAfter=16))

    styles.add(ParagraphStyle('SectionHead', parent=styles['Heading1'],
        fontSize=18, leading=24, textColor=BRAND_PRIMARY, spaceBefore=20, spaceAfter=8,
        fontName='Helvetica-Bold'))

    styles.add(ParagraphStyle('SubHead', parent=styles['Heading2'],
        fontSize=13, leading=17, textColor=BRAND_DARK, spaceBefore=14, spaceAfter=6,
        fontName='Helvetica-Bold'))

    styles.add(ParagraphStyle('Body', parent=styles['Normal'],
        fontSize=10, leading=15, textColor=BRAND_DARK, spaceAfter=6))

    styles.add(ParagraphStyle('BodyCenter', parent=styles['Normal'],
        fontSize=10, leading=15, textColor=BRAND_DARK, spaceAfter=6, alignment=TA_CENTER))

    styles.add(ParagraphStyle('SmallMuted', parent=styles['Normal'],
        fontSize=8, leading=10, textColor=BRAND_MUTED))

    styles.add(ParagraphStyle('BulletItem', parent=styles['Normal'],
        fontSize=10, leading=15, textColor=BRAND_DARK, leftIndent=20, spaceAfter=4,
        bulletIndent=8))

    styles.add(ParagraphStyle('InterpStyle', parent=styles['Normal'],
        fontSize=16, leading=20, alignment=TA_CENTER, fontName='Helvetica-Bold'))

    styles.add(ParagraphStyle('Footer', parent=styles['Normal'],
        fontSize=8, leading=10, textColor=BRAND_MUTED, alignment=TA_CENTER))

    styles.add(ParagraphStyle('CTAHead', parent=styles['Normal'],
        fontSize=16, textColor=BRAND_PRIMARY, alignment=TA_CENTER, fontName='Helvetica-Bold',
        spaceAfter=6))

    styles.add(ParagraphStyle('CTABody', parent=styles['Normal'],
        fontSize=11, alignment=TA_CENTER, textColor=BRAND_DARK, spaceAfter=4))

    styles.add(ParagraphStyle('CTAContact', parent=styles['Normal'],
        fontSize=12, alignment=TA_CENTER, textColor=BRAND_PRIMARY, fontName='Helvetica-Bold',
        spaceAfter=4))

    return styles


# ---------------------------------------------------------------------------
# Page Template (header + footer on every page)
# ---------------------------------------------------------------------------
def _header_footer(canvas_obj, doc):
    """Draw branded header bar and footer on every page."""
    canvas_obj.saveState()
    w, h = letter

    # Top accent line
    canvas_obj.setFillColor(BRAND_PRIMARY)
    canvas_obj.rect(0, h - 4, w, 4, fill=1, stroke=0)

    # Footer
    canvas_obj.setFont('Helvetica', 7)
    canvas_obj.setFillColor(BRAND_MUTED)
    canvas_obj.drawCentredString(w / 2, 20,
        f'{BRAND["company_name"]}  |  {BRAND["email"]}  |  {BRAND["phone"]}  |  {BRAND["website"]}')
    canvas_obj.drawRightString(w - 54, 20, f'Page {doc.page}')

    # Bottom accent line
    canvas_obj.setFillColor(BRAND_PRIMARY)
    canvas_obj.rect(0, 0, w, 3, fill=1, stroke=0)

    canvas_obj.restoreState()


# ---------------------------------------------------------------------------
# Build: Cover Page
# ---------------------------------------------------------------------------
def build_cover(story, styles, data, business_name):
    url = data.get('url', 'Unknown')
    audit_date = data.get('audit_date', datetime.now().isoformat())
    scores = data.get('scores', {})
    overall = scores.get('overall_geo_score', 0)
    interp = scores.get('interpretation', 'Unknown')

    # Branded header block
    story.append(BrandedHeader())
    story.append(Spacer(1, 30))

    # Title
    story.append(Paragraph('GEO/AEO Audit Report', styles['ReportTitle']))
    story.append(Paragraph(f'Prepared for <b>{business_name or url}</b>', styles['ReportSubtitle']))

    # Info cards row
    info_data = [[
        Paragraph('<font size="8" color="#64748b">WEBSITE</font><br/>'
                  f'<font size="10">{url}</font>', styles['Body']),
        Paragraph('<font size="8" color="#64748b">AUDIT DATE</font><br/>'
                  f'<font size="10">{audit_date[:10]}</font>', styles['Body']),
        Paragraph('<font size="8" color="#64748b">PREPARED BY</font><br/>'
                  f'<font size="10">{BRAND["company_name"]}</font>', styles['Body']),
    ]]
    info_table = Table(info_data, colWidths=[190, 140, 140])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_LIGHT),
        ('ROUNDEDCORNERS', (0, 0), (-1, -1), [6, 6, 6, 6]),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LINEAFTER', (0, 0), (1, 0), 0.5, BRAND_BORDER),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 24))

    # Score gauge centered
    gauge = ScoreGauge(overall, interp, size=170)
    gauge_table = Table([[gauge]], colWidths=[470])
    gauge_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(gauge_table)
    story.append(Spacer(1, 4))

    # Interpretation label
    icolor = _interp_color(interp)
    story.append(Paragraph(
        f'<font color="{icolor.hexval()}">{interp}</font>',
        styles['InterpStyle']
    ))
    story.append(Spacer(1, 20))

    # Category score bars
    categories = [
        ('AI Crawlability', 'ai_crawlability'),
        ('Schema & Structured Data', 'schema_structured_data'),
        ('Content Structure', 'content_structure'),
        ('Entity Clarity', 'entity_clarity'),
        ('Quotability & Citations', 'quotability_citations'),
        ('E-E-A-T Signals', 'eeat_signals'),
        ('Technical Foundation', 'technical_foundation'),
    ]
    for label, key in categories:
        story.append(make_score_bar(label, scores.get(key, 0)))
        story.append(Spacer(1, 2))


# ---------------------------------------------------------------------------
# Build: Detailed Findings
# ---------------------------------------------------------------------------
def build_findings(story, styles, data):
    checks = data.get('checks', {})
    scores = data.get('scores', {})

    story.append(PageBreak())
    story.append(Paragraph('Detailed Findings', styles['SectionHead']))
    story.append(HRFlowable(width='100%', thickness=1.5, color=BRAND_PRIMARY, spaceAfter=8))

    homepage = checks.get('homepage', {})
    robots = checks.get('robots_txt', {})
    llms = checks.get('llms_txt', {})
    sitemap = checks.get('sitemap', {})
    key_pages = checks.get('key_pages', {})
    ld_types = homepage.get('json_ld_types', [])

    sections = [
        ('1. AI Crawlability', 'ai_crawlability', [
            f'<b>robots.txt:</b> {"Found" if robots.get("exists") else "Missing"}'
            + (' &mdash; ' + ("Blocks all crawlers" if robots.get("disallow_all") else "Allows crawling") if robots.get('exists') else ''),
            _ai_bot_summary(robots),
            f'<b>llms.txt:</b> {"Found (" + str(llms.get("word_count", 0)) + " words)" if llms.get("exists") else "Not found &mdash; this is a quick win"}',
            f'<b>sitemap.xml:</b> {"Found (" + str(sitemap.get("url_count", 0)) + " URLs)" if sitemap.get("exists") else "Not found"}',
        ]),
        ('2. Schema & Structured Data', 'schema_structured_data', [
            f'<b>JSON-LD types found:</b> {", ".join(ld_types) if ld_types else "None &mdash; major gap for AI visibility"}',
            f'<b>Recommended missing:</b> {", ".join(t for t in ["Organization","LocalBusiness","FAQPage","BreadcrumbList"] if t not in ld_types) or "None &mdash; all key types present!"}',
        ]),
        ('3. Content Structure', 'content_structure', [
            f'<b>Headings:</b> H1: {homepage.get("heading_counts",{}).get("h1",0)}, H2: {homepage.get("heading_counts",{}).get("h2",0)}, H3: {homepage.get("heading_counts",{}).get("h3",0)}',
            f'<b>FAQ indicators:</b> {homepage.get("faq_indicators",0)} {"(Good)" if homepage.get("faq_indicators",0) >= 2 else "(Consider adding FAQ sections)"}',
            f'<b>Word count:</b> ~{homepage.get("word_count",0)} {"(Solid)" if homepage.get("word_count",0) > 500 else "(Thin &mdash; AI needs substance to cite)"}',
        ]),
        ('4. Entity Clarity', 'entity_clarity', [
            f'<b>Title tag:</b> {"Present" if homepage.get("title") else "Missing"}'
            + (f' &mdash; "{homepage.get("title","")[:55]}"' if homepage.get("title") else ''),
            f'<b>Meta description:</b> {"Present (" + str(len(homepage.get("meta_description",""))) + " chars)" if homepage.get("meta_description") else "Missing &mdash; critical for AI summaries"}',
            f'<b>About page:</b> {"Found" if key_pages.get("about") else "Missing"}',
            f'<b>Contact page:</b> {"Found" if key_pages.get("contact") else "Missing"}',
        ]),
        ('5. Quotability & Citations', 'quotability_citations', [
            f'<b>Blog/articles:</b> {"Found" if key_pages.get("blog") else "Missing &mdash; regular content builds AI citation authority"}',
            '<b>Note:</b> Full assessment requires manual content review for original data, statistics, and quotable statements.',
        ]),
        ('6. E-E-A-T Signals', 'eeat_signals', [
            f'<b>Author metadata:</b> {"Found" if homepage.get("has_author") else "Missing &mdash; add author tags for credibility"}',
            '<b>Manual review needed:</b> Testimonials, certifications, case studies, professional credentials.',
        ]),
        ('7. Technical Foundation', 'technical_foundation',
            _tech_findings(homepage)),
    ]

    for title, score_key, findings in sections:
        sv = scores.get(score_key, 0)
        sc = _score_color(sv)
        story.append(Paragraph(
            f'{title} &mdash; <font color="{sc.hexval()}">{sv:.1f}/10</font>',
            styles['SubHead']
        ))
        for f in findings:
            if f:
                story.append(Paragraph(f'&#8226; {f}', styles['BulletItem']))
        story.append(Spacer(1, 6))


def _ai_bot_summary(robots):
    ai_bots = robots.get('ai_bots', {})
    mentioned = [b for b, i in ai_bots.items() if i.get('mentioned')]
    blocked = [b for b, i in ai_bots.items() if i.get('allowed') is False]
    if blocked:
        return f'<b>AI bots BLOCKED:</b> <font color="#dc2626">{", ".join(blocked)}</font>'
    if mentioned:
        return f'<b>AI bots mentioned:</b> {", ".join(mentioned)}'
    return '<b>AI bots:</b> No AI-specific rules in robots.txt'


def _tech_findings(homepage):
    items = [
        ('HTTPS', homepage.get('is_https', False)),
        ('Mobile viewport', homepage.get('has_viewport', False)),
        ('Canonical tag', homepage.get('has_canonical', False)),
        ('Open Graph tags', bool(homepage.get('og_tags'))),
        ('Twitter Cards', bool(homepage.get('twitter_tags'))),
    ]
    results = []
    for label, ok in items:
        tag = '<font color="#059669"><b>PASS</b></font>' if ok else '<font color="#dc2626"><b>FAIL</b></font>'
        results.append(f'{label}: {tag}')
    ps = homepage.get('page_size_kb', 0)
    results.append(f'Page size: {ps} KB {"(Good)" if ps < 500 else "(Large)"}')
    return results


# ---------------------------------------------------------------------------
# Build: Recommendations
# ---------------------------------------------------------------------------
def build_recommendations(story, styles, data):
    checks = data.get('checks', {})
    scores = data.get('scores', {})
    homepage = checks.get('homepage', {})
    key_pages = checks.get('key_pages', {})
    ld_types = homepage.get('json_ld_types', [])

    story.append(PageBreak())
    story.append(Paragraph('Priority Recommendations', styles['SectionHead']))
    story.append(HRFlowable(width='100%', thickness=1.5, color=BRAND_PRIMARY, spaceAfter=10))

    recs = []

    if not checks.get('llms_txt', {}).get('exists'):
        recs.append(('HIGH', 'Create an llms.txt file',
            'Tells AI crawlers what your business does. Quick to implement, high signal value.'))

    if 'Organization' not in ld_types and 'LocalBusiness' not in ld_types:
        recs.append(('HIGH', 'Add Organization/LocalBusiness JSON-LD',
            'Structured data is how AI engines identify your business. Without it, you are invisible to AI-powered local search.'))

    if 'FAQPage' not in ld_types:
        recs.append(('MED', 'Add FAQPage schema markup',
            'Helps AI engines extract and present your answers directly in conversational results.'))

    if not homepage.get('meta_description'):
        recs.append(('HIGH', 'Add a meta description',
            'AI engines use meta descriptions as summary source material. Under 160 chars with key info.'))

    if not key_pages.get('blog'):
        recs.append(('MED', 'Start a blog or resources section',
            'Authoritative content builds citation-worthiness. Answer the questions your customers actually ask.'))

    if not key_pages.get('faq'):
        recs.append(('MED', 'Create a dedicated FAQ page',
            'Clear Q&A format. AI engines love extracting concise answers to common questions.'))

    blocked = [b for b, i in checks.get('robots_txt', {}).get('ai_bots', {}).items() if i.get('allowed') is False]
    if blocked:
        recs.append(('HIGH', f'Unblock AI crawlers: {", ".join(blocked)}',
            'Your robots.txt is actively blocking AI search engines from reading your site.'))

    if not recs:
        recs.append(('INFO', 'Looking good!', 'No critical issues. See detailed findings for fine-tuning.'))

    pcolors = {'HIGH': BRAND_DANGER, 'MED': BRAND_WARNING, 'LOW': BRAND_SUCCESS, 'INFO': BRAND_PRIMARY}

    for priority, title, desc in recs:
        pc = pcolors.get(priority, BRAND_MUTED)
        row = [[
            Paragraph(f'<font color="{pc.hexval()}"><b>[{priority}]</b></font>  <b>{title}</b>', styles['Body']),
        ], [
            Paragraph(desc, styles['Body']),
        ]]
        t = Table(row, colWidths=[470])
        t.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 14),
            ('RIGHTPADDING', (0, 0), (-1, -1), 14),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
            ('LINEBELOW', (0, -1), (-1, -1), 0.5, BRAND_BORDER),
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_LIGHT),
        ]))
        story.append(t)
        story.append(Spacer(1, 4))

    # Quick Wins
    story.append(Spacer(1, 14))
    story.append(Paragraph('Quick Wins (Do This Week)', styles['SectionHead']))
    story.append(HRFlowable(width='100%', thickness=1, color=BRAND_ACCENT, spaceAfter=8))

    wins = []
    if not checks.get('llms_txt', {}).get('exists'):
        wins.append('Create llms.txt &mdash; 15 minutes, immediate AI visibility boost')
    if not homepage.get('meta_description'):
        wins.append('Write a homepage meta description &mdash; 5 minutes')
    if 'Organization' not in ld_types and 'LocalBusiness' not in ld_types:
        wins.append('Add Organization JSON-LD to homepage &mdash; 30 minutes')
    if not homepage.get('og_tags'):
        wins.append('Add Open Graph meta tags &mdash; improves sharing and AI parsing')
    if homepage.get('heading_counts', {}).get('h1', 0) != 1:
        wins.append('Fix heading hierarchy &mdash; ensure exactly one H1 tag')
    if not wins:
        wins.append('Review detailed findings for optimization opportunities')

    for i, w in enumerate(wins[:5], 1):
        story.append(Paragraph(f'<b>{i}.</b> {w}', styles['BulletItem']))


# ---------------------------------------------------------------------------
# Build: CTA / Contact Page
# ---------------------------------------------------------------------------
def build_cta(story, styles):
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width='100%', thickness=2, color=BRAND_PRIMARY, spaceAfter=16))

    story.append(Paragraph('Ready to improve your AI search visibility?', styles['CTAHead']))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        'We can implement every recommendation in this report. '
        'Most businesses see measurable improvement in AI search citations within 30 days.',
        styles['CTABody']))
    story.append(Spacer(1, 14))

    # Contact card
    contact_data = [[
        Paragraph(f'<font size="12"><b>{BRAND["company_name"]}</b></font>', styles['BodyCenter']),
    ], [
        Paragraph(
            f'<font size="11" color="{BRAND_PRIMARY.hexval()}">{BRAND["email"]}</font>'
            f'  &nbsp;&nbsp;|&nbsp;&nbsp;  '
            f'<font size="11">{BRAND["phone"]}</font>',
            styles['BodyCenter']),
    ], [
        Paragraph(f'<font size="10" color="#64748b">{BRAND["website"]}</font>', styles['BodyCenter']),
    ]]
    ct = Table(contact_data, colWidths=[470])
    ct.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_LIGHT),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 12),
        ('ROUNDEDCORNERS', (0, 0), (-1, -1), [8, 8, 8, 8]),
    ]))
    story.append(ct)

    story.append(Spacer(1, 24))
    story.append(Paragraph(
        f'Report generated {datetime.now().strftime("%B %d, %Y")} by {BRAND["company_name"]}',
        styles['Footer']))


# ---------------------------------------------------------------------------
# Main PDF Builder
# ---------------------------------------------------------------------------
def generate_report(json_path, output_path=None, business_name=None):
    """Generate a branded PDF report from audit JSON results."""

    with open(json_path, 'r') as f:
        data = json.load(f)

    if not output_path:
        url = data.get('url', 'unknown')
        clean = url.replace('https://', '').replace('http://', '').replace('/', '_').replace('.', '_')
        output_path = f'geo_audit_report_{clean}.pdf'

    if not business_name:
        business_name = data.get('url', 'Unknown Website')

    styles = get_styles()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        title=f'GEO/AEO Audit Report - {business_name}',
        author=BRAND['company_name'],
    )

    story = []
    build_cover(story, styles, data, business_name)
    build_findings(story, styles, data)
    build_recommendations(story, styles, data)
    build_cta(story, styles)

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    print(f'[PDF Report] Generated: {output_path}')
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python generate_report_pdf.py <audit_results.json> [--output report.pdf] [--business "Name"]')
        sys.exit(1)

    json_file = sys.argv[1]
    out_file = None
    biz_name = None

    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            out_file = sys.argv[idx + 1]

    if '--business' in sys.argv:
        idx = sys.argv.index('--business')
        if idx + 1 < len(sys.argv):
            biz_name = sys.argv[idx + 1]

    result = generate_report(json_file, out_file, biz_name)
    print(f'Done! Report saved to: {result}')
