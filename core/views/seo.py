from django.http import HttpResponse
from django.utils import timezone

from core.models import ServiceLandingPage


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
Sitemap: https://salessignalai.com/sitemap.xml
"""
    return HttpResponse(content.strip(), content_type='text/plain')
