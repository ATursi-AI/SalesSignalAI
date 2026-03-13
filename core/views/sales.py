"""
Salesperson views — personal pipeline, prospects, activity logging, daily calls, stats.
Accessible by staff users who have a SalesPerson profile at /sales/.
"""
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Q, Avg
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone

from core.models.sales import SalesPerson, SalesProspect, SalesActivity
from core.models.business import BusinessProfile


def _get_sp(request):
    """Return the SalesPerson for the logged-in user, or None."""
    return getattr(request.user, 'salesperson_profile', None)


def salesperson_required(view):
    """Decorator: user must be logged in AND have a SalesPerson profile."""
    from functools import wraps

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        sp = _get_sp(request)
        if not sp:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('You do not have a salesperson profile.')
        request.salesperson = sp
        return view(request, *args, **kwargs)
    return wrapper


@salesperson_required
def pipeline(request):
    """Kanban board for the salesperson's pipeline."""
    sp = request.salesperson
    stages = SalesProspect.PIPELINE_CHOICES
    columns = []
    for code, label in stages:
        prospects = sp.prospects.filter(pipeline_stage=code).order_by('-updated_at')
        columns.append({'code': code, 'label': label, 'prospects': prospects})

    return render(request, 'sales/pipeline.html', {
        'columns': columns,
        'sp': sp,
    })


