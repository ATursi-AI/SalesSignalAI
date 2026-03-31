import hashlib
import json

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Q, F, Count, Sum

from core.models import (
    TradeCategory, ServiceArea, ServiceLandingPage, ServicePageSubmission,
    Lead, BusinessProfile,
)


# ─── Live Stats Helper ────────────────────────────────────────────

def _get_area_trade_stats(trade, area, days=7):
    """Get live stats for a trade+area combination from Lead model."""
    since = timezone.now() - timezone.timedelta(days=days)
    trade_lower = trade.name.lower()

    # Keywords to match this trade in lead content
    keywords = [kw.strip().lower() for kw in trade.service_keywords.split(',') if kw.strip()][:5]
    keywords.append(trade_lower)

    base_qs = Lead.objects.filter(discovered_at__gte=since)

    # Area filter
    area_q = Q(region__icontains=area.name) | Q(detected_location__icontains=area.name)
    if area.county:
        area_q |= Q(region__icontains=area.county)

    area_leads = base_qs.filter(area_q)

    # Build keyword Q for this trade
    kw_q = Q()
    for kw in keywords:
        kw_q |= Q(source_content__icontains=kw)

    stats = {
        'service_requests': area_leads.filter(
            kw_q,
            source_group='social_media',
        ).count(),
        'permits_filed': area_leads.filter(
            source_type__in=['permits', 'permits_now'],
        ).count(),
        'violations_issued': area_leads.filter(
            source_type='violations',
        ).count(),
        'properties_sold': area_leads.filter(
            source_type='property_sales',
        ).count(),
        'new_businesses': area_leads.filter(
            source_type='business_filings',
        ).count(),
        'health_inspections': area_leads.filter(
            source_type='health_inspections',
        ).count(),
    }

    # Only include stats with non-zero values
    return {k: v for k, v in stats.items() if v > 0}


# ─── Trade-specific data ─────────────────────────────────────────

