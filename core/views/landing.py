from django.shortcuts import render


def landing_page(request):
    if request.user.is_authenticated:
        from django.shortcuts import redirect
        profile = getattr(request.user, 'business_profile', None)
        if profile and profile.onboarding_complete:
            return redirect('dashboard_home')
        elif profile:
            return redirect('onboarding')
    return render(request, 'landing.html')
