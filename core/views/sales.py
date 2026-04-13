"""
Salesperson views — personal pipeline, prospects, activity logging, daily calls, stats.
Accessible at /sales/ by:
  - Superusers/admins: see ALL prospects across ALL salespeople (no SalesPerson profile needed)
  - Staff with a SalesPerson profile: see only their own prospects
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Q, Avg
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone

from core.models.sales import SalesPerson, SalesProspect, SalesActivity, EmailTemplate, CallScript
from core.models.sales_sequences import SalesSequence, SequenceEnrollment
from core.models.business import BusinessProfile
from core.models.leads import Lead


def _get_sp(request):
    """Return the SalesPerson for the logged-in user, or None."""
    return getattr(request.user, 'salesperson_profile', None)


def sales_access_required(view):
    """Decorator: user must be logged in AND be either a superuser or have a SalesPerson profile."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        sp = _get_sp(request)
        request.salesperson = sp  # may be None for admins
        request.is_sales_admin = request.user.is_superuser
        if not sp and not request.user.is_superuser:
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapper


def _prospect_qs(request):
    """Return the base prospect queryset: all for admins, own for salespeople."""
    if request.is_sales_admin:
        return SalesProspect.objects.all()
    return request.salesperson.prospects.all()


def _activity_qs(request):
    """Return the base activity queryset: all for admins, own for salespeople."""
    if request.is_sales_admin:
        return SalesActivity.objects.all()
    return request.salesperson.activities.all()


@sales_access_required
def pipeline(request):
    """Kanban board — all prospects for admin, own for salesperson."""
    sp = request.salesperson
    base_qs = _prospect_qs(request)
    stages = SalesProspect.PIPELINE_CHOICES
    columns = []
    for code, label in stages:
        prospects = base_qs.filter(pipeline_stage=code).select_related('salesperson__user').order_by('-updated_at')
        columns.append({'code': code, 'label': label, 'prospects': prospects})

    return render(request, 'sales/pipeline.html', {
        'columns': columns,
        'sp': sp,
        'is_sales_admin': request.is_sales_admin,
        'today': date.today(),
    })