@salesperson_required
def pipeline_move(request):
    """AJAX: move a prospect to a new pipeline stage."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    sp = request.salesperson
    prospect_id = request.POST.get('prospect_id')
    new_stage = request.POST.get('stage')
    prospect = get_object_or_404(SalesProspect, id=prospect_id, salesperson=sp)
    old_stage = prospect.pipeline_stage
    prospect.pipeline_stage = new_stage
    prospect.save(update_fields=['pipeline_stage', 'updated_at'])

    # Auto-log stage change
    SalesActivity.objects.create(
        prospect=prospect, salesperson=sp, activity_type='note',
        description=f'Moved from {old_stage} → {new_stage}',
    )
    return JsonResponse({'success': True})


@salesperson_required
def prospects(request):
    """Full prospect table with search and filters."""
    sp = request.salesperson

    # Handle add prospect POST
    if request.method == 'POST' and request.POST.get('action') == 'add_prospect':
        new = SalesProspect.objects.create(
            salesperson=sp,
            business_name=request.POST.get('business_name', ''),
            owner_name=request.POST.get('owner_name', ''),
            phone=request.POST.get('phone', ''),
            email=request.POST.get('email', ''),
            service_category=request.POST.get('service_category', ''),
            city=request.POST.get('city', ''),
            state=request.POST.get('state', ''),
            source='manual_entry',
        )
        return JsonResponse({'success': True, 'id': new.id})

    qs = sp.prospects.all()

    stage = request.GET.get('stage', '')
    search = request.GET.get('q', '')
    source = request.GET.get('source', '')

    if stage:
        qs = qs.filter(pipeline_stage=stage)
    if source:
        qs = qs.filter(source=source)
    if search:
        qs = qs.filter(
            Q(business_name__icontains=search) |
            Q(owner_name__icontains=search) |
            Q(phone__icontains=search) |
            Q(city__icontains=search)
        )

    return render(request, 'sales/prospects.html', {
        'prospects': qs[:200],
        'sp': sp,
        'stages': SalesProspect.PIPELINE_CHOICES,
        'sources': SalesProspect.SOURCE_CHOICES,
        'current_stage': stage,
        'current_source': source,
        'search_q': search,
    })


@salesperson_required
def prospect_detail(request, prospect_id):
    """Single prospect: info, activity timeline, action buttons."""
    sp = request.salesperson
    prospect = get_object_or_404(SalesProspect, id=prospect_id, salesperson=sp)

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'log_activity':
            activity_type = request.POST.get('activity_type', 'note')
            description = request.POST.get('description', '')
            outcome = request.POST.get('outcome', '')
            duration = request.POST.get('call_duration')

            SalesActivity.objects.create(
                prospect=prospect, salesperson=sp,
                activity_type=activity_type,
                description=description,
                outcome=outcome,
                call_duration=int(duration) if duration else None,
            )

            # Auto-advance pipeline if appropriate
            if activity_type == 'call' and prospect.pipeline_stage == 'new':
                prospect.pipeline_stage = 'contacted'
                prospect.save(update_fields=['pipeline_stage', 'updated_at'])
            elif activity_type == 'demo' and prospect.pipeline_stage in ('contacted', 'callback'):
                prospect.pipeline_stage = 'demo_scheduled'
                prospect.save(update_fields=['pipeline_stage', 'updated_at'])

            return JsonResponse({'success': True})

        elif action == 'schedule_followup':
            followup_date = request.POST.get('followup_date')
            if followup_date:
                prospect.next_follow_up_date = followup_date
                prospect.save(update_fields=['next_follow_up_date'])
                SalesActivity.objects.create(
                    prospect=prospect, salesperson=sp,
                    activity_type='follow_up',
                    description=f'Follow-up scheduled for {followup_date}',
                )
            return JsonResponse({'success': True})

        elif action == 'mark_won':
            monthly_value = request.POST.get('monthly_value', '')
            prospect.pipeline_stage = 'closed_won'
            if monthly_value:
                prospect.estimated_monthly_value = monthly_value
            prospect.save(update_fields=['pipeline_stage', 'estimated_monthly_value', 'updated_at'])
            SalesActivity.objects.create(
                prospect=prospect, salesperson=sp,
                activity_type='closed_won',
                description=f'Deal closed! MRR: ${monthly_value or "TBD"}',
            )
            return JsonResponse({'success': True})

        elif action == 'mark_lost':
            lost_reason = request.POST.get('lost_reason', 'other')
            prospect.pipeline_stage = 'closed_lost'
            prospect.lost_reason = lost_reason
            prospect.save(update_fields=['pipeline_stage', 'lost_reason', 'updated_at'])
            SalesActivity.objects.create(
                prospect=prospect, salesperson=sp,
                activity_type='closed_lost',
                description=f'Lost: {prospect.get_lost_reason_display()}',
            )
            return JsonResponse({'success': True})

        elif action == 'update_info':
            prospect.owner_name = request.POST.get('owner_name', prospect.owner_name)
            prospect.phone = request.POST.get('phone', prospect.phone)
            prospect.email = request.POST.get('email', prospect.email)
            prospect.website = request.POST.get('website', prospect.website)
            prospect.notes = request.POST.get('notes', prospect.notes)
            prospect.estimated_monthly_value = request.POST.get('monthly_value') or prospect.estimated_monthly_value
            prospect.save()
            return JsonResponse({'success': True})

        elif action == 'add_prospect':
            new = SalesProspect.objects.create(
                salesperson=sp,
                business_name=request.POST.get('business_name', ''),
                owner_name=request.POST.get('owner_name', ''),
                phone=request.POST.get('phone', ''),
                email=request.POST.get('email', ''),
                service_category=request.POST.get('service_category', ''),
                city=request.POST.get('city', ''),
                state=request.POST.get('state', ''),
                source='manual_entry',
            )
            return JsonResponse({'success': True, 'id': new.id})

    activities = prospect.activities.all()[:50]

    return render(request, 'sales/prospect_detail.html', {
        'prospect': prospect,
        'activities': activities,
        'sp': sp,
        'stages': SalesProspect.PIPELINE_CHOICES,
        'lost_reasons': SalesProspect.LOST_REASON_CHOICES,
        'activity_types': SalesActivity.TYPE_CHOICES,
        'outcome_choices': SalesActivity.OUTCOME_CHOICES,
    })


@salesperson_required
def today_calls(request):
    """Today's call sheet: overdue follow-ups + today's follow-ups + call goal."""
    sp = request.salesperson
    today = date.today()

    overdue = sp.prospects.filter(
        next_follow_up_date__lt=today,
    ).exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).order_by('next_follow_up_date')

    todays = sp.prospects.filter(
        next_follow_up_date=today,
    ).exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).order_by('business_name')

    new_prospects = sp.prospects.filter(
        pipeline_stage='new',
    ).order_by('-created_at')[:20]

    calls_today = sp.activities.filter(
        activity_type='call', created_at__date=today,
    ).count()

    return render(request, 'sales/today.html', {
        'overdue': overdue,
        'todays': todays,
        'new_prospects': new_prospects,
        'calls_today': calls_today,
        'call_goal': sp.daily_call_goal,
        'sp': sp,
    })


@salesperson_required
def stats(request):
    """Personal stats: calls, demos, deals, conversion, leaderboard."""
    sp = request.salesperson
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    calls_today = sp.activities.filter(activity_type='call', created_at__date=today).count()
    calls_week = sp.activities.filter(activity_type='call', created_at__date__gte=week_start).count()
    calls_month = sp.activities.filter(activity_type='call', created_at__date__gte=month_start).count()
    demos_week = sp.activities.filter(activity_type='demo', created_at__date__gte=week_start).count()
    demos_month = sp.activities.filter(activity_type='demo', created_at__date__gte=month_start).count()
    deals_month = sp.prospects.filter(pipeline_stage='closed_won', updated_at__date__gte=month_start).count()
    mrr_month = sp.prospects.filter(
        pipeline_stage='closed_won', updated_at__date__gte=month_start,
        estimated_monthly_value__isnull=False,
    ).aggregate(total=Sum('estimated_monthly_value'))['total'] or 0

    total_won = sp.prospects.filter(pipeline_stage='closed_won').count()
    total_closed = sp.prospects.filter(pipeline_stage__in=['closed_won', 'closed_lost']).count()
    conversion_rate = round(total_won / total_closed * 100, 1) if total_closed else 0

    active_pipeline = sp.prospects.exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).count()
    pipeline_value = sp.prospects.exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).filter(
        estimated_monthly_value__isnull=False,
    ).aggregate(total=Sum('estimated_monthly_value'))['total'] or 0

    # Leaderboard: all active salespeople this month
    leaderboard = []
    for person in SalesPerson.objects.filter(status='active'):
        deals = person.prospects.filter(
            pipeline_stage='closed_won', updated_at__date__gte=month_start,
        ).count()
        calls = person.activities.filter(
            activity_type='call', created_at__date__gte=month_start,
        ).count()
        leaderboard.append({
            'name': person.user.get_full_name() or person.user.username,
            'deals': deals,
            'calls': calls,
            'is_me': person.id == sp.id,
        })
    leaderboard.sort(key=lambda x: (-x['deals'], -x['calls']))

    return render(request, 'sales/stats.html', {
        'sp': sp,
        'calls_today': calls_today,
        'calls_week': calls_week,
        'calls_month': calls_month,
        'demos_week': demos_week,
        'demos_month': demos_month,
        'deals_month': deals_month,
        'mrr_month': mrr_month,
        'conversion_rate': conversion_rate,
        'active_pipeline': active_pipeline,
        'pipeline_value': pipeline_value,
        'leaderboard': leaderboard,
        'call_goal': sp.daily_call_goal,
    })