TRADE_SERVICES = {
    # Keys match DB trade slugs (not "emergency-plumber" — DB has "plumber")
    'plumber': {
        'display': 'Plumber',
        'brand_name': '911 Plumbing Pros',
        'brand_tagline': 'Fast. Licensed. Guaranteed.',
        'emergency_label': 'EMERGENCY PLUMBER',
        'response_time': '30-60 minutes',
        'hero_image': 'https://images.unsplash.com/photo-1585704032915-c3400ca199e7?w=1400&h=500&fit=crop',
        'banner_image': 'https://images.unsplash.com/photo-1585704032915-c3400ca199e7?w=1200&h=400&fit=crop&crop=center',
        'items': [
            {'icon': 'bi-droplet-fill', 'name': 'Burst Pipes', 'desc': 'Fast repair to stop flooding and prevent structural water damage to your home'},
            {'icon': 'bi-fire', 'name': 'Water Heater', 'desc': 'Same-day repair or replacement — tank and tankless — so you have hot water tonight'},
            {'icon': 'bi-arrow-down-circle', 'name': 'Drain Cleaning', 'desc': 'Professional hydro-jetting and snaking to clear the toughest kitchen, bath, and sewer clogs'},
            {'icon': 'bi-water', 'name': 'Leak Detection', 'desc': 'Pinpoint hidden slab and wall leaks with electronic detection before they cause mold'},
            {'icon': 'bi-wrench-adjustable', 'name': 'Sewer Line Repair', 'desc': 'Camera inspection, trenchless repair, and full replacement — minimal disruption to your yard'},
            {'icon': 'bi-wrench', 'name': 'Toilet Repair', 'desc': 'Running, leaking, clogged, or cracked — we fix or replace any toilet, any brand'},
            {'icon': 'bi-thermometer-snow', 'name': 'Frozen Pipes', 'desc': 'Safe thawing and insulation to prevent re-freeze. Available 24/7 during cold snaps'},
            {'icon': 'bi-tsunami', 'name': 'Sump Pump', 'desc': 'Emergency pump-outs, new installations, and battery backup systems to keep your basement dry'},
            {'icon': 'bi-fuel-pump', 'name': 'Gas Line', 'desc': 'If you smell gas, call immediately. Licensed gas line detection, repair, and new installations'},
            {'icon': 'bi-moisture', 'name': 'Water Damage Mitigation', 'desc': 'Emergency water extraction and dry-out to minimize damage while we fix the source'},
        ],
        'testimonials': [
            {'text': 'Pipe burst at 2 AM on a Sunday. They answered on the first ring, had a plumber at my door in 35 minutes, and saved my finished basement from flooding. Worth every penny.', 'author': 'Sarah M.', 'detail': 'Burst pipe, basement flooding'},
            {'text': 'Three plumbers told me I needed to rip up my driveway for a sewer repair. These guys did it trenchless in half a day. My yard still looks perfect.', 'author': 'Mike R.', 'detail': 'Trenchless sewer repair'},
            {'text': "I've used them for two emergencies and a bathroom remodel. They show up when they say, charge what they quote, and clean up after themselves. That's rare.", 'author': 'Lisa K.', 'detail': 'Repeat customer'},
        ],
        'faqs': [
            {'q': 'How fast can a plumber get to my home?', 'a': 'For emergencies, we dispatch licensed plumbers 24/7. In most cases, someone is at your door within 30-60 minutes. Non-emergency calls are typically same-day or next-day.'},
            {'q': 'Do you charge extra for nights, weekends, or holidays?', 'a': 'Dispatch and diagnosis are always free. Your plumber provides a clear, upfront price before any work begins — no surprise charges after the fact, regardless of when you call.'},
            {'q': 'Are your plumbers licensed and insured?', 'a': 'Yes. Every plumber in our network holds a valid state license, carries full liability insurance, and has passed a background check. We verify credentials before they join our network.'},
            {'q': "What if my issue isn't an emergency?", 'a': "We handle everything from emergency burst pipes to scheduled bathroom remodels. Call us and we'll book at your convenience — same-day and next-day slots are usually available."},
            {'q': 'Do you guarantee your work?', 'a': 'Yes. All work is backed by a satisfaction guarantee. If something we repaired fails within the warranty period, we come back and fix it at no additional charge.'},
            {'q': 'How much does a plumber cost?', 'a': 'It depends on the job. Simple repairs like a toilet fix typically start around $150-250. Larger jobs like water heater replacement or sewer repair vary. We always provide a free estimate before starting work.'},
        ],
    },
    'electrician': {
        'display': 'Electrician',
        'brand_name': '911 Electric Pros',
        'brand_tagline': 'Fast. Licensed. Guaranteed.',
        'emergency_label': 'EMERGENCY ELECTRICIAN',
        'response_time': '30-60 minutes',
        'hero_image': 'https://images.unsplash.com/photo-1621905251189-08b45d6a269e?w=1400&h=500&fit=crop',
        'banner_image': 'https://images.unsplash.com/photo-1621905251189-08b45d6a269e?w=1200&h=400&fit=crop&crop=center',
        'items': [
            {'icon': 'bi-lightning-fill', 'name': 'Power Outage', 'desc': 'Restore electricity to your home fast — we diagnose panel, wiring, and utility-side issues'},
            {'icon': 'bi-cpu', 'name': 'Panel Upgrade', 'desc': 'Upgrade from 100A to 200A+ for modern appliances, EV chargers, and home additions'},
            {'icon': 'bi-plug-fill', 'name': 'Wiring Repair', 'desc': 'Fix faulty, damaged, or aluminum wiring that causes flickering lights, tripped breakers, or fire risk'},
            {'icon': 'bi-outlet', 'name': 'Outlets & Switches', 'desc': 'Install GFCI outlets, USB outlets, smart switches, and repair dead or sparking receptacles'},
            {'icon': 'bi-shield-exclamation', 'name': 'Circuit Breaker', 'desc': 'Diagnose and fix constantly tripping breakers, short circuits, and overloaded panels'},
            {'icon': 'bi-lightbulb-fill', 'name': 'Lighting', 'desc': 'Recessed, under-cabinet, landscape, and security lighting — design through installation'},
            {'icon': 'bi-battery-charging', 'name': 'Generator Install', 'desc': 'Whole-home standby generators with automatic transfer switches — never lose power again'},
            {'icon': 'bi-bell-fill', 'name': 'Smoke & CO Detectors', 'desc': 'Hardwired, interconnected detectors that meet current NYC code — protect your family'},
            {'icon': 'bi-ev-station', 'name': 'EV Charger Install', 'desc': 'Level 2 home charging stations — proper circuit sizing, permitting, and clean installation'},
            {'icon': 'bi-exclamation-triangle-fill', 'name': 'Emergency Rewiring', 'desc': 'Burned wires, electrical fires, storm damage — we make your home safe immediately'},
        ],
        'testimonials': [
            {'text': 'Woke up to half my house with no power and a burning smell from the panel. They were here in 40 minutes, found a melted breaker, and had us back up and safe by noon.', 'author': 'James T.', 'detail': 'Panel emergency, burning smell'},
            {'text': 'Needed a full panel upgrade for our home addition. Pulled the permit, passed inspection first try, and the work was immaculate. Highly recommend.', 'author': 'Karen P.', 'detail': '200A panel upgrade'},
            {'text': 'We had flickering lights in three rooms for months. Other electricians couldn\'t find the problem. These guys traced it to a bad splice in 20 minutes. Fixed for good.', 'author': 'David L.', 'detail': 'Intermittent wiring issue'},
        ],
        'faqs': [
            {'q': 'How fast can an electrician get to my home?', 'a': 'For emergencies like sparking, burning smells, or total power loss, we prioritize dispatch 24/7. Most emergency calls are on-site within 30-60 minutes.'},
            {'q': 'Is it safe to wait if I see sparks or smell burning?', 'a': 'No. If you smell burning, see sparks, or have exposed wires, turn off the main breaker if you can safely reach it and call us immediately. Electrical fires can start in walls without warning.'},
            {'q': 'Are your electricians licensed and insured?', 'a': 'Every electrician in our network holds a valid state license, carries full liability insurance, and is bonded. We handle all required permits and inspections for code compliance.'},
            {'q': 'Do you handle commercial electrical work?', 'a': 'Yes. We service both residential and commercial properties — from single-family homes to office buildings, retail spaces, and restaurants.'},
            {'q': 'Do you guarantee your work?', 'a': 'Absolutely. All electrical work is backed by a workmanship warranty. If something we installed or repaired fails within the warranty period, we fix it at no additional cost.'},
            {'q': 'How much does an electrician cost?', 'a': 'Simple jobs like outlet replacement start around $150-200. Panel upgrades typically range $1,500-3,500 depending on amperage. We always provide a free estimate before starting.'},
        ],
    },
    # Aliases for legacy "emergency-" prefix slugs
    'emergency-plumber': None,  # filled below
    'emergency-electrician': None,  # filled below
}
# Wire aliases so old slugs still work
TRADE_SERVICES['emergency-plumber'] = TRADE_SERVICES['plumber']
TRADE_SERVICES['emergency-electrician'] = TRADE_SERVICES['electrician']