@sales_access_required
def pipeline_move(request):
    """AJAX: move a prospect to a new pipeline stage."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    sp = request.salesperson
    prospect_id = request.POST.get('prospect_id')
    new_stage = request.POST.get('stage')

    if request.is_sales_admin:
        prospect = get_object_or_404(SalesProspect, id=prospect_id)
    else:
        prospect = get_object_or_404(SalesProspect, id=prospect_id, salesperson=sp)

    old_stage = prospect.pipeline_stage
    prospect.pipeline_stage = new_stage
    prospect.save(update_fields=['pipeline_stage', 'updated_at'])

    # Auto-log stage change — attribute to the prospect's salesperson for admins
    log_sp = sp or prospect.salesperson
    SalesActivity.objects.create(
        prospect=prospect, salesperson=log_sp, activity_type='note',
        description=f'Moved from {old_stage} → {new_stage}',
    )

    # Trigger workflow automation
    try:
        from core.services.workflow_engine import trigger_workflow
        trigger_workflow('prospect_stage_changed', {
            'model': 'SalesProspect',
            'prospect_id': prospect.id,
            'id': prospect.id,
            'from_stage': old_stage,
            'to_stage': new_stage,
            'business_name': prospect.business_name,
            'phone': prospect.phone,
            'email': prospect.email,
        })
    except Exception:
        pass  # Don't break the stage change if workflow fails

    return JsonResponse({'success': True})


@sales_access_required
def prospects(request):
    """Full prospect table with search and filters."""
    sp = request.salesperson

    # Handle add prospect POST
    if request.method == 'POST' and request.POST.get('action') == 'add_prospect':
        # Admin must pick a salesperson; salesperson defaults to self
        if request.is_sales_admin:
            sp_id = request.POST.get('salesperson_id')
            assign_sp = get_object_or_404(SalesPerson, id=sp_id) if sp_id else sp
            if not assign_sp:
                return JsonResponse({'error': 'Select a salesperson'}, status=400)
        else:
            assign_sp = sp
        new = SalesProspect.objects.create(
            salesperson=assign_sp,
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

    qs = _prospect_qs(request).select_related('salesperson__user')

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
        'is_sales_admin': request.is_sales_admin,
        'salespeople': SalesPerson.objects.filter(status='active') if request.is_sales_admin else None,
        'stages': SalesProspect.PIPELINE_CHOICES,
        'sources': SalesProspect.SOURCE_CHOICES,
        'current_stage': stage,
        'current_source': source,
        'search_q': search,
    })


@sales_access_required
def prospect_detail(request, prospect_id):
    """Single prospect: info, activity timeline, action buttons."""
    sp = request.salesperson

    if request.is_sales_admin:
        prospect = get_object_or_404(SalesProspect, id=prospect_id)
    else:
        prospect = get_object_or_404(SalesProspect, id=prospect_id, salesperson=sp)

    # For activity logging, attribute to prospect's salesperson if admin has no profile
    log_sp = sp or prospect.salesperson

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'log_activity':
            activity_type = request.POST.get('activity_type', 'note')
            description = request.POST.get('description', '')
            outcome = request.POST.get('outcome', '')
            duration = request.POST.get('call_duration')

            SalesActivity.objects.create(
                prospect=prospect, salesperson=log_sp,
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
            followup_time = request.POST.get('followup_time', '').strip()
            followup_type = request.POST.get('followup_type', 'follow_up')
            followup_notes = request.POST.get('followup_notes', '').strip()

            if followup_date:
                prospect.next_follow_up_date = followup_date
                prospect.save(update_fields=['next_follow_up_date'])

                # Build description
                type_labels = {
                    'follow_up': 'Follow-up call',
                    'demo': 'Demo',
                    'meeting': 'Meeting',
                    'callback': 'Callback',
                }
                type_label = type_labels.get(followup_type, 'Follow-up')
                time_str = ''
                if followup_time:
                    try:
                        from datetime import datetime as dt
                        t = dt.strptime(followup_time, '%H:%M').strftime('%I:%M %p')
                        time_str = f' at {t}'
                    except ValueError:
                        time_str = f' at {followup_time}'

                desc = f'{type_label} scheduled for {followup_date}{time_str}'
                if followup_notes:
                    desc += f'\n{followup_notes}'

                # Map followup_type to activity_type
                activity_type_map = {
                    'follow_up': 'follow_up',
                    'demo': 'demo',
                    'meeting': 'demo',
                    'callback': 'follow_up',
                }
                activity_type = activity_type_map.get(followup_type, 'follow_up')

                # Create as a task so it shows on dashboard "Your Day"
                SalesActivity.objects.create(
                    prospect=prospect, salesperson=log_sp,
                    activity_type=activity_type,
                    description=desc,
                    is_task=True,
                    task_due_date=followup_date,
                )

                # Auto-advance pipeline for demos
                if followup_type == 'demo' and prospect.pipeline_stage in ('new', 'contacted', 'callback'):
                    prospect.pipeline_stage = 'demo_scheduled'
                    prospect.save(update_fields=['pipeline_stage', 'updated_at'])

            return JsonResponse({'success': True})

        elif action == 'mark_won':
            monthly_value = request.POST.get('monthly_value', '')
            prospect.pipeline_stage = 'closed_won'
            if monthly_value:
                prospect.estimated_monthly_value = monthly_value
            prospect.save(update_fields=['pipeline_stage', 'estimated_monthly_value', 'updated_at'])
            SalesActivity.objects.create(
                prospect=prospect, salesperson=log_sp,
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
                prospect=prospect, salesperson=log_sp,
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

    activities = prospect.activities.select_related('salesperson__user').all()[:50]

    return render(request, 'sales/prospect_detail.html', {
        'prospect': prospect,
        'activities': activities,
        'sp': sp,
        'is_sales_admin': request.is_sales_admin,
        'stages': SalesProspect.PIPELINE_CHOICES,
        'lost_reasons': SalesProspect.LOST_REASON_CHOICES,
        'activity_types': SalesActivity.TYPE_CHOICES,
        'outcome_choices': SalesActivity.OUTCOME_CHOICES,
    })


@sales_access_required
def today_calls(request):
    """Today's call sheet: overdue follow-ups + today's follow-ups + call goal."""
    sp = request.salesperson
    today = date.today()
    base_qs = _prospect_qs(request).select_related('salesperson__user')
    act_qs = _activity_qs(request)

    overdue = base_qs.filter(
        next_follow_up_date__lt=today,
    ).exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).order_by('next_follow_up_date')

    todays = base_qs.filter(
        next_follow_up_date=today,
    ).exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).order_by('business_name')

    new_prospects = base_qs.filter(
        pipeline_stage='new',
    ).order_by('-created_at')[:20]

    calls_today = act_qs.filter(
        activity_type='call', created_at__date=today,
    ).count()

    call_goal = sp.daily_call_goal if sp else 0

    return render(request, 'sales/today.html', {
        'overdue': overdue,
        'todays': todays,
        'new_prospects': new_prospects,
        'calls_today': calls_today,
        'call_goal': call_goal,
        'sp': sp,
        'is_sales_admin': request.is_sales_admin,
    })


@sales_access_required
def stats(request):
    """Personal stats (salesperson) or team-wide stats (admin). Plus leaderboard."""
    sp = request.salesperson
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    act_qs = _activity_qs(request)
    prospect_qs = _prospect_qs(request)

    calls_today = act_qs.filter(activity_type='call', created_at__date=today).count()
    calls_week = act_qs.filter(activity_type='call', created_at__date__gte=week_start).count()
    calls_month = act_qs.filter(activity_type='call', created_at__date__gte=month_start).count()
    demos_week = act_qs.filter(activity_type='demo', created_at__date__gte=week_start).count()
    demos_month = act_qs.filter(activity_type='demo', created_at__date__gte=month_start).count()
    deals_month = prospect_qs.filter(pipeline_stage='closed_won', updated_at__date__gte=month_start).count()
    mrr_month = prospect_qs.filter(
        pipeline_stage='closed_won', updated_at__date__gte=month_start,
        estimated_monthly_value__isnull=False,
    ).aggregate(total=Sum('estimated_monthly_value'))['total'] or 0

    total_won = prospect_qs.filter(pipeline_stage='closed_won').count()
    total_closed = prospect_qs.filter(pipeline_stage__in=['closed_won', 'closed_lost']).count()
    conversion_rate = round(total_won / total_closed * 100, 1) if total_closed else 0

    active_pipeline = prospect_qs.exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).count()
    pipeline_value = prospect_qs.exclude(
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
            'is_me': sp and person.id == sp.id,
        })
    leaderboard.sort(key=lambda x: (-x['deals'], -x['calls']))

    return render(request, 'sales/stats.html', {
        'sp': sp,
        'is_sales_admin': request.is_sales_admin,
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
        'call_goal': sp.daily_call_goal if sp else 0,
    })


# -------------------------------------------------------------------
# Sales Calendar
# -------------------------------------------------------------------

@sales_access_required
def sales_calendar(request):
    """Calendar view — day/week/month with follow-ups and activities."""
    view_mode = request.GET.get('view', 'week')
    date_str = request.GET.get('date', '')
    today = date.today()

    try:
        current_date = date.fromisoformat(date_str) if date_str else today
    except ValueError:
        current_date = today

    # Compute date range
    if view_mode == 'day':
        start_date = current_date
        end_date = current_date
        prev_date = current_date - timedelta(days=1)
        next_date = current_date + timedelta(days=1)
    elif view_mode == 'month':
        start_date = current_date.replace(day=1)
        _, last_day = calendar.monthrange(current_date.year, current_date.month)
        end_date = current_date.replace(day=last_day)
        prev_date = (start_date - timedelta(days=1)).replace(day=1)
        next_date = (end_date + timedelta(days=1))
        # Extend to full weeks for grid
        while start_date.weekday() != 0:  # Monday
            start_date -= timedelta(days=1)
        while end_date.weekday() != 6:  # Sunday
            end_date += timedelta(days=1)
    else:  # week
        start_date = current_date - timedelta(days=current_date.weekday())  # Monday
        end_date = start_date + timedelta(days=6)  # Sunday
        prev_date = start_date - timedelta(days=7)
        next_date = start_date + timedelta(days=7)

    date_range = []
    d = start_date
    while d <= end_date:
        date_range.append(d)
        d += timedelta(days=1)

    # Query prospects with follow-ups in range
    prospects = _prospect_qs(request).filter(
        next_follow_up_date__gte=start_date,
        next_follow_up_date__lte=end_date,
    ).select_related('salesperson__user').order_by('next_follow_up_date')

    # Query activities in range
    activities = _activity_qs(request).filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).select_related('prospect', 'salesperson__user').order_by('created_at')

    # Group by date
    prospects_by_date = defaultdict(list)
    for p in prospects:
        key = p.next_follow_up_date.isoformat()
        prospects_by_date[key].append(p)

    activities_by_date = defaultdict(list)
    for a in activities:
        key = a.created_at.date().isoformat()
        activities_by_date[key].append(a)

    # Overdue count
    overdue_count = _prospect_qs(request).filter(
        next_follow_up_date__lt=today,
        pipeline_stage__in=['new', 'contacted', 'callback', 'demo_scheduled', 'demo_completed', 'proposal_sent'],
    ).count()

    # Month grid: split into weeks
    weeks = []
    if view_mode == 'month':
        for i in range(0, len(date_range), 7):
            weeks.append(date_range[i:i + 7])

    return render(request, 'sales/calendar.html', {
        'current_view': view_mode,
        'current_date': current_date,
        'today': today,
        'date_range': date_range,
        'weeks': weeks,
        'prospects_by_date': dict(prospects_by_date),
        'activities_by_date': dict(activities_by_date),
        'overdue_count': overdue_count,
        'prev_date': prev_date if view_mode != 'month' else (current_date.replace(day=1) - timedelta(days=1)).replace(day=1),
        'next_date': next_date if view_mode != 'month' else (current_date.replace(day=28) + timedelta(days=4)).replace(day=1),
        'pipeline_choices': dict(SalesProspect.PIPELINE_CHOICES),
    })


@sales_access_required
def calendar_reschedule(request, prospect_id):
    """AJAX: reschedule a prospect's follow-up date."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    import json
    data = json.loads(request.body)
    new_date = data.get('date', '')

    prospect = get_object_or_404(SalesProspect, pk=prospect_id)
    if not request.is_sales_admin and request.salesperson != prospect.salesperson:
        return JsonResponse({'error': 'Access denied'}, status=403)

    try:
        prospect.next_follow_up_date = date.fromisoformat(new_date) if new_date else None
        prospect.save(update_fields=['next_follow_up_date'])
        return JsonResponse({'ok': True})
    except ValueError:
        return JsonResponse({'error': 'Invalid date'}, status=400)


