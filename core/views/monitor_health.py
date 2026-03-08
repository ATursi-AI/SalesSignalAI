"""
Monitor health dashboard showing status of all monitors,
email warming progress, and recent run history.
Restricted to staff/admin users.
"""
from collections import defaultdict
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Sum, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from core.models.monitoring import MonitorRun, EmailSendLog, Unsubscribe


@staff_member_required
def monitor_health_dashboard(request):
    """Main monitor health dashboard page."""
    now = timezone.now()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    # Get latest run per monitor
    monitors = {}
    all_names = (
        MonitorRun.objects.values_list('monitor_name', flat=True).distinct()
    )
    for name in all_names:
        latest = MonitorRun.objects.filter(monitor_name=name).first()
        week_runs = MonitorRun.objects.filter(
            monitor_name=name, started_at__gte=week_ago,
        )
        week_stats = week_runs.aggregate(
            total_runs=Count('id'),
            total_scraped=Sum('items_scraped'),
            total_leads=Sum('leads_created'),
            total_errors=Sum('errors'),
            failures=Count('id', filter=Q(status='failed')),
        )
        monitors[name] = {
            'latest': latest,
            'week': week_stats,
            'success_rate': (
                round((week_stats['total_runs'] - (week_stats['failures'] or 0))
                      / week_stats['total_runs'] * 100)
                if week_stats['total_runs'] else 0
            ),
        }

    # Email warming stats
    email_logs = EmailSendLog.objects.filter(date__gte=week_ago.date()).order_by('date')
    from core.utils.email_engine.warming import get_warming_day, get_daily_limit
    warming_day = get_warming_day()
    today_limit = get_daily_limit()

    # Unsubscribe count
    unsub_count = Unsubscribe.objects.count()
    unsub_recent = Unsubscribe.objects.filter(created_at__gte=week_ago).count()

    # Recent runs (last 30)
    recent_runs = MonitorRun.objects.all()[:30]

    context = {
        'monitors': monitors,
        'email_logs': email_logs,
        'warming_day': warming_day,
        'today_limit': today_limit,
        'unsub_count': unsub_count,
        'unsub_recent': unsub_recent,
        'recent_runs': recent_runs,
    }
    return render(request, 'monitor_health/dashboard.html', context)


@staff_member_required
def monitor_health_api(request):
    """JSON API for monitor health data (for AJAX refresh)."""
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    monitors = {}
    all_names = MonitorRun.objects.values_list('monitor_name', flat=True).distinct()
    for name in all_names:
        latest = MonitorRun.objects.filter(monitor_name=name).first()
        monitors[name] = {
            'status': latest.status if latest else 'unknown',
            'last_run': latest.started_at.isoformat() if latest else None,
            'duration': latest.duration_seconds if latest else None,
            'items_scraped': latest.items_scraped if latest else 0,
            'leads_created': latest.leads_created if latest else 0,
            'errors': latest.errors if latest else 0,
            'error_message': latest.error_message if latest else '',
        }

    return JsonResponse({'monitors': monitors})