AREA_NEIGHBORHOODS = {
    'queens': ['Astoria', 'Long Island City', 'Flushing', 'Jamaica', 'Bayside', 'Forest Hills', 'Rego Park', 'Woodside', 'Jackson Heights', 'Elmhurst'],
    'brooklyn': ['Williamsburg', 'Park Slope', 'Bay Ridge', 'Flatbush', 'Bushwick', 'DUMBO', 'Bed-Stuy', 'Crown Heights', 'Greenpoint', 'Sunset Park'],
    'manhattan': ['Upper East Side', 'Upper West Side', 'Midtown', 'Harlem', 'East Village', 'West Village', 'SoHo', 'Tribeca', 'Chelsea', 'Murray Hill'],
    'bronx': ['Riverdale', 'Fordham', 'Pelham Bay', 'Morris Park', 'Kingsbridge', 'Mott Haven', 'Hunts Point', 'Tremont', 'Soundview', 'Eastchester'],
    'staten-island': ['St. George', 'Tottenville', 'Great Kills', 'New Dorp', 'Stapleton', 'Port Richmond', 'Eltingville', 'Annadale', 'Huguenot', 'Midland Beach'],
    'nassau-county': ['Lynbrook', 'Rockville Centre', 'Garden City', 'Mineola', 'Hempstead', 'Freeport', 'Valley Stream', 'Merrick', 'Massapequa', 'Hicksville'],
    'suffolk-county': ['Babylon', 'Huntington', 'Islip', 'Smithtown', 'Brookhaven', 'Patchogue', 'Bay Shore', 'Commack', 'Dix Hills', 'Hauppauge'],
}


