"""
Sales Admin views — internal dashboard for managing the sales team.
Only accessible by superusers at /sales-admin/.
"""
import csv
import io
from datetime import date, timedelta

from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.db.models import Count, Sum, Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone

from core.models.sales import SalesPerson, SalesProspect, SalesActivity
from core.models.leads import Lead


def superuser_required(view):
    return user_passes_test(lambda u: u.is_superuser)(view)


@superuser_required
def dashboard(request):
    """Team dashboard with KPIs and per-salesperson metrics."""
    now = timezone.now()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    salespeople = SalesPerson.objects.filter(status='active')

    # Global KPIs
    total_prospects = SalesProspect.objects.exclude(
        pipeline_stage__in=['closed_won', 'closed_lost'],
    ).count()

    demos_this_week = SalesActivity.objects.filter(
        activity_type='demo',
        created_at__date__gte=week_start,
    ).count()

    deals_this_month = SalesProspect.objects.filter(
        pipeline_stage='closed_won',
        updated_at__date__gte=month_start,
    ).count()

    mrr_closed = SalesProspect.objects.filter(
        pipeline_stage='closed_won',
        updated_at__date__gte=month_start,
        estimated_monthly_value__isnull=False,
    ).aggregate(total=Sum('estimated_monthly_value'))['total'] or 0

    # Per-salesperson stats
    team_stats = []
    for sp in salespeople:
        calls_today = sp.activities.filter(
            activity_type='call', created_at__date=today,
        ).count()
        calls_week = sp.activities.filter(
            activity_type='call', created_at__date__gte=week_start,
        ).count()
        demos = sp.activities.filter(
            activity_type='demo', created_at__date__gte=week_start,
        ).count()
        deals = sp.prospects.filter(
            pipeline_stage='closed_won', updated_at__date__gte=month_start,
        ).count()
        pipeline_value = sp.prospects.exclude(
            pipeline_stage__in=['closed_won', 'closed_lost'],
        ).filter(
            estimated_monthly_value__isnull=False,
        ).aggregate(total=Sum('estimated_monthly_value'))['total'] or 0
        active_prospects = sp.prospects.exclude(
            pipeline_stage__in=['closed_won', 'closed_lost'],
        ).count()

        team_stats.append({
            'sp': sp,
            'calls_today': calls_today,
            'calls_week': calls_week,
            'demos': demos,
            'deals': deals,
            'pipeline_value': pipeline_value,
            'active_prospects': active_prospects,
        })

    context = {
        'total_salespeople': salespeople.count(),
        'total_prospects': total_prospects,
        'demos_this_week': demos_this_week,
        'deals_this_month': deals_this_month,
        'mrr_closed': mrr_closed,
        'team_stats': team_stats,
    }
    return render(request, 'sales_admin/dashboard.html', context)