# ─── Sales Dashboard ─────────────────────────────────────────────

@sales_access_required
def sales_dashboard(request):
    """Salesperson home dashboard with metrics, tasks, and activity feed."""
    sp = request.salesperson
    today = date.today()
    now = timezone.now()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    if request.is_sales_admin:
        prospects = SalesProspect.objects.all()
        activities = SalesActivity.objects.all()
    else:
        prospects = SalesProspect.objects.filter(salesperson=sp)
        activities = SalesActivity.objects.filter(salesperson=sp)

    active_prospects = prospects.exclude(pipeline_stage__in=['closed_won', 'closed_lost'])

    # Metrics
    calls_today = activities.filter(
        activity_type='call', created_at__date=today,
    ).count()
    call_goal = sp.daily_call_goal if sp else 40

    tasks_due = activities.filter(
        is_task=True, task_completed=False,
        task_due_date__lte=today,
    ).count()

    pipeline_value = active_prospects.aggregate(
        total=Sum('estimated_monthly_value'),
    )['total'] or 0

    meetings_week = activities.filter(
        activity_type='demo',
        created_at__date__gte=week_start,
        created_at__date__lte=week_end,
    ).count()

    # Your Day — overdue + today follow-ups + today tasks
    overdue_followups = active_prospects.filter(
        next_follow_up_date__lt=today,
    ).order_by('next_follow_up_date')[:10]

    today_followups = active_prospects.filter(
        next_follow_up_date=today,
    ).order_by('business_name')[:10]

    today_tasks = activities.filter(
        is_task=True, task_completed=False, task_due_date=today,
    ).select_related('prospect')[:10]

    overdue_tasks = activities.filter(
        is_task=True, task_completed=False, task_due_date__lt=today,
    ).select_related('prospect')[:10]

    # Recent activity
    recent_activities = activities.select_related('prospect').order_by('-created_at')[:10]

    # Pipeline summary — build list of (label, count) for template
    raw_counts = dict(
        active_prospects.values_list('pipeline_stage')
        .annotate(c=Count('id'))
        .values_list('pipeline_stage', 'c')
    )
    pipeline_summary = [
        (label, raw_counts.get(key, 0))
        for key, label in SalesProspect.PIPELINE_CHOICES
        if key not in ('closed_won', 'closed_lost')
    ]

    # ── Sequence data ──────────────────────────────────────────
    # Active sequences with enrollment counts
    active_sequences = SalesSequence.objects.filter(status='active').annotate(
        active_count=Count('enrollments', filter=Q(enrollments__status='active')),
        replied_count=Count('enrollments', filter=Q(enrollments__status='replied')),
    )

    # Sequence call tasks due today (created by sequence runner)
    sequence_call_tasks = activities.filter(
        is_task=True, task_completed=False,
        task_due_date__lte=today,
        sequence_step_logs__isnull=False,
    ).select_related('prospect').distinct()[:20]

    # Enrollments with actions due today
    if request.is_sales_admin:
        due_enrollments = SequenceEnrollment.objects.filter(
            status='active', next_action_date__lte=today,
        )
    else:
        due_enrollments = SequenceEnrollment.objects.filter(
            status='active', next_action_date__lte=today,
            prospect__salesperson=sp,
        )
    due_enrollment_count = due_enrollments.count()

    # Recent sequence replies
    recent_replies = SequenceEnrollment.objects.filter(
        status='replied',
    ).select_related('prospect', 'sequence').order_by('-replied_at')[:5]

    context = {
        'calls_today': calls_today,
        'call_goal': call_goal,
        'call_pct': min(100, round(calls_today / call_goal * 100)) if call_goal else 0,
        'tasks_due': tasks_due,
        'pipeline_value': pipeline_value,
        'meetings_week': meetings_week,
        'overdue_followups': overdue_followups,
        'today_followups': today_followups,
        'today_tasks': today_tasks,
        'overdue_tasks': overdue_tasks,
        'recent_activities': recent_activities,
        'pipeline_summary': pipeline_summary,
        # Sequence data
        'active_sequences': active_sequences,
        'sequence_call_tasks': sequence_call_tasks,
        'due_enrollment_count': due_enrollment_count,
        'recent_replies': recent_replies,
    }
    return render(request, 'sales/dashboard.html', context)