# ─── Public Landing Page ──────────────────────────────────────────

def service_landing_page(request, trade_slug, area_slug):
    """Public-facing service landing page (SalesSignal-owned)."""
    page = get_object_or_404(
        ServiceLandingPage,
        slug=f"{trade_slug}-{area_slug}",
        status='active',
        page_type='salessignal',
    )

    ServiceLandingPage.objects.filter(pk=page.pk).update(page_views=F('page_views') + 1)

    stats = _get_area_trade_stats(page.trade, page.area) if page.show_live_stats else {}
    services_text = [s.strip() for s in page.services_offered.split('\n') if s.strip()] if page.services_offered else []

    # Trade-specific structured data
    trade_data = TRADE_SERVICES.get(trade_slug, {}) or {}
    trade_services = trade_data.get('items', [])
    trade_faqs = trade_data.get('faqs', page.faq_section or [])
    trade_testimonials = trade_data.get('testimonials', [])
    response_time = trade_data.get('response_time', '30-60 minutes')
    emergency_label = trade_data.get('emergency_label', f'EMERGENCY {page.trade.name.upper()}')
    hero_image = trade_data.get('hero_image', '')
    banner_image = trade_data.get('banner_image', '')
    neighborhoods = AREA_NEIGHBORHOODS.get(area_slug, [])
    # Strip state suffix for neighborhood lookup (e.g. "queens-ny" -> "queens")
    if not neighborhoods and '-' in area_slug:
        neighborhoods = AREA_NEIGHBORHOODS.get(area_slug.rsplit('-', 1)[0], [])

    # Internal linking
    neighboring = page.area.neighboring_areas.filter(is_active=True)[:6]
    same_area_pages = ServiceLandingPage.objects.filter(
        area=page.area, status='active', page_type='salessignal',
    ).exclude(pk=page.pk).select_related('trade')[:8]
    same_trade_pages = ServiceLandingPage.objects.filter(
        trade=page.trade, status='active', page_type='salessignal',
    ).exclude(pk=page.pk).select_related('area')[:8]

    phone = page.signalwire_phone or page.get_phone_display() or '(959) 247-2537'
    phone_raw = phone.replace('(', '').replace(')', '').replace(' ', '').replace('-', '')
    if not phone_raw.startswith('+'):
        phone_raw = '+1' + phone_raw

    # Trade brand for SalesSignal-owned pages (e.g. "911 Plumbing Pros")
    brand_name = trade_data.get('brand_name', f'SalesSignal {page.trade.name}')
    brand_tagline = trade_data.get('brand_tagline', 'Fast. Licensed. Guaranteed.')

    context = {
        'page': page,
        'stats': stats,
        'services': services_text,
        'trade_services': trade_services,
        'trade_faqs': trade_faqs,
        'trade_testimonials': trade_testimonials,
        'response_time': response_time,
        'emergency_label': emergency_label,
        'hero_image': hero_image,
        'banner_image': banner_image,
        'neighborhoods': neighborhoods,
        'neighboring': neighboring,
        'same_area_pages': same_area_pages,
        'same_trade_pages': same_trade_pages,
        'is_branded': False,
        'phone': phone,
        'phone_raw': phone_raw,
        'trade_name': page.trade.name,
        'area_name': page.area.name,
        'state': page.area.state,
        'state_full': page.area.state_full,
        'brand_name': brand_name,
        'brand_tagline': brand_tagline,
    }
    return render(request, 'service_pages/landing.html', context)


