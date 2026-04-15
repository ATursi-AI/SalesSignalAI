from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, Sum, Q
from django.utils import timezone
from datetime import timedelta
from core.models import LeadAssignment, Lead, TrackedCompetitor, CompetitorReview
from core.models.outreach import OutreachCampaign, OutreachProspect, GeneratedEmail


@login_required
def dashboard_home(request):
    from django.shortcuts import redirect

    # Salespeople without a business profile → redirect to sales dashboard
    if not hasattr(request.user, 'business_profile') or not request.user.business_profile:
        if hasattr(request.user, 'salesperson_profile'):
            return redirect('sales_dashboard')
        return redirect('onboarding')

    profile = request.user.business_profile
    if not profile.onboarding_complete:
        return redirect('onboarding')

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    assignments = LeadAssignment.objects.filter(business=profile)

    hot_leads = assignments.filter(lead__urgency_level='hot', status='new').count()
    leads_this_week = assignments.filter(created_at__gte=week_ago).count()
    total_leads = assignments.count()
    contacted = assignments.filter(status__in=['contacted', 'quoted', 'won']).count()
    response_rate = round((contacted / total_leads * 100) if total_leads > 0 else 0)
    leads_won = assignments.filter(status='won', updated_at__gte=month_ago).count()

    recent_assignments = assignments.select_related('lead', 'lead__detected_service_type').order_by('-created_at')[:10]

    # Quick Stats: avg response time
    contacted_qs = assignments.filter(contacted_at__isnull=False).annotate(
        response_delta=F('contacted_at') - F('created_at'),
    )
    avg_td = contacted_qs.aggregate(avg=Avg('response_delta'))['avg']
    avg_response = f"{round(avg_td.total_seconds() / 3600, 1)}h" if avg_td else None

    # Quick Stats: best platform
    top_platform_row = assignments.filter(
        created_at__gte=month_ago,
    ).values('lead__platform').annotate(c=Count('id')).order_by('-c').first()
    best_platform = top_platform_row['lead__platform'].replace('_', ' ').title() if top_platform_row else None

    # Quick Stats: top area
    top_area_row = assignments.filter(
        lead__detected_location__gt='', created_at__gte=month_ago,
    ).values('lead__detected_location').annotate(c=Count('id')).order_by('-c').first()
    top_area = top_area_row['lead__detected_location'] if top_area_row else None

    # Quick Stats: conversion rate
    total_30d = assignments.filter(created_at__gte=month_ago).count()
    won_30d = assignments.filter(status='won', created_at__gte=month_ago).count()
    conversion_rate = f"{round(won_30d / total_30d * 100)}%" if total_30d else None

    # Competitor summary
    competitors = TrackedCompetitor.objects.filter(business=profile, is_active=True)
    competitor_count = competitors.count()
    competitor_neg = CompetitorReview.objects.filter(
        competitor__in=competitors, is_negative=True, review_date__gte=week_ago,
    ).count() if competitor_count else 0

    # Public records leads breakdown (last 30 days)
    public_records_platforms = [
        'building_permit', 'property_sale', 'new_business_filing',
        'code_violation', 'health_inspection', 'license_expiry',
        'eviction_filing', 'bbb',
    ]
    public_records_stats = (
        assignments.filter(
            lead__platform__in=public_records_platforms,
            created_at__gte=month_ago,
        )
        .values('lead__platform')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    public_records_total = sum(s['count'] for s in public_records_stats)
    public_records_breakdown = [
        {
            'platform': s['lead__platform'],
            'label': s['lead__platform'].replace('_', ' ').title(),
            'count': s['count'],
        }
        for s in public_records_stats
    ]

    # Welcome banner: show on first visit, with monitoring info
    show_welcome = not profile.has_seen_welcome
    source_count = 34  # Total monitor sources

    # For empty state: nationwide leads in their service category (last 24h)
    nationwide_recent = []
    if total_leads == 0 and profile.service_category:
        nationwide_recent = list(
            Lead.objects.filter(
                detected_service_type=profile.service_category,
                discovered_at__gte=now - timedelta(hours=24),
            )
            .order_by('-discovered_at')[:5]
            .values('platform', 'source_content', 'detected_location', 'discovered_at')
        )

    is_trial = (profile.account_status == 'trial' or profile.subscription_tier == 'none')
    trial_leads_used = max(0, 10 - profile.trial_leads_remaining) if is_trial else 0

    # ROI metrics
    total_delivered = assignments.count()
    total_contacted = assignments.filter(status__in=['contacted', 'quoted', 'won']).count()
    total_won = assignments.filter(status='won').count()
    total_revenue = assignments.filter(status='won').aggregate(
        rev=Sum('revenue'))['rev'] or 0
    tier_prices = {'outreach': 149, 'growth': 349, 'dominate': 649}
    monthly_cost = tier_prices.get(profile.subscription_tier, 0)
    roi_multiple = round(float(total_revenue) / monthly_cost, 1) if monthly_cost and total_revenue else 0

    # ── Outreach Activity (managed campaigns run by SalesSignalAI team) ──
    customer_campaigns = OutreachCampaign.objects.filter(business=profile)
    outreach_total_sent = 0
    outreach_total_opened = 0
    outreach_total_replied = 0
    outreach_active_campaigns = 0
    recent_replies = []

    if customer_campaigns.exists():
        outreach_active_campaigns = customer_campaigns.filter(status='active').count()
        # Aggregate across all campaigns for this customer
        for camp in customer_campaigns:
            sent = GeneratedEmail.objects.filter(
                prospect__campaign=camp, status__in=['sent', 'opened', 'replied']
            ).count()
            opened = GeneratedEmail.objects.filter(
                prospect__campaign=camp, status__in=['opened', 'replied']
            ).count()
            replied = GeneratedEmail.objects.filter(
                prospect__campaign=camp, status='replied'
            ).count()
            outreach_total_sent += sent
            outreach_total_opened += opened
            outreach_total_replied += replied

        # Recent replies the customer should see (last 30 days)
        recent_replies = list(
            GeneratedEmail.objects.filter(
                prospect__campaign__business=profile,
                status='replied',
                replied_at__isnull=False,
            )
            .select_related('prospect')
            .order_by('-replied_at')[:10]
        )

    outreach_open_rate = round(outreach_total_opened / outreach_total_sent * 100) if outreach_total_sent else 0
    outreach_reply_rate = round(outreach_total_replied / outreach_total_sent * 100) if outreach_total_sent else 0

    context = {
        'profile': profile,
        'hot_leads': hot_leads,
        'leads_this_week': leads_this_week,
        'response_rate': response_rate,
        'leads_won': leads_won,
        'recent_assignments': recent_assignments,
        'avg_response': avg_response,
        'best_platform': best_platform,
        'top_area': top_area,
        'conversion_rate': conversion_rate,
        'competitor_count': competitor_count,
        'competitor_neg': competitor_neg,
        'public_records_total': public_records_total,
        'public_records_breakdown': public_records_breakdown,
        'show_welcome': show_welcome,
        'source_count': source_count,
        'total_leads': total_leads,
        'nationwide_recent': nationwide_recent,
        'is_trial': is_trial,
        'trial_leads_remaining': profile.trial_leads_remaining if is_trial else 0,
        'trial_leads_used': trial_leads_used,
        'total_delivered': total_delivered,
        'total_contacted': total_contacted,
        'total_won': total_won,
        'total_revenue': total_revenue,
        'monthly_cost': monthly_cost,
        'roi_multiple': roi_multiple,
        # Outreach activity (managed-service view)
        'outreach_total_sent': outreach_total_sent,
        'outreach_total_opened': outreach_total_opened,
        'outreach_total_replied': outreach_total_replied,
        'outreach_open_rate': outreach_open_rate,
        'outreach_reply_rate': outreach_reply_rate,
        'outreach_active_campaigns': outreach_active_campaigns,
        'recent_replies': recent_replies,
    }
    return render(request, 'dashboard/home.html', context)
