from collections import OrderedDict

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from core.models import ServiceCategory, BusinessProfile, UserKeyword


@login_required
def onboarding_view(request):
    profile = request.user.business_profile
    if profile.onboarding_complete:
        return redirect('dashboard_home')

    categories = ServiceCategory.objects.filter(is_active=True).prefetch_related('subcategories')

    # Group categories by industry_group for tabbed UI
    group_labels = dict(ServiceCategory.INDUSTRY_GROUPS)
    grouped = OrderedDict()
    for key, label in ServiceCategory.INDUSTRY_GROUPS:
        cats = [c for c in categories if c.industry_group == key]
        if cats:
            grouped[key] = {'label': label, 'categories': cats}

    if request.method == 'POST':
        # Handle both form-encoded and JSON requests
        import json as _json
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                body_data = _json.loads(request.body)
                step = body_data.get('step')
            except (ValueError, AttributeError):
                step = request.POST.get('step')
        else:
            step = request.POST.get('step')

        # Handle JSON body (from keyword save)
        if not step and request.content_type and 'json' in request.content_type:
            import json as _json
            try:
                json_data = _json.loads(request.body)
                step = json_data.get('step')
            except (ValueError, AttributeError):
                pass

        if step == '1':
            category_id = request.POST.get('service_category')
            if category_id:
                profile.service_category_id = int(category_id)
                profile.save()
                # Auto-populate keywords from the selected category
                profile.populate_default_keywords()

                # Return the populated keywords for the UI
                keywords = list(
                    UserKeyword.objects.filter(business=profile, is_active=True)
                    .values_list('keyword', flat=True)
                )
                return JsonResponse({
                    'success': True,
                    'next_step': 2,
                    'keywords': keywords,
                })

        elif step == '1_keywords':
            # Save keyword changes
            keywords = request.POST.getlist('keywords')
            if not keywords:
                # Fallback: try JSON body
                import json
                try:
                    data = json.loads(request.body)
                    keywords = data.get('keywords', [])
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass

            if keywords:
                # Deactivate all existing keywords
                UserKeyword.objects.filter(business=profile).update(is_active=False)
                # Create / reactivate the submitted ones
                for kw in keywords:
                    kw = kw.strip()
                    if not kw:
                        continue
                    obj, created = UserKeyword.objects.get_or_create(
                        business=profile, keyword=kw,
                        defaults={'source': 'custom', 'is_active': True},
                    )
                    if not created:
                        obj.is_active = True
                        obj.save(update_fields=['is_active'])
                return JsonResponse({'success': True, 'next_step': 3})
            return JsonResponse({'success': True, 'next_step': 3})

        elif step == '2':
            address = request.POST.get('address', '')
            city = request.POST.get('city', '')
            state = request.POST.get('state', '')
            zip_code = request.POST.get('zip_code', '')
            radius = request.POST.get('service_radius_miles', 15)
            profile.address = address
            profile.city = city
            profile.state = state
            profile.zip_code = zip_code
            profile.service_radius_miles = int(radius)
            profile.save()
            return JsonResponse({'success': True, 'next_step': 3})

        elif step == '3':
            alert_email = request.POST.get('alert_via_email') == 'on'
            alert_sms = request.POST.get('alert_via_sms') == 'on'
            alert_phone = request.POST.get('alert_phone', '')
            profile.alert_via_email = alert_email
            profile.alert_via_sms = alert_sms
            profile.alert_phone = alert_phone
            profile.onboarding_complete = True
            profile.save()

            # Backfill leads from the last 48 hours
            backfill_count = _backfill_recent_leads(profile)

            return JsonResponse({
                'success': True,
                'redirect': '/dashboard/',
                'backfilled': backfill_count,
            })

    # Get existing keywords for the UI
    existing_keywords = list(
        UserKeyword.objects.filter(business=profile, is_active=True)
        .values_list('keyword', flat=True)
    )

    return render(request, 'onboarding/wizard.html', {
        'categories': categories,
        'grouped_categories': grouped,
        'profile': profile,
        'existing_keywords': existing_keywords,
    })


def _backfill_recent_leads(profile):
    """
    Assign existing leads from the last 48 hours that match the user's
    service type and geography. Returns the count of new assignments.
    """
    from django.utils import timezone
    from datetime import timedelta
    from core.models import Lead, LeadAssignment

    cutoff = timezone.now() - timedelta(hours=48)

    # Match by service category
    leads = Lead.objects.filter(
        detected_service_type=profile.service_category,
        discovered_at__gte=cutoff,
    )

    # Filter by geography: match on zip code or detected_location containing city
    if profile.zip_code:
        geo_leads = leads.filter(detected_zip=profile.zip_code)
        if profile.city:
            geo_leads = leads.filter(
                detected_location__icontains=profile.city,
            ) | geo_leads
    elif profile.city:
        geo_leads = leads.filter(detected_location__icontains=profile.city)
    else:
        geo_leads = leads  # No geo filter if no location set

    # Exclude already-assigned leads
    existing_ids = LeadAssignment.objects.filter(
        business=profile,
    ).values_list('lead_id', flat=True)

    to_assign = geo_leads.exclude(id__in=existing_ids).distinct()[:50]  # Cap at 50

    assignments = []
    for lead in to_assign:
        assignments.append(LeadAssignment(
            lead=lead,
            business=profile,
            status='new',
        ))

    if assignments:
        LeadAssignment.objects.bulk_create(assignments, ignore_conflicts=True)

    return len(assignments)