def service_landing_page_branded(request, customer_slug, area_slug):
    """Public-facing customer-branded landing page."""
    page = get_object_or_404(
        ServiceLandingPage,
        slug=f"{customer_slug}-{area_slug}",
        status='active',
        page_type='customer',
    )

    ServiceLandingPage.objects.filter(pk=page.pk).update(page_views=F('page_views') + 1)

    stats = _get_area_trade_stats(page.trade, page.area) if page.show_live_stats else {}
    services_text = [s.strip() for s in page.services_offered.split('\n') if s.strip()] if page.services_offered else []

    trade_slug = page.trade.slug
    trade_data = TRADE_SERVICES.get(trade_slug, {})
    neighborhoods = AREA_NEIGHBORHOODS.get(page.area.slug, [])

    phone = page.branded_phone or page.signalwire_phone or '(959) 247-2537'
    phone_raw = phone.replace('(', '').replace(')', '').replace(' ', '').replace('-', '')
    if not phone_raw.startswith('+'):
        phone_raw = '+1' + phone_raw

    context = {
        'page': page,
        'stats': stats,
        'services': services_text,
        'trade_services': trade_data.get('items', []),
        'trade_faqs': trade_data.get('faqs', page.faq_section or []),
        'neighborhoods': neighborhoods,
        'neighboring': [],
        'same_area_pages': [],
        'same_trade_pages': [],
        'is_branded': True,
        'phone': phone,
        'phone_raw': phone_raw,
        'trade_name': page.trade.name,
        'area_name': page.area.name,
        'state': page.area.state,
        'state_full': page.area.state_full,
    }
    return render(request, 'service_pages/landing.html', context)


# ─── Form Submission ──────────────────────────────────────────────

@csrf_exempt
@require_POST
def service_page_submit(request):
    """Handle service request form submission."""
    try:
        data = json.loads(request.body)
        page_id = data.get('page_id')
        name = data.get('name', '').strip()
        phone = data.get('phone', '').strip()
        email = data.get('email', '').strip()
        address = data.get('address', '').strip()
        problem = data.get('problem', '').strip()
        urgency = data.get('urgency', 'today').strip()

        if not page_id or not name or not phone:
            return JsonResponse({'error': 'Name and phone are required.'}, status=400)

        page = ServiceLandingPage.objects.filter(pk=page_id, status='active').first()
        if not page:
            return JsonResponse({'error': 'Page not found'}, status=404)

        # Create submission
        submission = ServicePageSubmission.objects.create(
            landing_page=page,
            name=name,
            phone=phone,
            email=email,
            address=address,
            problem_description=problem,
            urgency=urgency,
        )

        # Increment form submission count
        ServiceLandingPage.objects.filter(pk=page.pk).update(form_submissions=F('form_submissions') + 1)

        # Create a Lead record
        content = (
            f"Service request from {page.h1_headline or page.trade.name}:\n"
            f"{name} ({phone}) - {urgency}\n{problem}"
        )
        content_hash = hashlib.sha256(
            f"service_page|{page.pk}|{phone}|{problem[:50]}".encode()
        ).hexdigest()

        if not Lead.objects.filter(content_hash=content_hash).exists():
            Lead.objects.create(
                platform='manual',
                source_url=f'/find/{page.trade.slug}/{page.area.slug}-{page.area.state.lower()}/',
                source_content=content,
                source_author=name,
                detected_location=f"{page.area.name}, {page.area.state}",
                urgency_level='hot' if urgency == 'emergency' else 'warm',
                urgency_score=95 if urgency == 'emergency' else 70,
                confidence='high',
                content_hash=content_hash,
                state=page.area.state,
                region=page.area.name,
                source_group='social_media',
                source_type='prospect_video',  # reuse existing choice
                contact_name=name,
                contact_phone=phone,
                contact_email=email,
                contact_address=address,
                contact_business='',
                raw_data={
                    'source': 'service_landing_page',
                    'page_id': page.pk,
                    'trade': page.trade.name,
                    'area': page.area.name,
                    'urgency': urgency,
                    'problem': problem,
                },
            )

        # Route to customer if customer-branded
        if page.page_type == 'customer' and page.customer:
            submission.routed_to = page.customer
            submission.routed_at = timezone.now()
            submission.save(update_fields=['routed_to', 'routed_at'])

        # Email notification
        try:
            subject = f"New Service Request: {page.trade.name} in {page.area.name}"
            body = (
                f"New service request from {page.h1_headline}:\n\n"
                f"Name: {name}\nPhone: {phone}\nEmail: {email}\n"
                f"Address: {address}\nUrgency: {urgency}\n"
                f"Problem: {problem}\n\n"
                f"Page: {page}\nSubmission ID: {submission.pk}"
            )
            send_mail(
                subject, body,
                getattr(settings, 'DEFAULT_FROM_EMAIL', 'alerts@salessignal.ai'),
                [getattr(settings, 'ALERT_FROM_EMAIL', 'alerts@salessignal.ai')],
                fail_silently=True,
            )
        except Exception:
            pass

        return JsonResponse({'ok': True})

    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)


