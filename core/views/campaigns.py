import json

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q, Count, Sum

from core.models import (
    OutreachCampaign, OutreachEmail, ProspectBusiness, ServiceCategory,
    Lead, Contact,
)
from core.models.outreach import OutreachProspect, GeneratedEmail
from core.utils.scrapers.google_maps import scrape_prospects
from core.utils.scrapers.website_email import extract_emails_from_website
from core.utils.email_engine.validator import validate_prospect_email
from core.utils.email_engine.ai_engine import (
    generate_email, enrich_prospect, classify_reply, generate_reply_draft,
)
from core.utils.email_engine.backends import get_email_sender
from core.utils.email_engine.sender import (
    send_outreach_email, process_campaign_queue, _append_unsubscribe_footer,
)
from core.utils.email_engine.followup import schedule_followups


def _get_business(request):
    bp = getattr(request.user, 'business_profile', None)
    return bp


def _get_effective_business(request):
    """Resolve effective business — own profile or salesperson's active customer."""
    bp = getattr(request.user, 'business_profile', None)
    if bp:
        return bp
    customer_id = request.session.get('active_customer_id')
    if customer_id:
        from core.models.business import BusinessProfile
        try:
            return BusinessProfile.objects.get(pk=customer_id)
        except BusinessProfile.DoesNotExist:
            pass
    return None


@login_required
def campaign_list(request):
    profile = _get_effective_business(request)
    if not profile:
        if request.user.is_staff:
            # Admin sees all campaigns
            campaigns = OutreachCampaign.objects.all().order_by('-created_at')
        else:
            return redirect('onboarding')
    else:
        campaigns = OutreachCampaign.objects.filter(business=profile).order_by('-created_at')

    # Annotate with prospect/email counts from new models
    for c in campaigns:
        c.prospect_count = c.prospects.count()
        c.new_prospect_count = c.prospects.filter(status='new').count()
        c.replied_count = c.prospects.filter(status__in=['replied', 'interested']).count()
        # Metrics used by list template
        sent_qs = GeneratedEmail.objects.filter(
            prospect__campaign=c, status__in=['sent', 'opened', 'replied']
        )
        c.emails_sent = sent_qs.count()
        c.emails_opened = GeneratedEmail.objects.filter(
            prospect__campaign=c, status__in=['opened', 'replied']
        ).count()
        c.emails_replied = GeneratedEmail.objects.filter(
            prospect__campaign=c, status='replied'
        ).count()
        c.open_rate_pct = round((c.emails_opened / c.emails_sent * 100) if c.emails_sent > 0 else 0)
        c.reply_rate_pct = round((c.emails_replied / c.emails_sent * 100) if c.emails_sent > 0 else 0)

    # Aggregate totals for summary cards
    total_sent = sum(c.emails_sent for c in campaigns)
    total_replied = sum(c.emails_replied for c in campaigns)

    context = {
        'campaigns': campaigns,
        'active_count': campaigns.filter(status='active').count(),
        'draft_count': campaigns.filter(status='draft').count(),
        'total_sent': total_sent,
        'total_replied': total_replied,
        'is_staff': request.user.is_staff or request.user.is_superuser,
    }
    return render(request, 'campaigns/list.html', context)


