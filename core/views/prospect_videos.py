import hashlib
import json
import re
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Q, F

from core.models import ProspectVideo, BusinessProfile, Lead


# ─── Public Landing Page ───────────────────────────────────────────

def prospect_video_landing(request, slug):
    """Public-facing prospect video landing page."""
    video = get_object_or_404(ProspectVideo, slug=slug, status__in=['active', 'responded', 'converted'])

    # Increment page views
    ProspectVideo.objects.filter(pk=video.pk).update(page_views=F('page_views') + 1)

    # Parse YouTube embed URL
    youtube_embed = None
    if video.video_url:
        youtube_embed = _get_youtube_embed(video.video_url)

    context = {
        'video': video,
        'youtube_embed': youtube_embed,
        'is_white_label': video.is_white_label(),
    }
    return render(request, 'prospect_videos/landing.html', context)


def _get_youtube_embed(url):
    """Convert YouTube URL to embed URL."""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return f'https://www.youtube.com/embed/{match.group(1)}?autoplay=1&rel=0'
    return None


# ─── Tracking API ──────────────────────────────────────────────────

@csrf_exempt
@require_POST
def prospect_video_track(request):
    """Track page views, video plays, and CTA clicks."""
    try:
        data = json.loads(request.body)
        slug = data.get('slug')
        event = data.get('event')

        if not slug or event not in ('view', 'play', 'cta_click'):
            return JsonResponse({'error': 'Invalid request'}, status=400)

        field_map = {
            'view': 'page_views',
            'play': 'video_plays',
            'cta_click': 'cta_clicks',
        }

        updated = ProspectVideo.objects.filter(slug=slug).update(
            **{field_map[event]: F(field_map[event]) + 1}
        )

        if not updated:
            return JsonResponse({'error': 'Not found'}, status=404)

        return JsonResponse({'ok': True})
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


# ─── Intake Form Submission ─────────────────────────────────────────

@csrf_exempt
@require_POST
def prospect_video_intake(request):
    """Handle inline intake form submission from the prospect landing page."""
    try:
        data = json.loads(request.body)
        slug = data.get('slug', '').strip()
        business_name = data.get('business_name', '').strip()
        owner_name = data.get('owner_name', '').strip()
        phone = data.get('phone', '').strip()
        email = data.get('email', '').strip()
        service = data.get('service', '').strip()
        how_get_customers = data.get('how_get_customers', '').strip()

        if not slug or not phone or not email:
            return JsonResponse({'error': 'Phone and email are required.'}, status=400)

        video = ProspectVideo.objects.filter(slug=slug).first()
        if not video:
            return JsonResponse({'error': 'Not found'}, status=404)

        # Create a Lead
        content = f"Prospect video intake from {business_name} ({email}, {phone}). Service: {service}. Video page: /demo/{slug}/"
        content_hash = hashlib.sha256(f"prospect_video|{slug}|{email}|{phone}".encode()).hexdigest()

        # Check for duplicate submission
        if Lead.objects.filter(content_hash=content_hash).exists():
            return JsonResponse({'ok': True, 'message': 'Already submitted'})

        lead = Lead.objects.create(
            platform='manual',
            source_url=f'/demo/{slug}/',
            source_content=content,
            source_author=owner_name or business_name,
            detected_location=f"{video.prospect_city}, {video.prospect_state}",
            urgency_level='hot',
            urgency_score=90,
            confidence='high',
            review_status='unreviewed',
            content_hash=content_hash,
            state=video.prospect_state,
            region=video.prospect_city,
            source_group='social_media',
            source_type='prospect_video',
            contact_name=owner_name,
            contact_phone=phone,
            contact_email=email,
            contact_business=business_name,
            raw_data={
                'slug': slug,
                'service': service,
                'how_get_customers': how_get_customers,
                'prospect_video_id': video.pk,
                'trigger_type': video.trigger_type,
            },
        )

        # Mark ProspectVideo as responded
        video.prospect_responded = True
        video.response_date = timezone.now()
        video.response_notes = f"Intake form submitted: {email}, {phone}. Service: {service}"
        if video.status == 'active':
            video.status = 'responded'
        video.save(update_fields=['prospect_responded', 'response_date', 'response_notes', 'status'])

        # Send email notification to admin
        try:
            subject = f"New Prospect Video Intake: {business_name}"
            body = (
                f"A prospect submitted the intake form on /demo/{slug}/\n\n"
                f"Business: {business_name}\n"
                f"Owner: {owner_name}\n"
                f"Phone: {phone}\n"
                f"Email: {email}\n"
                f"Service: {service}\n"
                f"How they get customers: {how_get_customers or 'N/A'}\n\n"
                f"Trigger: {video.get_trigger_type_display()} — {video.trigger_detail or 'N/A'}\n"
                f"Video page: /demo/{slug}/\n"
                f"Lead ID: {lead.pk}\n"
            )
            send_mail(
                subject,
                body,
                getattr(settings, 'DEFAULT_FROM_EMAIL', 'alerts@salessignal.ai'),
                [getattr(settings, 'ALERT_FROM_EMAIL', 'alerts@salessignal.ai')],
                fail_silently=True,
            )
        except Exception:
            pass  # Don't fail the submission if email fails

        return JsonResponse({'ok': True})

    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)