# ─── Admin: List ──────────────────────────────────────────────────

@staff_member_required
def service_page_list(request):
    """Admin list of all service landing pages."""
    pages = ServiceLandingPage.objects.select_related('trade', 'area', 'customer').all()

    status = request.GET.get('status')
    trade = request.GET.get('trade')
    area_type = request.GET.get('area_type')
    page_type = request.GET.get('page_type')
    q = request.GET.get('q', '').strip()

    if status:
        pages = pages.filter(status=status)
    if trade:
        pages = pages.filter(trade__slug=trade)
    if area_type:
        pages = pages.filter(area__area_type=area_type)
    if page_type:
        pages = pages.filter(page_type=page_type)
    if q:
        pages = pages.filter(
            Q(trade__name__icontains=q) | Q(area__name__icontains=q) |
            Q(branded_business_name__icontains=q) | Q(slug__icontains=q)
        )

    trades = TradeCategory.objects.filter(is_active=True).order_by('name')

    context = {
        'pages': pages,
        'trades': trades,
        'total_active': pages.filter(status='active').count(),
        'total_draft': pages.filter(status='draft').count(),
        'total_submissions': ServicePageSubmission.objects.count(),
    }
    return render(request, 'service_pages/admin_list.html', context)


# ─── Admin: Create / Edit ────────────────────────────────────────

@staff_member_required
def service_page_create(request):
    """Create a new service landing page."""
    if request.method == 'POST':
        page = _save_page(request, None)
        if page:
            return redirect('service_page_edit', page_id=page.pk)

    trades = TradeCategory.objects.filter(is_active=True)
    areas = ServiceArea.objects.filter(is_active=True)
    customers = BusinessProfile.objects.filter(is_active=True).order_by('business_name')

    context = {
        'page': None,
        'trades': trades,
        'areas': areas,
        'customers': customers,
        'editing': False,
    }
    return render(request, 'service_pages/admin_form.html', context)


@staff_member_required
def service_page_edit(request, page_id):
    """Edit a service landing page."""
    page = get_object_or_404(ServiceLandingPage, pk=page_id)

    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        if action == 'activate':
            page.status = 'active'
            page.save(update_fields=['status'])
        elif action == 'pause':
            page.status = 'paused'
            page.save(update_fields=['status'])
        elif action == 'delete':
            page.delete()
            return redirect('service_page_list')
        else:
            page = _save_page(request, page)
        return redirect('service_page_edit', page_id=page.pk)

    trades = TradeCategory.objects.filter(is_active=True)
    areas = ServiceArea.objects.filter(is_active=True)
    customers = BusinessProfile.objects.filter(is_active=True).order_by('business_name')

    context = {
        'page': page,
        'trades': trades,
        'areas': areas,
        'customers': customers,
        'editing': True,
    }
    return render(request, 'service_pages/admin_form.html', context)