@sales_access_required
def quick_log(request):
    """AJAX: Quick-log a call/email/note from the dashboard."""
    import json
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    sp = request.salesperson
    data = json.loads(request.body)
    prospect_id = data.get('prospect_id')
    activity_type = data.get('type', 'call')
    description = data.get('description', '')
    outcome = data.get('outcome', '')

    if request.is_sales_admin:
        prospect = get_object_or_404(SalesProspect, id=prospect_id)
    else:
        prospect = get_object_or_404(SalesProspect, id=prospect_id, salesperson=sp)

    log_sp = sp or prospect.salesperson
    activity = SalesActivity.objects.create(
        prospect=prospect,
        salesperson=log_sp,
        activity_type=activity_type,
        description=description,
        outcome=outcome,
    )

    return JsonResponse({'ok': True, 'id': activity.id})


@sales_access_required
def complete_task(request):
    """AJAX: Mark a task as complete."""
    import json
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    task_id = data.get('task_id')

    activity = get_object_or_404(SalesActivity, id=task_id, is_task=True)
    activity.task_completed = True
    activity.task_completed_at = timezone.now()
    activity.save(update_fields=['task_completed', 'task_completed_at'])

    return JsonResponse({'ok': True})


# ─── Email Templates + Compose ─────────────────────────────────────

