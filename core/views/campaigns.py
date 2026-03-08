import json

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q

from core.models import (
    OutreachCampaign, OutreachEmail, ProspectBusiness, ServiceCategory,
)
from core.utils.scrapers.google_maps import scrape_prospects
from core.utils.scrapers.website_email import extract_emails_from_website
from core.utils.email_engine.validator import validate_prospect_email
from core.utils.email_engine.ai_writer import generate_outreach_email
from core.utils.email_engine.sender import send_outreach_email, process_campaign_queue
from core.utils.email_engine.followup import schedule_followups


@login_required
def campaign_list(request):
    profile = request.user.business_profile
    campaigns = OutreachCampaign.objects.filter(business=profile).order_by('-created_at')

    context = {
        'campaigns': campaigns,
        'active_count': campaigns.filter(status='active').count(),
        'draft_count': campaigns.filter(status='draft').count(),
    }
    return render(request, 'campaigns/list.html', context)


@login_required
def campaign_wizard(request):
    """Multi-step campaign creation wizard."""
    profile = request.user.business_profile

    if request.method == 'POST':
        step = request.POST.get('step', '1')

        if step == '1':
            # Step 1: Name + target business type + geography
            name = request.POST.get('name', '').strip()
            target_types = request.POST.getlist('target_types')
            target_zips = request.POST.get('target_zip_codes', '').strip()
            target_radius = request.POST.get('target_radius', '25')

            if not name:
                return JsonResponse({'error': 'Campaign name is required'}, status=400)

            zip_list = [z.strip() for z in target_zips.split(',') if z.strip()] if target_zips else []

            campaign = OutreachCampaign.objects.create(
                business=profile,
                name=name,
                target_business_types=target_types,
                target_zip_codes=zip_list,
                target_radius_miles=int(target_radius) if target_radius else 25,
                status='draft',
            )

            return JsonResponse({
                'success': True,
                'campaign_id': campaign.id,
                'next_step': 2,
            })

        elif step == '2':
            # Step 2: Email template
            campaign_id = request.POST.get('campaign_id')
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

            campaign.email_subject_template = request.POST.get('subject_template', '')
            campaign.email_body_template = request.POST.get('body_template', '')
            campaign.use_ai_personalization = request.POST.get('use_ai', 'on') == 'on'
            campaign.save(update_fields=[
                'email_subject_template', 'email_body_template', 'use_ai_personalization',
            ])

            return JsonResponse({
                'success': True,
                'campaign_id': campaign.id,
                'next_step': 3,
            })

        elif step == '3':
            # Step 3: Sending pace + follow-up schedule
            campaign_id = request.POST.get('campaign_id')
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

            campaign.max_emails_per_day = int(request.POST.get('max_per_day', 25))
            campaign.followup_delay_days = int(request.POST.get('followup_delay', 3))
            campaign.max_followups = int(request.POST.get('max_followups', 2))
            campaign.save(update_fields=[
                'max_emails_per_day', 'followup_delay_days', 'max_followups',
            ])

            return JsonResponse({
                'success': True,
                'campaign_id': campaign.id,
                'next_step': 4,
            })

        elif step == '4':
            # Step 4: Scrape prospects
            campaign_id = request.POST.get('campaign_id')
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

            query = request.POST.get('search_query', '')
            if query:
                stats = scrape_prospects(
                    query=query,
                    zip_codes=campaign.target_zip_codes or None,
                    radius_miles=campaign.target_radius_miles or 25,
                    max_per_query=20,
                )
                return JsonResponse({
                    'success': True,
                    'stats': stats,
                    'campaign_id': campaign.id,
                })

            return JsonResponse({'success': True, 'campaign_id': campaign.id})

        elif step == '5':
            # Step 5: Launch campaign
            campaign_id = request.POST.get('campaign_id')
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

            prospect_ids = request.POST.getlist('prospect_ids')
            if not prospect_ids:
                # Use all available prospects
                prospects = ProspectBusiness.objects.filter(
                    email_validated=True,
                ).exclude(
                    email='',
                ).exclude(
                    outreach_emails__campaign=campaign,
                )
                if campaign.target_zip_codes:
                    prospects = prospects.filter(zip_code__in=campaign.target_zip_codes)
                prospect_ids = list(prospects.values_list('id', flat=True)[:100])

            # Generate initial emails for each prospect
            generated = 0
            for pid in prospect_ids:
                try:
                    prospect = ProspectBusiness.objects.get(id=pid)
                    to_email = prospect.email or prospect.owner_email
                    if not to_email:
                        continue

                    # Check for existing email in this campaign
                    if OutreachEmail.objects.filter(campaign=campaign, prospect=prospect).exists():
                        continue

                    if campaign.use_ai_personalization:
                        content = generate_outreach_email(prospect, campaign, 1)
                    else:
                        from core.utils.email_engine.ai_writer import _template_fallback
                        content = _template_fallback(prospect, campaign, 1)

                    if content:
                        OutreachEmail.objects.create(
                            campaign=campaign,
                            prospect=prospect,
                            sequence_number=1,
                            subject=content['subject'],
                            body=content['body'],
                            status='queued',
                        )
                        generated += 1
                except Exception:
                    continue

            campaign.total_prospects = generated
            campaign.status = 'active'
            campaign.save(update_fields=['total_prospects', 'status'])

            return JsonResponse({
                'success': True,
                'redirect': f'/campaigns/{campaign.id}/',
                'generated': generated,
            })

    # GET: render wizard
    categories = ServiceCategory.objects.filter(is_active=True)
    context = {
        'categories': categories,
        'profile': profile,
    }
    return render(request, 'campaigns/wizard.html', context)