@superuser_required
def manage_team(request):
    """List and manage salespeople."""
    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'create':
            username = request.POST.get('username', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            phone = request.POST.get('phone', '').strip()
            territory = request.POST.get('territory', '').strip()

            if not username:
                return JsonResponse({'error': 'Username is required'}, status=400)

            if User.objects.filter(username=username).exists():
                return JsonResponse({'error': 'Username already taken'}, status=400)

            user = User.objects.create_user(
                username=username,
                email=email,
                password='SalesSignal2024!',
                first_name=first_name,
                last_name=last_name,
                is_staff=True,
            )

            SalesPerson.objects.create(
                user=user,
                phone=phone,
                territory=territory,
                hire_date=date.today(),
            )

            return JsonResponse({'success': True})

        elif action == 'update':
            sp_id = request.POST.get('sp_id')
            sp = get_object_or_404(SalesPerson, id=sp_id)
            sp.phone = request.POST.get('phone', sp.phone)
            sp.territory = request.POST.get('territory', sp.territory)
            sp.daily_call_goal = int(request.POST.get('daily_call_goal', sp.daily_call_goal))
            sp.save(update_fields=['phone', 'territory', 'daily_call_goal'])
            return JsonResponse({'success': True})

        elif action == 'toggle_status':
            sp_id = request.POST.get('sp_id')
            sp = get_object_or_404(SalesPerson, id=sp_id)
            sp.status = 'inactive' if sp.status == 'active' else 'active'
            sp.save(update_fields=['status'])
            return JsonResponse({'success': True, 'status': sp.status})

    salespeople = SalesPerson.objects.all().order_by('status', 'user__first_name')
    context = {'salespeople': salespeople}
    return render(request, 'sales_admin/team.html', context)


@superuser_required
def assign_prospects(request):
    """Bulk import and assign prospects to salespeople."""
    salespeople = SalesPerson.objects.filter(status='active')

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'import_leads':
            # Import NO_WEBSITE_PROSPECT and other leads from Lead table
            lead_ids = request.POST.getlist('lead_ids')
            sp_id = request.POST.get('salesperson_id')

            if not sp_id:
                return JsonResponse({'error': 'Select a salesperson'}, status=400)

            sp = get_object_or_404(SalesPerson, id=sp_id)
            imported = 0

            for lid in lead_ids:
                try:
                    lead = Lead.objects.get(id=lid)
                    raw = lead.raw_data or {}

                    # Skip if already imported
                    if SalesProspect.objects.filter(source_lead_id=lead.id).exists():
                        continue

                    SalesProspect.objects.create(
                        salesperson=sp,
                        business_name=raw.get('business_name', lead.source_author or 'Unknown'),
                        phone=raw.get('phone', ''),
                        address=raw.get('address', lead.detected_location),
                        city=raw.get('city', ''),
                        state=raw.get('state', ''),
                        zip_code=raw.get('zip_code', ''),
                        service_category=raw.get('category', ''),
                        source='google_maps_scan',
                        source_lead_id=lead.id,
                        google_rating=raw.get('rating'),
                        google_review_count=raw.get('review_count'),
                        has_website=raw.get('type') != 'no_website',
                        notes=lead.source_content[:500] if lead.source_content else '',
                    )
                    imported += 1
                except Lead.DoesNotExist:
                    continue

            return JsonResponse({'success': True, 'imported': imported})

        elif action == 'upload_csv':
            sp_id = request.POST.get('salesperson_id')
            csv_file = request.FILES.get('csv_file')

            if not sp_id or not csv_file:
                return JsonResponse({'error': 'Select a salesperson and upload a CSV'}, status=400)

            sp = get_object_or_404(SalesPerson, id=sp_id)
            decoded = csv_file.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(decoded))
            imported = 0

            for row in reader:
                biz_name = row.get('business_name', row.get('name', '')).strip()
                if not biz_name:
                    continue

                SalesProspect.objects.create(
                    salesperson=sp,
                    business_name=biz_name,
                    owner_name=row.get('owner_name', row.get('contact', '')).strip(),
                    phone=row.get('phone', '').strip(),
                    email=row.get('email', '').strip(),
                    website=row.get('website', '').strip(),
                    address=row.get('address', '').strip(),
                    city=row.get('city', '').strip(),
                    state=row.get('state', '').strip(),
                    zip_code=row.get('zip_code', row.get('zip', '')).strip(),
                    service_category=row.get('category', row.get('service_category', '')).strip(),
                    source='manual_entry',
                )
                imported += 1

            return JsonResponse({'success': True, 'imported': imported})

        elif action == 'reassign':
            prospect_ids = request.POST.getlist('prospect_ids')
            sp_id = request.POST.get('salesperson_id')
            if sp_id and prospect_ids:
                sp = get_object_or_404(SalesPerson, id=sp_id)
                SalesProspect.objects.filter(id__in=prospect_ids).update(salesperson=sp)
                return JsonResponse({'success': True})

    # Get importable leads (NO_WEBSITE_PROSPECT + google_maps + new businesses)
    importable_leads = Lead.objects.filter(
        platform='google_maps',
    ).exclude(
        id__in=SalesProspect.objects.filter(
            source_lead_id__isnull=False,
        ).values_list('source_lead_id', flat=True),
    ).order_by('-discovered_at')[:100]

    # Unassigned prospects
    unassigned = SalesProspect.objects.filter(
        pipeline_stage='new',
    ).order_by('-created_at')[:50]

    context = {
        'salespeople': salespeople,
        'importable_leads': importable_leads,
        'unassigned': unassigned,
    }
    return render(request, 'sales_admin/assign.html', context)


@superuser_required
def salesperson_detail(request, sp_id):
    """View a specific salesperson's full pipeline (admin view)."""
    sp = get_object_or_404(SalesPerson, id=sp_id)
    prospects = sp.prospects.all().order_by('-updated_at')

    stage_filter = request.GET.get('stage', '')
    if stage_filter:
        prospects = prospects.filter(pipeline_stage=stage_filter)

    context = {
        'sp': sp,
        'prospects': prospects[:100],
        'stages': SalesProspect.PIPELINE_CHOICES,
        'current_stage': stage_filter,
    }
    return render(request, 'sales_admin/sp_detail.html', context)