@sales_access_required
def get_email_templates(request):
    """Return all email templates as JSON."""
    templates = EmailTemplate.objects.all().order_by('category', 'name')
    data = [{
        'id': t.id, 'name': t.name, 'category': t.category,
        'subject': t.subject, 'body': t.body,
    } for t in templates]
    return JsonResponse({'templates': data})


@sales_access_required
def send_prospect_email(request, prospect_id):
    """Send an email to a prospect from the detail page."""
    import json
    from django.core.mail import send_mail as django_send_mail

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    sp = _get_salesperson(request)
    if request.user.is_superuser:
        prospect = get_object_or_404(SalesProspect, id=prospect_id)
    else:
        prospect = get_object_or_404(SalesProspect, id=prospect_id, salesperson=sp)

    data = json.loads(request.body)
    to_email = data.get('to', prospect.email or '').strip()
    subject = data.get('subject', '').strip()
    body = data.get('body', '').strip()

    if not to_email or not subject or not body:
        return JsonResponse({'ok': False, 'error': 'Email, subject, and body are required.'})

    # Replace template variables
    rep_name = request.user.get_full_name() or request.user.username
    rep_phone = sp.phone if sp else ''
    replacements = {
        '{{business_name}}': prospect.business_name or '',
        '{{contact_name}}': prospect.contact_name or prospect.business_name or '',
        '{{your_name}}': rep_name,
        '{{your_phone}}': rep_phone,
        '{{your_company}}': 'SalesSignal AI',
    }
    for k, v in replacements.items():
        subject = subject.replace(k, v)
        body = body.replace(k, v)

    try:
        django_send_mail(
            subject, body,
            'support@salessignalai.com', [to_email],
            fail_silently=False,
        )
        SalesActivity.objects.create(
            prospect=prospect,
            salesperson=sp or prospect.salesperson,
            activity_type='email',
            description=f'Email sent: {subject}\nTo: {to_email}',
        )
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})