@login_required
def campaign_detail(request, campaign_id):
    profile = request.user.business_profile
    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

    emails = campaign.emails.select_related('prospect').order_by('-created_at')

    # Status filter
    status_filter = request.GET.get('status', '')
    if status_filter:
        emails = emails.filter(status=status_filter)

    # Calculate metrics
    total = campaign.emails.count()
    sent = campaign.emails.filter(status__in=['sent', 'delivered', 'opened', 'replied']).count()
    opened = campaign.emails.filter(status__in=['opened', 'replied']).count()
    replied = campaign.emails.filter(status='replied').count()
    open_rate = round((opened / sent * 100) if sent > 0 else 0)
    reply_rate = round((replied / sent * 100) if sent > 0 else 0)

    context = {
        'campaign': campaign,
        'emails': emails[:100],
        'total_emails': total,
        'sent_count': sent,
        'opened_count': opened,
        'replied_count': replied,
        'open_rate': open_rate,
        'reply_rate': reply_rate,
        'current_status': status_filter,
        'email_statuses': OutreachEmail.STATUS_CHOICES,
    }
    return render(request, 'campaigns/detail.html', context)


@login_required
def campaign_action(request, campaign_id):
    """Pause, resume, or complete a campaign."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = request.user.business_profile
    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

    action = request.POST.get('action', '')

    if action == 'pause':
        campaign.status = 'paused'
    elif action == 'resume':
        campaign.status = 'active'
    elif action == 'complete':
        campaign.status = 'completed'
    elif action == 'send_queue':
        stats = process_campaign_queue(campaign.id)
        return JsonResponse({'success': True, 'stats': stats})
    elif action == 'schedule_followups':
        stats = schedule_followups(campaign.id)
        return JsonResponse({'success': True, 'stats': stats})
    else:
        return JsonResponse({'error': 'Invalid action'}, status=400)

    campaign.save(update_fields=['status'])

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'status': campaign.status})

    return redirect('campaign_detail', campaign_id=campaign.id)


@login_required
def prospect_scrape(request):
    """AJAX endpoint to scrape prospects via Google Maps."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    query = request.POST.get('query', '').strip()
    zip_codes_raw = request.POST.get('zip_codes', '')
    radius = int(request.POST.get('radius', 25))

    if not query:
        return JsonResponse({'error': 'Search query required'}, status=400)

    zip_list = [z.strip() for z in zip_codes_raw.split(',') if z.strip()] if zip_codes_raw else None

    stats = scrape_prospects(query=query, zip_codes=zip_list, radius_miles=radius)
    return JsonResponse({'success': True, 'stats': stats})


@login_required
def prospect_find_email(request, prospect_id):
    """AJAX endpoint to crawl a prospect's website for email."""
    prospect = get_object_or_404(ProspectBusiness, id=prospect_id)

    if not prospect.website:
        return JsonResponse({'error': 'No website URL'}, status=400)

    result = extract_emails_from_website(prospect.website)

    if result['emails']:
        prospect.email = result['emails'][0]
        if result.get('owner_name'):
            prospect.owner_name = result['owner_name']
        prospect.save(update_fields=['email', 'owner_name'])

    return JsonResponse({
        'success': True,
        'emails': result['emails'],
        'owner_name': result.get('owner_name', ''),
    })


@login_required
def prospect_validate(request, prospect_id):
    """AJAX endpoint to validate a prospect's email."""
    prospect = get_object_or_404(ProspectBusiness, id=prospect_id)
    status = validate_prospect_email(prospect.id)
    prospect.refresh_from_db()

    return JsonResponse({
        'success': True,
        'status': status,
        'validated': prospect.email_validated,
    })


@login_required
def prospect_list_api(request):
    """AJAX endpoint returning prospect list for campaign wizard."""
    prospects = ProspectBusiness.objects.all().order_by('-created_at')

    zip_filter = request.GET.get('zip_codes', '')
    if zip_filter:
        zips = [z.strip() for z in zip_filter.split(',')]
        prospects = prospects.filter(zip_code__in=zips)

    category = request.GET.get('category', '')
    if category:
        prospects = prospects.filter(category__icontains=category)

    validated_only = request.GET.get('validated', '')
    if validated_only == '1':
        prospects = prospects.filter(email_validated=True).exclude(email='')

    data = []
    for p in prospects[:100]:
        data.append({
            'id': p.id,
            'name': p.name,
            'category': p.category,
            'city': p.city,
            'state': p.state,
            'email': p.email or p.owner_email,
            'email_validated': p.email_validated,
            'google_rating': p.google_rating,
            'website': p.website,
        })

    return JsonResponse({'prospects': data, 'total': prospects.count()})