@login_required
def campaign_wizard(request):
    """Multi-step campaign creation wizard — staff/admin only."""
    if not (request.user.is_staff or request.user.is_superuser):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Campaign creation is managed by the SalesSignalAI team.')

    profile = _get_effective_business(request)
    if not profile:
        return redirect('onboarding')

    if request.method == 'POST':
        step = request.POST.get('step', '1')

        if step == '1':
            name = request.POST.get('name', '').strip()
            target_types = request.POST.getlist('target_types')
            target_zips = request.POST.get('target_zip_codes', '').strip()
            target_radius = request.POST.get('target_radius', '25')
            target_category = request.POST.get('target_category', '').strip()
            target_location = request.POST.get('target_location', '').strip()

            if not name:
                return JsonResponse({'error': 'Campaign name is required'}, status=400)

            zip_list = [z.strip() for z in target_zips.split(',') if z.strip()] if target_zips else []

            campaign = OutreachCampaign.objects.create(
                business=profile,
                name=name,
                target_business_types=target_types,
                target_zip_codes=zip_list,
                target_radius_miles=int(target_radius) if target_radius else 25,
                target_category=target_category,
                target_location=target_location,
                status='draft',
            )

            return JsonResponse({
                'success': True,
                'campaign_id': campaign.id,
                'next_step': 2,
            })

        elif step == '2':
            campaign_id = request.POST.get('campaign_id')
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

            campaign.email_subject_template = request.POST.get('subject_template', '')
            campaign.email_body_template = request.POST.get('body_template', '')
            campaign.use_ai_personalization = request.POST.get('use_ai', 'on') == 'on'
            campaign.email_style = request.POST.get('email_style', 'professional')
            campaign.customer_custom_instructions = request.POST.get('custom_instructions', '')
            campaign.reply_to_email = request.POST.get('reply_to_email', '') or profile.email
            campaign.sending_email = request.POST.get('sending_email', '')
            campaign.send_mode = request.POST.get('send_mode', 'salessignal')
            campaign.save(update_fields=[
                'email_subject_template', 'email_body_template', 'use_ai_personalization',
                'email_style', 'customer_custom_instructions',
                'reply_to_email', 'sending_email', 'send_mode',
            ])

            return JsonResponse({
                'success': True,
                'campaign_id': campaign.id,
                'next_step': 3,
            })

        elif step == '3':
            campaign_id = request.POST.get('campaign_id')
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

            campaign.daily_send_limit = int(request.POST.get('daily_limit', 15))
            campaign.max_emails_per_day = int(request.POST.get('max_per_day', 25))
            campaign.followup_delay_days = int(request.POST.get('followup_delay', 3))
            campaign.email_sequence_count = int(request.POST.get('sequence_count', 3))
            campaign.save(update_fields=[
                'daily_send_limit', 'max_emails_per_day',
                'followup_delay_days', 'email_sequence_count',
            ])

            return JsonResponse({
                'success': True,
                'campaign_id': campaign.id,
                'next_step': 4,
            })

        elif step == '4':
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
            campaign_id = request.POST.get('campaign_id')
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

            prospect_ids = request.POST.getlist('prospect_ids')
            if not prospect_ids:
                prospects = ProspectBusiness.objects.filter(
                    email_validated=True,
                ).exclude(email='')
                if campaign.target_zip_codes:
                    prospects = prospects.filter(zip_code__in=campaign.target_zip_codes)
                prospect_ids = list(prospects.values_list('id', flat=True)[:100])

            # Create OutreachProspect records from ProspectBusiness
            created_count = 0
            for pid in prospect_ids:
                try:
                    pb = ProspectBusiness.objects.get(id=pid)
                    to_email = pb.email or pb.owner_email
                    if not to_email:
                        continue

                    # Skip if already added to this campaign
                    if OutreachProspect.objects.filter(
                        campaign=campaign, contact_email=to_email
                    ).exists():
                        continue

                    OutreachProspect.objects.create(
                        campaign=campaign,
                        prospect_business=pb,
                        business_name=pb.name,
                        contact_name=pb.owner_name,
                        contact_email=to_email,
                        contact_phone=pb.phone,
                        website_url=pb.website,
                        source='google_maps' if pb.google_place_id else 'manual_upload',
                        status='new',
                    )
                    created_count += 1
                except ProspectBusiness.DoesNotExist:
                    continue

            campaign.total_prospects = campaign.prospects.count()
            campaign.status = 'active'
            campaign.save(update_fields=['total_prospects', 'status'])

            return JsonResponse({
                'success': True,
                'redirect': f'/campaigns/{campaign.id}/',
                'prospects_added': created_count,
            })

    # GET: render wizard
    categories = ServiceCategory.objects.filter(is_active=True)

    # Group categories by industry_group for organized display
    from collections import OrderedDict
    grouped_categories = OrderedDict()
    group_labels = dict(ServiceCategory.INDUSTRY_GROUPS)
    for group_key, group_label in ServiceCategory.INDUSTRY_GROUPS:
        cats = [c for c in categories if c.industry_group == group_key]
        if cats:
            grouped_categories[group_key] = {
                'label': group_label,
                'categories': cats,
            }

    context = {
        'categories': categories,
        'grouped_categories': grouped_categories,
        'profile': profile,
    }
    return render(request, 'campaigns/wizard.html', context)