# ─── Call Scripts ───────────────────────────────────────────────────

@sales_access_required
def get_call_scripts(request):
    """Return all active call scripts as JSON."""
    script_type = request.GET.get('type', '')
    qs = CallScript.objects.filter(is_active=True)
    if script_type:
        qs = qs.filter(script_type=script_type)
    data = [{
        'id': s.id, 'name': s.name, 'script_type': s.script_type,
        'opening': s.opening, 'talking_points': s.talking_points,
        'qualification_questions': s.qualification_questions,
        'objection_handlers': s.objection_handlers,
        'closing': s.closing,
    } for s in qs]
    return JsonResponse({'scripts': data})


@sales_access_required
def get_prospect_script(request, prospect_id):
    """Get the appropriate call script for a specific prospect, with variables replaced."""
    sp = _get_salesperson(request)
    if request.user.is_superuser:
        prospect = get_object_or_404(SalesProspect, id=prospect_id)
    else:
        prospect = get_object_or_404(SalesProspect, id=prospect_id, salesperson=sp)

    source_map = {
        'violation': 'violation',
        'property_sale': 'property_sale',
        'no_website': 'no_website',
        'health_inspection': 'health_inspection',
        'business_filing': 'business_filing',
        'google_maps_scan': 'no_website',
    }
    script_type = source_map.get(prospect.source or '', 'general')

    script = CallScript.objects.filter(script_type=script_type, is_active=True).first()
    if not script:
        script = CallScript.objects.filter(script_type='general', is_active=True).first()
    if not script:
        return JsonResponse({'found': False})

    def rep(text):
        return (text or '').replace(
            '[CONTACT NAME]', prospect.contact_name or prospect.business_name or ''
        ).replace(
            '[BUSINESS NAME]', prospect.business_name or ''
        ).replace(
            '[BUSINESS/PROPERTY]', prospect.business_name or ''
        ).replace(
            '[ADDRESS]', prospect.address or ''
        ).replace(
            '[YOUR NAME]', request.user.get_full_name() or request.user.username
        ).replace(
            '[AREA]', prospect.city or ''
        ).replace(
            '[TIMEFRAME]', 'recently'
        )

    return JsonResponse({
        'found': True,
        'name': script.name,
        'type': script.script_type,
        'opening': rep(script.opening),
        'talking_points': [rep(tp) for tp in (script.talking_points or [])],
        'qualification_questions': script.qualification_questions or [],
        'objection_handlers': {k: rep(v) for k, v in (script.objection_handlers or {}).items()},
        'closing': rep(script.closing),
    })