# ─── Admin: List ───────────────────────────────────────────────────

@staff_member_required
def prospect_video_list(request):
    """Admin list of all prospect video pages."""
    videos = ProspectVideo.objects.all()

    # Filters
    status = request.GET.get('status')
    trade = request.GET.get('trade')
    ownership = request.GET.get('ownership')  # 'ours' or 'customer'
    q = request.GET.get('q', '').strip()

    if status:
        videos = videos.filter(status=status)
    if trade:
        videos = videos.filter(prospect_trade__iexact=trade)
    if ownership == 'ours':
        videos = videos.filter(customer__isnull=True, customer_business_name='')
    elif ownership == 'customer':
        videos = videos.filter(Q(customer__isnull=False) | ~Q(customer_business_name=''))
    if q:
        videos = videos.filter(
            Q(prospect_business_name__icontains=q) |
            Q(prospect_owner_name__icontains=q) |
            Q(slug__icontains=q)
        )

    trades = ProspectVideo.objects.values_list('prospect_trade', flat=True).distinct().order_by('prospect_trade')

    context = {
        'videos': videos,
        'trades': trades,
        'current_status': status or '',
        'current_trade': trade or '',
        'current_ownership': ownership or '',
        'current_q': q,
    }
    return render(request, 'prospect_videos/admin_list.html', context)


# ─── Admin: Create / Edit ─────────────────────────────────────────

TRADE_CHOICES = [
    'Plumbing', 'Electrical', 'HVAC', 'Commercial Cleaning', 'Roofing',
    'General Contracting', 'Pest Control', 'Landscaping', 'Moving',
    'Insurance', 'Legal', 'Other',
]

TRIGGER_CHOICES = [
    ('health_violation', 'Health Inspection Violation'),
    ('building_violation', 'Building Violation'),
    ('new_business', 'New Business Filing'),
    ('property_sale', 'Property Sale'),
    ('permit_filed', 'Permit Filed'),
    ('social_request', 'Social Media Request'),
    ('no_website', 'No Website Detected'),
    ('bad_reviews', 'Low Google Reviews'),
    ('competitor_issue', 'Competitor Issue'),
    ('custom', 'Custom Outreach'),
]


@staff_member_required
def prospect_video_create(request):
    """Create a new prospect video page."""
    customers = BusinessProfile.objects.filter(is_active=True).order_by('business_name')

    if request.method == 'POST':
        video = _save_prospect_video(request, None)
        if video:
            return redirect('prospect_video_edit', video_id=video.pk)

    context = {
        'video': None,
        'customers': customers,
        'trade_choices': TRADE_CHOICES,
        'trigger_choices': TRIGGER_CHOICES,
        'editing': False,
    }
    return render(request, 'prospect_videos/admin_form.html', context)