@login_required
def campaign_detail(request, campaign_id):
    profile = _get_effective_business(request)
    if not profile:
        if request.user.is_staff:
            campaign = get_object_or_404(OutreachCampaign, id=campaign_id)
        else:
            return redirect('onboarding')
    else:
        campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

    # Get prospects with their emails
    prospects = campaign.prospects.all().order_by('-updated_at')

    status_filter = request.GET.get('status', '')
    if status_filter:
        prospects = prospects.filter(status=status_filter)

    # Calculate metrics from OutreachProspect statuses
    total_prospects = campaign.prospects.count()
    email1_sent = campaign.prospects.filter(
        status__in=['email1_sent', 'email2_sent', 'email3_sent', 'replied', 'interested'],
    ).count()
    email2_sent = campaign.prospects.filter(
        status__in=['email2_sent', 'email3_sent', 'replied', 'interested'],
    ).count()
    replied = campaign.prospects.filter(status__in=['replied', 'interested']).count()
    interested = campaign.prospects.filter(status='interested').count()
    bounced = campaign.prospects.filter(status='bounced').count()

    # Emails stats from GeneratedEmail
    total_emails = GeneratedEmail.objects.filter(prospect__campaign=campaign).count()
    sent_emails = GeneratedEmail.objects.filter(
        prospect__campaign=campaign, status__in=['sent', 'opened', 'replied'],
    ).count()
    opened_emails = GeneratedEmail.objects.filter(
        prospect__campaign=campaign, status__in=['opened', 'replied'],
    ).count()

    open_rate = round((opened_emails / sent_emails * 100) if sent_emails > 0 else 0)
    reply_rate = round((replied / sent_emails * 100) if sent_emails > 0 else 0)

    # Drip sequence stats
    email3_sent = campaign.prospects.filter(
        status__in=['email3_sent', 'replied', 'interested'],
    ).count() if campaign.email_sequence_count >= 3 else 0
    new_count = campaign.prospects.filter(status='new').count()

    # Email 1 open rate
    e1_opened = GeneratedEmail.objects.filter(
        prospect__campaign=campaign, sequence_number=1, status__in=['opened', 'replied'],
    ).count()
    e1_total = GeneratedEmail.objects.filter(
        prospect__campaign=campaign, sequence_number=1, status__in=['sent', 'opened', 'replied'],
    ).count()
    email1_open_pct = round((e1_opened / e1_total * 100) if e1_total > 0 else 0)

    # Completed full sequence = email3_sent + replied + interested (those who went through all 3)
    max_step = campaign.email_sequence_count
    if max_step >= 3:
        completed = campaign.prospects.filter(status__in=['email3_sent', 'replied', 'interested']).count()
    elif max_step == 2:
        completed = campaign.prospects.filter(status__in=['email2_sent', 'replied', 'interested']).count()
    else:
        completed = campaign.prospects.filter(status__in=['email1_sent', 'replied', 'interested']).count()
    completed_pct = round((completed / total_prospects * 100) if total_prospects > 0 else 0)

    context = {
        'campaign': campaign,
        'prospects': prospects[:100],
        'total_prospects': total_prospects,
        'email1_sent': email1_sent,
        'email2_sent': email2_sent,
        'email3_sent': email3_sent,
        'total_emails': total_emails,
        'sent_emails': sent_emails,
        'opened_emails': opened_emails,
        'replied_count': replied,
        'interested_count': interested,
        'bounced_count': bounced,
        'new_count': new_count,
        'open_rate': open_rate,
        'reply_rate': reply_rate,
        'email1_open_pct': email1_open_pct,
        'completed_pct': completed_pct,
        'current_status': status_filter,
        'prospect_statuses': OutreachProspect.STATUS_CHOICES,
        'is_staff': request.user.is_staff or request.user.is_superuser,
    }
    return render(request, 'campaigns/detail.html', context)