# ─── High-Value Leads ─────────────────────────────────────────────

@sales_access_required
def high_value_leads(request):
    """High-value leads ($5K+) flagged for immediate sales review."""
    from core.utils.reach.lead_value import flag_high_value_leads, extract_lead_value

    threshold = int(request.GET.get('threshold', 5000))
    source_type = request.GET.get('source_type', '')
    days = int(request.GET.get('days', 90))

    cutoff = timezone.now() - timedelta(days=days)
    qs = Lead.objects.filter(
        raw_data__isnull=False,
        discovered_at__gte=cutoff,
    ).exclude(raw_data={}).order_by('-discovered_at')

    if source_type:
        qs = qs.filter(source_type=source_type)

    results = flag_high_value_leads(queryset=qs[:1000], threshold=threshold)

    # Build display-ready list
    leads_data = []
    for lead, value in results:
        location = f'{lead.region or ""}, {lead.state or ""}'.strip(', ')
        leads_data.append({
            'lead': lead,
            'value': value,
            'location': location or 'Unknown',
            'preview': (lead.source_content or '')[:80].replace('\n', ' '),
        })

    total_value = sum(v for _, v in results)
    avg_value = total_value / len(results) if results else 0

    # Get distinct source types for filter dropdown
    source_types = (
        Lead.objects.filter(raw_data__isnull=False)
        .exclude(raw_data={})
        .values_list('source_type', flat=True)
        .distinct()
        .order_by('source_type')
    )

    return render(request, 'sales/high_value_leads.html', {
        'leads_data': leads_data,
        'total_value': total_value,
        'avg_value': avg_value,
        'lead_count': len(results),
        'threshold': threshold,
        'source_type': source_type,
        'days': days,
        'source_types': [s for s in source_types if s],
        'sp': request.salesperson,
        'is_sales_admin': request.is_sales_admin,
    })


# ─────────────────────────────────────────────────────────────
# CUSTOMER CONTEXT (for salespeople working on behalf of customers)
# ─────────────────────────────────────────────────────────────

@login_required
def set_customer_context(request):
    """POST: Set the active customer context for a salesperson."""
    if not (hasattr(request.user, 'salesperson_profile') or request.user.is_superuser):
        return redirect('dashboard_home')

    if request.method != 'POST':
        return redirect('customer_accounts')

    customer_id = request.POST.get('customer_id')
    if not customer_id:
        return redirect('customer_accounts')

    try:
        bp = BusinessProfile.objects.get(pk=customer_id, is_active=True)
    except BusinessProfile.DoesNotExist:
        return redirect('customer_accounts')

    request.session['active_customer_id'] = bp.pk
    next_url = request.POST.get('next', '') or 'crm_pipeline'
    return redirect(next_url)


@login_required
def clear_customer_context(request):
    """Clear the active customer context."""
    if 'active_customer_id' in request.session:
        del request.session['active_customer_id']
    return redirect('sales_dashboard')
