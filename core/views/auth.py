from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib import messages
from core.models import BusinessProfile


def register_view(request):
    """Public registration is disabled. Accounts are created by admin only."""
    return redirect('login')


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        # Allow login with email or username
        user = authenticate(request, username=username, password=password)
        if user is None and '@' in username:
            # Try to find user by email
            try:
                email_user = User.objects.get(email=username)
                user = authenticate(request, username=email_user.username, password=password)
            except User.DoesNotExist:
                pass

        if user is not None:
            login(request, user)
            profile = getattr(user, 'business_profile', None)
            if profile and profile.onboarding_complete:
                return redirect('dashboard_home')
            return redirect('onboarding')
        else:
            messages.error(request, 'Invalid email or password.')

    return render(request, 'registration/login.html')


def logout_view(request):
    logout(request)
    return redirect('landing')