@login_required
def campaign_action(request, campaign_id):
    """Pause, resume, complete, or send queue for a campaign — staff only."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Campaign actions are managed by the SalesSignalAI team.'}, status=403)

    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

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
def campaign_add_prospects(request, campaign_id):
    """Add prospects to a campaign manually."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)

    business_name = request.POST.get('business_name', '').strip()
    contact_email = request.POST.get('contact_email', '').strip()
    contact_name = request.POST.get('contact_name', '').strip()
    contact_phone = request.POST.get('contact_phone', '').strip()
    website_url = request.POST.get('website_url', '').strip()

    if not business_name or not contact_email:
        return JsonResponse({'error': 'Business name and email are required'}, status=400)

    # Check for duplicate
    if OutreachProspect.objects.filter(campaign=campaign, contact_email=contact_email).exists():
        return JsonResponse({'error': 'Prospect already in this campaign'}, status=400)

    OutreachProspect.objects.create(
        campaign=campaign,
        business_name=business_name,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        website_url=website_url,
        source='manual_upload',
        status='new',
    )

    campaign.total_prospects = campaign.prospects.count()
    campaign.save(update_fields=['total_prospects'])

    return JsonResponse({'success': True})


@login_required
def prospect_detail_api(request, campaign_id, prospect_id):
    """Get prospect details including generated emails and enrichment data."""
    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

    prospect = get_object_or_404(
        OutreachProspect,
        id=prospect_id,
        campaign_id=campaign_id,
        campaign__business=profile,
    )

    emails = []
    for ge in prospect.generated_emails.order_by('sequence_number'):
        emails.append({
            'sequence': ge.sequence_number,
            'subject': ge.subject,
            'body': ge.body,
            'status': ge.status,
            'model': ge.ai_model_used,
            'sent_at': ge.sent_at.isoformat() if ge.sent_at else None,
            'opened_at': ge.opened_at.isoformat() if ge.opened_at else None,
        })

    data = {
        'id': prospect.id,
        'business_name': prospect.business_name,
        'contact_name': prospect.contact_name,
        'contact_email': prospect.contact_email,
        'contact_phone': prospect.contact_phone,
        'website_url': prospect.website_url,
        'source': prospect.get_source_display(),
        'status': prospect.status,
        'enrichment': prospect.enrichment_data or {},
        'emails': emails,
        'reply_text': prospect.reply_text,
        'reply_classification': prospect.reply_classification,
    }

    return JsonResponse(data)


