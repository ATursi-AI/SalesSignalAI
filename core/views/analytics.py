"""
Analytics views for SalesSignal AI.
Provides Chart.js-ready JSON endpoints and the analytics dashboard page.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Avg, Q, F
from django.db.models.functions import TruncWeek, TruncMonth, TruncDate
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from core.models import Lead, LeadAssignment, TrackedCompetitor


@login_required
def analytics_dashboard(request):
    profile = request.user.business_profile
    assignments = LeadAssignment.objects.filter(business=profile)
    now = timezone.now()
    thirty_days = now - timedelta(days=30)

    # Top-level KPIs
    total_leads = assignments.count()
    total_won = assignments.filter(status='won').count()
    total_revenue = assignments.filter(status='won').aggregate(
        total=Sum('revenue'))['total'] or Decimal('0')
    conversion_rate = round(total_won / total_leads * 100, 1) if total_leads else 0

    # Avg response time (created_at → contacted_at) for contacted+ leads
    contacted = assignments.filter(
        contacted_at__isnull=False,
    ).annotate(
        response_hours=F('contacted_at') - F('created_at'),
    )
    avg_response_td = contacted.aggregate(avg=Avg('response_hours'))['avg']
    avg_response_hours = round(avg_response_td.total_seconds() / 3600, 1) if avg_response_td else None

    context = {
        'profile': profile,
        'total_leads': total_leads,
        'total_won': total_won,
        'total_revenue': total_revenue,
        'conversion_rate': conversion_rate,
        'avg_response_hours': avg_response_hours,
    }
    return render(request, 'analytics/dashboard.html', context)


@login_required
def analytics_lead_volume(request):
    """Weekly/monthly lead volume broken down by platform."""
    profile = request.user.business_profile
    period = request.GET.get('period', 'weekly')
    days = int(request.GET.get('days', 90))
    since = timezone.now() - timedelta(days=days)

    trunc_fn = TruncWeek if period == 'weekly' else TruncMonth

    qs = LeadAssignment.objects.filter(
        business=profile,
        created_at__gte=since,
    ).annotate(
        period=trunc_fn('created_at'),
    ).values('period', 'lead__platform').annotate(
        count=Count('id'),
    ).order_by('period')

    # Build {period: {platform: count}}
    data = defaultdict(lambda: defaultdict(int))
    platforms_seen = set()
    for row in qs:
        label = row['period'].strftime('%b %d' if period == 'weekly' else '%b %Y')
        plat = row['lead__platform']
        data[label][plat] = row['count']
        platforms_seen.add(plat)

    platforms = sorted(platforms_seen)
    labels = list(data.keys())

    datasets = []
    platform_colors = {
        'craigslist': '#7B2FBE', 'reddit': '#FF4500', 'patch': '#0EA5E9',
        'google_qna': '#4285F4', 'google_reviews': '#FBBC04', 'yelp_review': '#D32323',
        'houzz': '#4DBC5B', 'alignable': '#1B3A5C', 'facebook': '#1877F2',
        'nextdoor': '#8ED500', 'thumbtack': '#009FD9', 'porch': '#00BFA5',
        'twitter': '#1DA1F2', 'angi_review': '#39B54A', 'local_news': '#607D8B',
        'citydata': '#2D5F8A', 'biggerpockets': '#F57C00',
        'parent_community': '#E91E63', 'trade_forum': '#795548', 'manual': '#6B6B80',
    }

    for plat in platforms:
        datasets.append({
            'label': plat.replace('_', ' ').title(),
            'data': [data[l][plat] for l in labels],
            'backgroundColor': platform_colors.get(plat, '#3B82F6'),
        })

    return JsonResponse({'labels': labels, 'datasets': datasets})


@login_required
def analytics_funnel(request):
    """Lead conversion funnel: Detected → Alerted → Viewed → Contacted → Won."""
    profile = request.user.business_profile
    days = int(request.GET.get('days', 90))
    since = timezone.now() - timedelta(days=days)

    qs = LeadAssignment.objects.filter(business=profile, created_at__gte=since)

    detected = qs.count()
    alerted = qs.filter(alert_sent_at__isnull=False).count()
    viewed = qs.filter(status__in=['viewed', 'contacted', 'quoted', 'won', 'lost']).count()
    contacted = qs.filter(status__in=['contacted', 'quoted', 'won', 'lost']).count()
    won = qs.filter(status='won').count()

    return JsonResponse({
        'labels': ['Detected', 'Alerted', 'Viewed', 'Contacted', 'Won'],
        'data': [detected, alerted, viewed, contacted, won],
        'colors': ['#3B82F6', '#8B5CF6', '#F59E0B', '#10B981', '#FF4757'],
    })


@login_required
def analytics_revenue(request):
    """Monthly revenue from Won leads."""
    profile = request.user.business_profile
    days = int(request.GET.get('days', 365))
    since = timezone.now() - timedelta(days=days)

    qs = LeadAssignment.objects.filter(
        business=profile, status='won',
        revenue__isnull=False, updated_at__gte=since,
    ).annotate(
        month=TruncMonth('updated_at'),
    ).values('month').annotate(
        total=Sum('revenue'), count=Count('id'),
    ).order_by('month')

    labels = []
    revenue_data = []
    count_data = []
    for row in qs:
        labels.append(row['month'].strftime('%b %Y'))
        revenue_data.append(float(row['total']))
        count_data.append(row['count'])

    return JsonResponse({
        'labels': labels,
        'revenue': revenue_data,
        'count': count_data,
    })


@login_required
def analytics_platform_performance(request):
    """Platform comparison: leads, conversion rate, avg revenue."""
    profile = request.user.business_profile
    days = int(request.GET.get('days', 90))
    since = timezone.now() - timedelta(days=days)

    qs = LeadAssignment.objects.filter(
        business=profile, created_at__gte=since,
    ).values('lead__platform').annotate(
        total=Count('id'),
        won=Count('id', filter=Q(status='won')),
        revenue=Sum('revenue', filter=Q(status='won')),
    ).order_by('-total')

    platforms = []
    for row in qs:
        plat = row['lead__platform']
        total = row['total']
        won = row['won']
        platforms.append({
            'platform': plat.replace('_', ' ').title(),
            'platform_key': plat,
            'total': total,
            'won': won,
            'conversion': round(won / total * 100, 1) if total else 0,
            'revenue': float(row['revenue'] or 0),
        })

    return JsonResponse({'platforms': platforms})


@login_required
def analytics_response_time(request):
    """Response time vs win rate analysis."""
    profile = request.user.business_profile
    days = int(request.GET.get('days', 180))
    since = timezone.now() - timedelta(days=days)

    contacted = LeadAssignment.objects.filter(
        business=profile, created_at__gte=since,
        contacted_at__isnull=False,
    ).annotate(
        response_delta=F('contacted_at') - F('created_at'),
    )

    # Bucket by response time
    buckets = [
        ('< 1 hr', 0, 1),
        ('1-4 hrs', 1, 4),
        ('4-12 hrs', 4, 12),
        ('12-24 hrs', 12, 24),
        ('1-3 days', 24, 72),
        ('3+ days', 72, 99999),
    ]

    labels = []
    win_rates = []
    counts = []

    for label, min_h, max_h in buckets:
        min_td = timedelta(hours=min_h)
        max_td = timedelta(hours=max_h)
        bucket_qs = contacted.filter(
            response_delta__gte=min_td,
            response_delta__lt=max_td,
        )
        total = bucket_qs.count()
        won = bucket_qs.filter(status='won').count()
        labels.append(label)
        win_rates.append(round(won / total * 100, 1) if total else 0)
        counts.append(total)

    return JsonResponse({
        'labels': labels,
        'win_rates': win_rates,
        'counts': counts,
    })


@login_required
def analytics_territory(request):
    """Territory demand concentration data for heat map."""
    profile = request.user.business_profile
    days = int(request.GET.get('days', 90))
    since = timezone.now() - timedelta(days=days)

    qs = LeadAssignment.objects.filter(
        business=profile, created_at__gte=since,
        lead__detected_location__gt='',
    ).values('lead__detected_location', 'lead__detected_zip').annotate(
        total=Count('id'),
        won=Count('id', filter=Q(status='won')),
        revenue=Sum('revenue', filter=Q(status='won')),
    ).order_by('-total')[:20]

    locations = []
    for row in qs:
        locations.append({
            'location': row['lead__detected_location'],
            'zip': row['lead__detected_zip'] or '',
            'total': row['total'],
            'won': row['won'],
            'conversion': round(row['won'] / row['total'] * 100, 1) if row['total'] else 0,
            'revenue': float(row['revenue'] or 0),
        })

    return JsonResponse({'locations': locations})
