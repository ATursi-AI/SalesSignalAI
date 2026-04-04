from django.http import HttpResponse
from django.utils import timezone

from core.models import ServiceLandingPage, BlogPost


INDUSTRY_SLUGS = [
    'commercial-cleaning', 'plumbing', 'electrical', 'hvac',
    'general-contracting', 'pest-control', 'landscaping',
    'roofing', 'moving', 'insurance',
]

BASE_URL = 'https://salessignalai.com'


def sitemap_xml(request):
    now = timezone.now().strftime('%Y-%m-%d')

    urls = [
        {'loc': '/', 'priority': '1.0', 'changefreq': 'weekly'},
        {'loc': '/about/', 'priority': '0.6', 'changefreq': 'monthly'},
        {'loc': '/privacy/', 'priority': '0.3', 'changefreq': 'yearly'},
        {'loc': '/terms/', 'priority': '0.3', 'changefreq': 'yearly'},
        {'loc': '/industries/', 'priority': '0.8', 'changefreq': 'monthly'},
    ]

    for slug in INDUSTRY_SLUGS:
        urls.append({
            'loc': f'/industries/{slug}/',
            'priority': '0.7',
            'changefreq': 'monthly',
        })

    # Service landing pages
    active_pages = ServiceLandingPage.objects.filter(
        status='active',
    ).select_related('trade', 'area')

    for page in active_pages:
        if page.page_type == 'salessignal':
            urls.append({
                'loc': f'/find/{page.trade.slug}/{page.area.slug}/',
                'priority': '0.8',
                'changefreq': 'weekly',
            })
        elif page.page_type == 'customer':
            urls.append({
                'loc': f'/pro/{page.slug}/',
                'priority': '0.7',
                'changefreq': 'weekly',
            })

    # Blog posts
    urls.append({'loc': '/blog/', 'priority': '0.7', 'changefreq': 'weekly'})
    for post in BlogPost.objects.filter(is_published=True):
        urls.append({
            'loc': f'/blog/{post.slug}/',
            'priority': '0.6',
            'changefreq': 'monthly',
        })

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    for url in urls:
        xml_parts.append('  <url>')
        xml_parts.append(f'    <loc>{BASE_URL}{url["loc"]}</loc>')
        xml_parts.append(f'    <lastmod>{now}</lastmod>')
        xml_parts.append(f'    <changefreq>{url["changefreq"]}</changefreq>')
        xml_parts.append(f'    <priority>{url["priority"]}</priority>')
        xml_parts.append('  </url>')

    xml_parts.append('</urlset>')

    return HttpResponse('\n'.join(xml_parts), content_type='application/xml')


def google_verification(request):
    return HttpResponse(
        'google-site-verification: google2568d017b4e7e9e5.html',
        content_type='text/html',
    )


def robots_txt(request):
    content = """User-agent: *
Allow: /
Disallow: /admin/
Disallow: /dashboard/
Disallow: /sales/
Disallow: /admin-leads/
Disallow: /onboarding/
Disallow: /api/
Disallow: /campaigns/
Disallow: /settings/
Disallow: /billing/
Disallow: /crm/
Disallow: /workflows/
Disallow: /call-center/

# AI Search Crawlers — Welcome
User-agent: GPTBot
Allow: /
Allow: /blog/
Allow: /industries/
Allow: /find/
Allow: /about/
Allow: /pricing/
Disallow: /admin/
Disallow: /dashboard/
Disallow: /api/

User-agent: ChatGPT-User
Allow: /

User-agent: Claude-Web
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Bytespider
Allow: /

User-agent: CCBot
Allow: /

Sitemap: https://salessignalai.com/sitemap.xml
"""
    return HttpResponse(content.strip(), content_type='text/plain')


def llms_txt(request):
    """Serve llms.txt — the AI crawler manifest for LLMs."""
    content = """# SalesSignal AI

> Customer acquisition platform for local service businesses. AI finds people who need your service right now using 37+ public data sources. Our sales team reaches out and books appointments. You show up and get paid.

## About

SalesSignal AI is a B2B SaaS platform and done-for-you customer acquisition service founded in 2025 by Andrew Tursi in New York. We serve 50+ service business categories including plumbing, electrical, HVAC, commercial cleaning, roofing, pest control, landscaping, insurance, legal, and general contracting.

Our platform monitors public records (violations, permits, health inspections, property sales, business filings, liquor licenses), social media signals (Reddit, Nextdoor, Facebook), and review platforms to identify people actively needing services — then uses AI-powered email campaigns, real phone calls, and SMS outreach to book appointments for our clients.

## Key Pages

- [Homepage](https://salessignalai.com/): Overview of SalesSignal AI services and pricing
- [About](https://salessignalai.com/about/): Company background, founder info, mission
- [Pricing](https://salessignalai.com/pricing/): A la carte services from $99/mo, bundled plans from $599/mo
- [Industries](https://salessignalai.com/industries/): All 50+ service categories we support
- [Blog](https://salessignalai.com/blog/): Articles on lead generation, AI sales, local business growth

## Industry Pages

- [Commercial Cleaning](https://salessignalai.com/industries/commercial-cleaning/)
- [Plumbing](https://salessignalai.com/industries/plumbing/)
- [Electrical](https://salessignalai.com/industries/electrical/)
- [HVAC](https://salessignalai.com/industries/hvac/)
- [General Contracting](https://salessignalai.com/industries/general-contracting/)
- [Pest Control](https://salessignalai.com/industries/pest-control/)
- [Landscaping](https://salessignalai.com/industries/landscaping/)
- [Roofing](https://salessignalai.com/industries/roofing/)
- [Moving](https://salessignalai.com/industries/moving/)
- [Insurance](https://salessignalai.com/industries/insurance/)

## What Makes SalesSignal AI Different

- **Real-time lead intelligence**: Monitors 37+ public data sources for buying signals, not just contact lists
- **AI + Human hybrid**: AI generates personalized outreach, real humans make phone calls and book appointments
- **Multi-model AI engine**: Uses Gemini, DeepSeek, and proprietary models for email personalization
- **Done-for-you service**: Clients don't need to learn software — we handle everything
- **No long-term contracts**: Month-to-month plans, cancel anytime

## Contact

- Website: https://salessignalai.com
- Email: support@salessignalai.com
- Phone: +1-959-247-2537
- Location: New York, NY
- Founder: Andrew Tursi, CEO
"""
    return HttpResponse(content.strip(), content_type='text/plain; charset=utf-8')