@login_required
def prospect_mark_status(request, campaign_id, prospect_id):
    """Mark a prospect as interested/not interested."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

    prospect = get_object_or_404(
        OutreachProspect,
        id=prospect_id,
        campaign_id=campaign_id,
        campaign__business=profile,
    )

    new_status = request.POST.get('status', '')
    if new_status not in ['interested', 'not_interested']:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    prospect.status = new_status
    prospect.save(update_fields=['status', 'updated_at'])

    # If interested, create Contact in CRM
    if new_status == 'interested':
        _create_contact_from_prospect(prospect)

    return JsonResponse({'success': True, 'status': new_status})


def _create_contact_from_prospect(prospect):
    """Auto-create a CRM Contact when a prospect is marked as interested."""
    from core.models.crm import Contact, Activity

    campaign = prospect.campaign
    bp = campaign.business

    # Skip if contact already exists for this email
    if Contact.objects.filter(business=bp, email=prospect.contact_email).exists():
        return

    contact = Contact.objects.create(
        business=bp,
        name=prospect.contact_name or prospect.business_name,
        email=prospect.contact_email,
        phone=prospect.contact_phone,
        address=prospect.website_url,
        source='outreach',
        source_platform='email_campaign',
        source_prospect=prospect.prospect_business,
        pipeline_stage='contacted',
        service_needed=campaign.target_category or '',
    )

    Activity.objects.create(
        contact=contact,
        activity_type='email_replied',
        description=f'Replied to outreach campaign "{campaign.name}" — marked as interested',
    )


@login_required
def compose_email(request):
    """Standalone email compose page with template support."""
    from core.models.sales import EmailTemplate
    templates = EmailTemplate.objects.all()
    context = {
        'templates': templates,
        'to': request.GET.get('to', ''),
        'name': request.GET.get('name', ''),
        'subject': request.GET.get('subject', ''),
        'lead_id': request.GET.get('lead_id', ''),
        'contact_id': request.GET.get('contact_id', ''),
    }
    return render(request, 'email/compose.html', context)


@login_required
def email_templates_api(request):
    """CRUD API for email templates."""
    from core.models.sales import EmailTemplate

    if request.method == 'GET':
        templates = EmailTemplate.objects.all()
        data = [{'id': t.id, 'name': t.name, 'category': t.category,
                 'subject': t.subject, 'body': t.body} for t in templates]
        return JsonResponse({'templates': data})

    if request.method == 'POST':
        data = json.loads(request.body)
        t = EmailTemplate.objects.create(
            name=data['name'],
            category=data.get('category', 'custom'),
            subject=data.get('subject', ''),
            body=data.get('body', ''),
            created_by=request.user,
        )
        return JsonResponse({'ok': True, 'id': t.id, 'name': t.name})

    if request.method == 'DELETE':
        data = json.loads(request.body)
        EmailTemplate.objects.filter(id=data.get('id')).delete()
        return JsonResponse({'ok': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def quick_send_email(request):
    """Send a quick one-off email via SendGrid (AJAX)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

    data = json.loads(request.body)
    to_email = data.get('to', '').strip()
    subject = data.get('subject', '').strip()
    body = data.get('body', '').strip()
    cc = data.get('cc', '').strip()

    if not to_email or not subject or not body:
        return JsonResponse({'error': 'To, subject, and body are required'}, status=400)

    from core.utils.email_engine.backends import SendGridEmailSender
    sender = SendGridEmailSender()

    from_email = getattr(profile, 'sending_email', '') or f'support@salessignalai.com'
    reply_to = profile.email or ''

    result = sender.send_email(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=from_email,
        reply_to=reply_to,
        html_body=body.replace('\n', '<br>') if '<' not in body else body,
    )

    if result['success']:
        return JsonResponse({
            'ok': True,
            'message_id': result['message_id'],
            'message': f'Email sent to {to_email}',
        })
    else:
        return JsonResponse({
            'ok': False,
            'error': result.get('error', 'Send failed'),
        }, status=400)


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


@login_required
def campaign_leads_api(request):
    """AJAX endpoint returning leads for importing into campaigns."""
    leads = Lead.objects.all().order_by('-discovered_at')

    source_type = request.GET.get('source_type', '')
    if source_type:
        leads = leads.filter(source_type=source_type)

    urgency = request.GET.get('urgency', '')
    if urgency:
        leads = leads.filter(urgency_level=urgency)

    region = request.GET.get('region', '')
    if region:
        leads = leads.filter(Q(region__icontains=region) | Q(detected_location__icontains=region))

    data = []
    for lead in leads[:200]:
        data.append({
            'id': lead.id,
            'business': lead.contact_business or '',
            'name': lead.contact_name or lead.source_author or '',
            'phone': lead.contact_phone or '',
            'email': lead.contact_email or '',
            'source_type': lead.get_source_type_display() if lead.source_type else lead.get_platform_display(),
            'urgency': lead.urgency_level,
            'location': lead.detected_location or lead.region or '',
            'date': lead.discovered_at.strftime('%b %d') if lead.discovered_at else '',
        })

    return JsonResponse({'leads': data, 'total': leads.count()})