@staff_member_required
def prospect_video_edit(request, video_id):
    """Edit an existing prospect video page."""
    video = get_object_or_404(ProspectVideo, pk=video_id)
    customers = BusinessProfile.objects.filter(is_active=True).order_by('business_name')

    if request.method == 'POST':
        action = request.POST.get('action', 'save')

        if action == 'activate':
            video.status = 'active'
            video.save(update_fields=['status'])
            return redirect('prospect_video_edit', video_id=video.pk)
        elif action == 'mark_sms_sent':
            video.sms_sent = True
            video.sms_sent_at = timezone.now()
            video.save(update_fields=['sms_sent', 'sms_sent_at'])
            return redirect('prospect_video_edit', video_id=video.pk)
        elif action == 'mark_email_sent':
            video.email_sent = True
            video.email_sent_at = timezone.now()
            video.save(update_fields=['email_sent', 'email_sent_at'])
            return redirect('prospect_video_edit', video_id=video.pk)
        elif action == 'update_status':
            new_status = request.POST.get('new_status')
            if new_status in dict(ProspectVideo._meta.get_field('status').choices):
                video.status = new_status
                video.save(update_fields=['status'])
            return redirect('prospect_video_edit', video_id=video.pk)
        else:
            video = _save_prospect_video(request, video)
            if video:
                return redirect('prospect_video_edit', video_id=video.pk)

    # Generate script templates
    our_script = _generate_our_script(video)
    customer_script = _generate_customer_script(video) if video.is_white_label() else ''

    # Pre-formatted SMS and email
    sms_text = f"Hey {video.prospect_owner_name or 'there'} — we made something for {video.prospect_business_name}: salessignalai.com/demo/{video.slug} - Andrew, SalesSignal AI"
    email_text = f"Hi {video.prospect_owner_name or 'there'},\n\nI put together a quick video for {video.prospect_business_name} — I think you'll find it interesting.\n\nWatch it here: salessignalai.com/demo/{video.slug}\n\nBest,\nAndrew\nSalesSignal AI"

    context = {
        'video': video,
        'customers': customers,
        'trade_choices': TRADE_CHOICES,
        'trigger_choices': TRIGGER_CHOICES,
        'editing': True,
        'our_script': our_script,
        'customer_script': customer_script,
        'sms_text': sms_text,
        'email_text': email_text,
    }
    return render(request, 'prospect_videos/admin_form.html', context)


@staff_member_required
def prospect_video_stats(request, video_id):
    """View tracking stats for a prospect video."""
    video = get_object_or_404(ProspectVideo, pk=video_id)
    context = {'video': video}
    return render(request, 'prospect_videos/admin_stats.html', context)


# ─── Helpers ───────────────────────────────────────────────────────

def _save_prospect_video(request, video):
    """Save prospect video from POST data. Returns the video or None on error."""
    p = request.POST

    if not video:
        video = ProspectVideo()

    video.prospect_business_name = p.get('prospect_business_name', '').strip()
    video.prospect_owner_name = p.get('prospect_owner_name', '').strip()
    video.prospect_phone = p.get('prospect_phone', '').strip()
    video.prospect_email = p.get('prospect_email', '').strip()
    video.prospect_trade = p.get('prospect_trade', '').strip()
    video.prospect_city = p.get('prospect_city', '').strip()
    video.prospect_state = p.get('prospect_state', 'NY').strip()

    video.video_url = p.get('video_url', '').strip()
    video.video_thumbnail_url = p.get('video_thumbnail_url', '').strip()

    video.headline = p.get('headline', '').strip()
    video.custom_message = p.get('custom_message', '').strip()
    video.cta_text = p.get('cta_text', 'Book a Call').strip()
    video.cta_url = p.get('cta_url', '').strip()

    customer_id = p.get('customer_id')
    if customer_id:
        try:
            video.customer = BusinessProfile.objects.get(pk=int(customer_id))
        except (ValueError, BusinessProfile.DoesNotExist):
            video.customer = None
    else:
        video.customer = None

    video.customer_business_name = p.get('customer_business_name', '').strip()
    video.customer_phone = p.get('customer_phone', '').strip()
    video.customer_website = p.get('customer_website', '').strip()

    video.trigger_type = p.get('trigger_type', 'custom').strip()
    video.trigger_detail = p.get('trigger_detail', '').strip()

    slug = p.get('slug', '').strip()
    if not slug:
        slug = slugify(video.prospect_business_name)
    video.slug = slug

    video.save()
    return video


def _generate_our_script(video):
    return (
        f'"{video.prospect_business_name} has been serving {video.prospect_city} for years.\n'
        f'Your customers clearly value quality — and so do we.\n'
        f'But right now, people in your area are actively looking for a {video.prospect_trade.lower()}.\n'
        f'They\'re posting on community forums, they\'re searching online — '
        f'and most {video.prospect_trade.lower()}s don\'t even know these leads exist.\n'
        f'What if you were the first call they got?\n'
        f'I\'m Andrew with SalesSignal AI. Check the link — '
        f'I\'d love to show you what we found in your area."'
    )


def _generate_customer_script(video):
    return (
        f'"{video.prospect_business_name} — did you know {video.trigger_detail or "[trigger detail]"}?\n'
        f'{video.customer_business_name} specializes in exactly what you need right now.\n'
        f'With years serving {video.prospect_city}, they\'ve helped dozens of businesses just like yours.\n'
        f'Give them a call at {video.customer_phone or "[phone]"} — or click below to book an appointment."'
    )
