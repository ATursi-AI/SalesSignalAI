from collections import OrderedDict

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from core.models import ServiceCategory, BusinessProfile


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
        step = request.POST.get('step')

        if step == '1':
            category_id = request.POST.get('service_category')
            if category_id:
                profile.service_category_id = int(category_id)
                profile.save()
                # Auto-populate keywords from the selected category
                profile.populate_default_keywords()
                return JsonResponse({'success': True, 'next_step': 2})

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
            return JsonResponse({'success': True, 'redirect': '/dashboard/'})

    return render(request, 'onboarding/wizard.html', {
        'categories': categories,
        'grouped_categories': grouped,
        'profile': profile,
    })