def _save_page(request, page):
    p = request.POST
    trade_id = p.get('trade_id')
    area_id = p.get('area_id')

    if not trade_id or not area_id:
        return page

    trade = TradeCategory.objects.filter(pk=trade_id).first()
    area = ServiceArea.objects.filter(pk=area_id).first()
    if not trade or not area:
        return page

    if not page:
        page = ServiceLandingPage()

    page.trade = trade
    page.area = area
    page.page_type = p.get('page_type', 'salessignal')

    slug = p.get('slug', '').strip()
    if slug:
        page.slug = slug

    page.page_title = p.get('page_title', '').strip()
    page.meta_description = p.get('meta_description', '').strip()
    page.h1_headline = p.get('h1_headline', '').strip()
    page.hero_subheadline = p.get('hero_subheadline', '').strip()
    page.about_section = p.get('about_section', '').strip()
    page.services_offered = p.get('services_offered', '').strip()
    page.show_live_stats = p.get('show_live_stats') == 'on'

    # Customer branding
    customer_id = p.get('customer_id')
    if customer_id:
        page.customer = BusinessProfile.objects.filter(pk=customer_id).first()
    page.branded_business_name = p.get('branded_business_name', '').strip()
    page.branded_phone = p.get('branded_phone', '').strip()
    page.branded_email = p.get('branded_email', '').strip()
    page.branded_website = p.get('branded_website', '').strip()
    page.branded_tagline = p.get('branded_tagline', '').strip()
    page.branded_license_number = p.get('branded_license_number', '').strip()

    years = p.get('branded_years_in_business', '').strip()
    page.branded_years_in_business = int(years) if years.isdigit() else None

    # Phone routing
    page.signalwire_phone = p.get('signalwire_phone', '').strip()
    page.forward_to_phone = p.get('forward_to_phone', '').strip()

    page.save()
    return page


# ─── Admin: Bulk Create ──────────────────────────────────────────

@staff_member_required
def service_page_bulk_create(request):
    """Bulk create landing pages for multiple trade+area combinations."""
    if request.method == 'POST':
        trade_ids = request.POST.getlist('trade_ids')
        area_ids = request.POST.getlist('area_ids')
        page_type = request.POST.get('page_type', 'salessignal')
        customer_id = request.POST.get('customer_id')

        customer = None
        if customer_id:
            customer = BusinessProfile.objects.filter(pk=customer_id).first()

        trades = TradeCategory.objects.filter(pk__in=trade_ids)
        areas = ServiceArea.objects.filter(pk__in=area_ids)

        created = 0
        skipped = 0

        for trade in trades:
            for area in areas:
                slug = slugify(f"{trade.name}-{area.name}-{area.state}")
                if ServiceLandingPage.objects.filter(slug=slug).exists():
                    skipped += 1
                    continue

                ServiceLandingPage.objects.create(
                    trade=trade,
                    area=area,
                    page_type=page_type,
                    slug=slug,
                    customer=customer,
                    branded_business_name=customer.business_name if customer else '',
                    status='draft',
                )
                created += 1

        return JsonResponse({
            'ok': True,
            'created': created,
            'skipped': skipped,
            'total': len(trade_ids) * len(area_ids),
        })

    trades = TradeCategory.objects.filter(is_active=True).order_by('category_type', 'name')
    areas = ServiceArea.objects.filter(is_active=True).order_by('state', 'area_type', 'name')
    customers = BusinessProfile.objects.filter(is_active=True).order_by('business_name')

    # Group areas by type
    area_groups = {}
    for area in areas:
        group = area.get_area_type_display()
        area_groups.setdefault(group, []).append(area)

    context = {
        'trades': trades,
        'areas': areas,
        'area_groups': area_groups,
        'customers': customers,
    }
    return render(request, 'service_pages/admin_bulk.html', context)


# ─── Admin: Submissions ──────────────────────────────────────────

@staff_member_required
def service_page_submissions(request):
    """View all form submissions across all pages."""
    subs = ServicePageSubmission.objects.select_related(
        'landing_page', 'landing_page__trade', 'landing_page__area', 'routed_to',
    ).all()

    status = request.GET.get('status')
    if status:
        subs = subs.filter(status=status)

    context = {'submissions': subs[:200]}
    return render(request, 'service_pages/admin_submissions.html', context)


@staff_member_required
def service_page_submission_action(request, submission_id):
    """Update a submission status."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    sub = get_object_or_404(ServicePageSubmission, pk=submission_id)
    data = json.loads(request.body)
    new_status = data.get('status')

    if new_status in dict(ServicePageSubmission.STATUS_CHOICES):
        sub.status = new_status
        sub.save(update_fields=['status'])
        return JsonResponse({'ok': True})

    return JsonResponse({'error': 'Invalid status'}, status=400)
