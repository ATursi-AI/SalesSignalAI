from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib import messages
from core.models import BusinessProfile


def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')
        business_name = request.POST.get('business_name', '').strip()
        owner_name = request.POST.get('owner_name', '').strip()

        if password != password2:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'registration/register.html')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already taken.')
            return render(request, 'registration/register.html')

        if User.objects.filter(email=email).exists():
            messages.error(request, 'Email already registered.')
            return render(request, 'registration/register.html')

        user = User.objects.create_user(username=username, email=email, password=password)
        BusinessProfile.objects.create(
            user=user,
            business_name=business_name,
            owner_name=owner_name,
            email=email,
        )
        login(request, user)
        return redirect('onboarding')

    return render(request, 'registration/register.html')


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            profile = getattr(user, 'business_profile', None)
            if profile and profile.onboarding_complete:
                return redirect('dashboard_home')
            return redirect('onboarding')
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'registration/login.html')


def logout_view(request):
    logout(request)
    return redirect('landing')
