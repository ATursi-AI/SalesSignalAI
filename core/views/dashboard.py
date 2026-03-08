from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F
from django.utils import timezone
from datetime import timedelta
from core.models import LeadAssignment, Lead, TrackedCompetitor, CompetitorReview


@login_required
def dashboard_home(request):
    profile = request.user.business_profile
    if not profile.onboarding_complete:
        from django.shortcuts import redirect
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
    }
    return render(request, 'dashboard/home.html', context)
