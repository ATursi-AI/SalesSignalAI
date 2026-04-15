from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode

from core.models import BusinessProfile


def register_view(request):
    """Public registration is disabled. Redirect to new signup."""
    return redirect('signup')


def _get_login_redirect(user):
    """Determine where to redirect a user after login based on their role."""
    # Force password change for sales-assisted accounts
    profile = getattr(user, 'business_profile', None)
    if profile and profile.must_change_password:
        return 'force_password_change'

    # 1. Superuser/staff → Command Center
    if user.is_superuser or user.is_staff:
        return 'admin_lead_repository'

    # 2. Salesperson → Sales Dashboard
    if hasattr(user, 'salesperson_profile'):
        try:
            sp = user.salesperson_profile
            if sp.status == 'active':
                return 'sales_pipeline'
        except Exception:
            pass

    # 3. Customer with BusinessProfile
    if profile:
        if not profile.onboarding_complete:
            return 'onboarding'
        return 'dashboard_home'

    # 4. No profile at all — send to onboarding to create one
    return 'onboarding'


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_get_login_redirect(request.user))

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        # Allow login with email or username
        user = authenticate(request, username=username, password=password)
        if user is None and '@' in username:
            try:
                email_user = User.objects.get(email=username)
                user = authenticate(request, username=email_user.username, password=password)
            except User.DoesNotExist:
                pass

        if user is not None:
            login(request, user)
            return redirect(_get_login_redirect(user))
        else:
            messages.error(request, 'Invalid email or password.')

    return render(request, 'registration/login.html')


def logout_view(request):
    logout(request)
    return redirect('landing')


# ─── Password Reset ───────────────────────────────────────────────

def password_reset_request(request):
    """Send password reset email."""
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        try:
            user = User.objects.get(email=email, is_active=True)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            link = f"https://salessignalai.com/auth/password-reset/confirm/{uid}/{token}/"

            send_mail(
                'Reset your password — SalesSignal AI',
                f'Hi {user.first_name or "there"},\n\n'
                f'Click the link below to reset your password:\n\n'
                f'{link}\n\n'
                f'If you didn\'t request this, you can ignore this email.\n\n'
                f'— SalesSignal AI\nsupport@salessignalai.com',
                getattr(settings, 'SUPPORT_EMAIL', 'support@salessignalai.com'),
                [email],
                fail_silently=True,
            )
        except User.DoesNotExist:
            pass  # Don't reveal if email exists

        return render(request, 'registration/password_reset_sent.html', {'email': email})

    return render(request, 'registration/password_reset.html')


def password_reset_confirm(request, uidb64, token):
    """Confirm password reset."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if not user or not default_token_generator.check_token(user, token):
        return render(request, 'registration/password_reset_invalid.html')

    if request.method == 'POST':
        password = request.POST.get('new_password', '').strip()
        password2 = request.POST.get('confirm_password', '').strip()

        if len(password) < 8:
            return render(request, 'registration/password_reset_confirm.html', {
                'error': 'Password must be at least 8 characters.',
                'uidb64': uidb64, 'token': token,
            })
        if password != password2:
            return render(request, 'registration/password_reset_confirm.html', {
                'error': 'Passwords do not match.',
                'uidb64': uidb64, 'token': token,
            })

        user.set_password(password)
        user.save()

        # Clear temp password if exists
        profile = getattr(user, 'business_profile', None)
        if profile and profile.must_change_password:
            profile.must_change_password = False
            profile.temp_password = ''
            profile.save(update_fields=['must_change_password', 'temp_password'])

        messages.success(request, 'Password updated. Please log in with your new password.')
        return redirect('login')

    return render(request, 'registration/password_reset_confirm.html', {
        'uidb64': uidb64, 'token': token,
    })