@login_required
def campaign_import_leads(request, campaign_id):
    """Import selected leads into a campaign as OutreachProspects."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)
    data = json.loads(request.body)
    lead_ids = data.get('lead_ids', [])

    if not lead_ids:
        return JsonResponse({'error': 'No leads selected'}, status=400)

    leads = Lead.objects.filter(pk__in=lead_ids)
    added = 0
    skipped = 0

    for lead in leads:
        email = lead.contact_email
        if not email:
            skipped += 1
            continue
        if OutreachProspect.objects.filter(campaign=campaign, contact_email=email).exists():
            skipped += 1
            continue

        OutreachProspect.objects.create(
            campaign=campaign,
            business_name=lead.contact_business or lead.source_author or 'Unknown',
            contact_name=lead.contact_name or '',
            contact_email=email,
            contact_phone=lead.contact_phone or '',
            website_url='',
            source='lead_import',
            status='new',
        )
        added += 1

    campaign.total_prospects = campaign.prospects.count()
    campaign.save(update_fields=['total_prospects'])

    return JsonResponse({'ok': True, 'added': added, 'skipped': skipped})


@login_required
def campaign_contacts_api(request):
    """AJAX endpoint returning CRM contacts for campaign import."""
    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'contacts': [], 'total': 0})

    contacts = Contact.objects.filter(business=profile).order_by('-created_at')

    data = []
    for c in contacts[:200]:
        data.append({
            'id': c.id,
            'name': c.name,
            'email': c.email or '',
            'phone': c.phone or '',
            'source': c.get_source_display(),
            'stage': c.get_stage_display(),
        })

    return JsonResponse({'contacts': data, 'total': contacts.count()})


@login_required
def campaign_import_contacts(request, campaign_id):
    """Import selected CRM contacts into a campaign."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)
    data = json.loads(request.body)
    contact_ids = data.get('contact_ids', [])

    if not contact_ids:
        return JsonResponse({'error': 'No contacts selected'}, status=400)

    contacts = Contact.objects.filter(pk__in=contact_ids, business=profile)
    added = 0
    skipped = 0

    for c in contacts:
        if not c.email:
            skipped += 1
            continue
        if OutreachProspect.objects.filter(campaign=campaign, contact_email=c.email).exists():
            skipped += 1
            continue

        OutreachProspect.objects.create(
            campaign=campaign,
            business_name=c.name,
            contact_name=c.name,
            contact_email=c.email,
            contact_phone=c.phone or '',
            source='crm_import',
            status='new',
        )
        added += 1

    campaign.total_prospects = campaign.prospects.count()
    campaign.save(update_fields=['total_prospects'])

    return JsonResponse({'ok': True, 'added': added, 'skipped': skipped})


@login_required
def campaign_import_csv(request, campaign_id):
    """Import prospects from CSV upload into a campaign."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    import csv
    import io

    profile = _get_effective_business(request)
    if not profile:
        return JsonResponse({'error': 'No business profile'}, status=403)

    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, business=profile)
    csv_file = request.FILES.get('csv_file')

    if not csv_file:
        return JsonResponse({'error': 'No file uploaded'}, status=400)

    try:
        decoded = csv_file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(decoded))
        added = 0
        skipped = 0

        for row in reader:
            email = (row.get('email', '') or row.get('Email', '') or '').strip()
            if not email:
                skipped += 1
                continue
            if OutreachProspect.objects.filter(campaign=campaign, contact_email=email).exists():
                skipped += 1
                continue

            business_name = (row.get('business_name', '') or row.get('Business Name', '') or row.get('company', '') or '').strip()
            contact_name = (row.get('contact_name', '') or row.get('Contact Name', '') or row.get('name', '') or row.get('Name', '') or '').strip()
            phone = (row.get('phone', '') or row.get('Phone', '') or '').strip()

            OutreachProspect.objects.create(
                campaign=campaign,
                business_name=business_name or contact_name or email,
                contact_name=contact_name,
                contact_email=email,
                contact_phone=phone,
                source='csv_upload',
                status='new',
            )
            added += 1

        campaign.total_prospects = campaign.prospects.count()
        campaign.save(update_fields=['total_prospects'])

        return JsonResponse({'ok': True, 'added': added, 'skipped': skipped})

    except Exception as e:
        return JsonResponse({'error': f'Error parsing CSV: {str(e)}'}, status=400)
