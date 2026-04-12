import json
import secrets
import string

import stripe
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import BusinessProfile

stripe.api_key = settings.STRIPE_SECRET_KEY

# Package Bundle pricing with pre-generated Stripe payment links
PACKAGE_BUNDLES = {
    'starter_ai': {
        'name': 'Starter — AI Automated',
        'price': 599,
        'description': 'Email drip + Social listings + Dashboard lead access',
        'payment_link': 'https://buy.stripe.com/4gM3cpgt86hKd7Ac596oo0j',
    },
    'starter_human': {
        'name': 'Starter — Human + AI',
        'price': 999,
        'description': 'Email drip + Social listings + Dashboard lead access',
        'payment_link': 'https://buy.stripe.com/3cI00d4Kq21u4B40mr6oo0k',
    },
    'growth_ai': {
        'name': 'Growth — AI Automated',
        'price': 1199,
        'description': '+ Video email + Appointment setting + Social (5 platforms)',
        'payment_link': 'https://buy.stripe.com/14AfZba4Kay0d7A6KP6oo0l',
    },
    'growth_human': {
        'name': 'Growth — Human + AI',
        'price': 1999,
        'description': '+ Video email + Appointment setting + Social (5 platforms)',
        'payment_link': 'https://buy.stripe.com/eVq8wJ2CidKcaZs2uz6oo0m',
    },
    'dominate_ai': {
        'name': 'Dominate — AI Automated',
        'price': 1999,
        'description': '+ Inbound call center + Landing page',
        'payment_link': 'https://buy.stripe.com/14A3cp3Gm5dG9Vo2uz6oo0n',
    },
    'dominate_human': {
        'name': 'Dominate — Human + AI',
        'price': 3499,
        'description': '+ Inbound call center + Landing page',
        'payment_link': 'https://buy.stripe.com/bJe9ANel049CebEd9d6oo0o',
    },
    'closer_ai': {
        'name': 'Closer — AI Automated',
        'price': 3999,
        'description': '+ Outbound call center + Appointment setting',
        'payment_link': 'https://buy.stripe.com/cNieV7fp4bC42sW3yD6oo0p',
    },
    'closer_human': {
        'name': 'Closer — Human + AI',
        'price': 6499,
        'description': '+ Outbound call center + Appointment setting',
        'payment_link': 'https://buy.stripe.com/7sYeV7a4K8pS4B40mr6oo0q',
    },
    'full_service_ai': {
        'name': 'Full Service — AI + Human',
        'price': 7999,
        'description': 'Everything + Outbound sales team + Account manager',
        'payment_link': 'https://buy.stripe.com/4gMcMZ7WCbC44B41qv6oo0r',
    },
    'full_service_human': {
        'name': 'Full Service — Full Human',
        'price': 12999,
        'description': 'Everything + Outbound sales team + Account manager',
        'payment_link': 'https://buy.stripe.com/6oUfZbel00Xqd7Aedh6oo0s',
    },
}

# A La Carte services with payment links
ALACARTE_SERVICES = {
    'email_drip_ai': {'name': 'Email Drip Campaign — AI', 'price': '$199/mo', 'link': 'https://buy.stripe.com/aFadR3a4K0XqaZs9X16oo00'},
    'email_drip_human': {'name': 'Email Drip Campaign — Human+AI', 'price': '$399/mo', 'link': 'https://buy.stripe.com/dRm4gt7WC5dG8Rkedh6oo01'},
    'video_drip_ai': {'name': 'Video Email Drip — AI', 'price': '$349/mo', 'link': 'https://buy.stripe.com/6oUdR3b8O0XqebE0mr6oo02'},
    'video_drip_human': {'name': 'Video Email Drip — Human+AI', 'price': '$599/mo', 'link': 'https://buy.stripe.com/fZu7sF2Ci5dGebE8SX6oo03'},
    'lead_access': {'name': 'Lead Access — Dashboard', 'price': '$299/mo', 'link': 'https://buy.stripe.com/fZu3cp5Ou6hKaZs4CH6oo04'},
    'lead_qualified': {'name': 'Lead Access — Human-Qualified', 'price': '$125/lead', 'link': 'https://buy.stripe.com/aFabIV7WC49C2sW0mr6oo0w'},
    'social_ai': {'name': 'Social Listings — AI (3 platforms)', 'price': '$349/mo', 'link': 'https://buy.stripe.com/eVqdR32CidKc5F8d9d6oo05'},
    'social_human': {'name': 'Social Listings — Human+AI (5+ platforms)', 'price': '$699/mo', 'link': 'https://buy.stripe.com/3cIfZb0ua5dGaZsedh6oo06'},
    'appt_ai': {'name': 'Appointment Setting — AI', 'price': 'Starting at $99/appt', 'link': 'https://buy.stripe.com/bJecMZel07lO2sWfhl6oo0x'},
    'appt_human': {'name': 'Appointment Setting — Human', 'price': 'Starting at $175/appt', 'link': 'https://buy.stripe.com/cNi6oB90GeOggjM7OT6oo0y'},
    'inbound_ai': {'name': 'Inbound Call Center — AI', 'price': 'Starting at $399/mo', 'link': 'https://buy.stripe.com/cNi4gt2Ci35y0kO8SX6oo07'},
    'inbound_human': {'name': 'Inbound Call Center — Human', 'price': 'Starting at $699/mo', 'link': 'https://buy.stripe.com/5kQ8wJa4KgWod7Ab156oo08'},
    'outbound_ai': {'name': 'Outbound Call Center — AI', 'price': 'Starting at $599/mo', 'link': 'https://buy.stripe.com/7sY9ANccS49C1oS6KP6oo09'},
    'outbound_human': {'name': 'Outbound Call Center — Human', 'price': 'Starting at $1,199/mo', 'link': 'https://buy.stripe.com/14A6oBgt8eOg0kOb156oo0a'},
    'landing_ai': {'name': 'Landing Page — AI', 'price': '$99/mo + $399 setup', 'link': 'https://buy.stripe.com/bJeaER6Sy9tW7Ngfhl6oo0b'},
    'landing_human': {'name': 'Landing Page — Custom', 'price': '$149/mo + $999 setup', 'link': 'https://buy.stripe.com/7sY4gt5Ou35y6Jc7OT6oo0c'},
    'sales_team_ai': {'name': 'Outbound Sales Team — AI', 'price': 'Starting at $3,999/mo', 'link': 'https://buy.stripe.com/8x2bIV0ua8pS3x06KP6oo0d'},
    'sales_team_human': {'name': 'Outbound Sales Team — Human', 'price': 'Starting at $7,499/mo', 'link': 'https://buy.stripe.com/4gM8wJ0ua9tWd7A7OT6oo0e'},
    'seo_ai': {'name': 'SEO + AEO — AI', 'price': 'Starting at $399/mo', 'link': 'https://buy.stripe.com/fZudR33Gmay02sW6KP6oo0f'},
    'seo_human': {'name': 'SEO + AEO — Human+AI', 'price': 'Starting at $799/mo', 'link': 'https://buy.stripe.com/eVqbIV1ye9tW3x04CH6oo0g'},
    'byo_standard': {'name': 'BYO Leads — Standard', 'price': '$199/mo + $99/appt', 'link': 'https://buy.stripe.com/3cI4gtdgWgWod7Afhl6oo0h'},
    'byo_emergency': {'name': 'BYO Leads — Emergency', 'price': '$299/mo + $149/appt', 'link': 'https://buy.stripe.com/5kQeV7fp4ay05F88SX6oo0i'},
}

SETUP_FEE_LINK = 'https://buy.stripe.com/3cIcMZ3GmfSkaZs2uz6oo0t'

# Keep PLAN_PRICES for backward compat with billing_page and create_checkout_session
PLAN_PRICES = {
    'outreach': {'name': 'Starter AI (Legacy)', 'price': 599, 'stripe_price': getattr(settings, 'STRIPE_PRICE_OUTREACH', '')},
    'growth': {'name': 'Growth AI (Legacy)', 'price': 1199, 'stripe_price': getattr(settings, 'STRIPE_PRICE_GROWTH', '')},
    'dominate': {'name': 'Dominate AI (Legacy)', 'price': 1999, 'stripe_price': getattr(settings, 'STRIPE_PRICE_DOMINATE', '')},
}


# ─── Signup ───────────────────────────────────────────────────────

def signup_view(request):
    """Self-service signup page."""
    if request.user.is_authenticated:
        return redirect('dashboard_home')

    pre_plan = request.GET.get('plan', '')

    if request.method == 'POST':
        # Honeypot check — real users never see/fill this field
        if request.POST.get('company_url', ''):
            # Bot detected — silently show the "check your email" page
            # so the bot thinks it succeeded (no retry)
            fake_email = request.POST.get('email', 'user@example.com')
            return render(request, 'registration/signup_verify.html', {'email': fake_email})

        business_name = request.POST.get('business_name', '').strip()
        owner_name = request.POST.get('owner_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '').strip()
        password2 = request.POST.get('password2', '').strip()

        errors = []
        if not business_name:
            errors.append('Business name is required.')
        if not owner_name:
            errors.append('Your name is required.')
        if not email:
            errors.append('Email is required.')
        if not password or len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != password2:
            errors.append('Passwords do not match.')
        if User.objects.filter(email=email).exists():
            errors.append('An account with this email already exists.')
        if User.objects.filter(username=email).exists():
            errors.append('An account with this email already exists.')

        if errors:
            return render(request, 'registration/signup.html', {
                'errors': errors,
                'form_data': request.POST,
                'pre_plan': pre_plan,
            })

        # Create inactive user
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            is_active=False,
            first_name=owner_name.split()[0] if owner_name else '',
            last_name=' '.join(owner_name.split()[1:]) if len(owner_name.split()) > 1 else '',
        )

        # Create business profile
        BusinessProfile.objects.create(
            user=user,
            business_name=business_name,
            owner_name=owner_name,
            email=email,
            phone=phone,
            account_status='pending_verification',
        )

        # Send verification email
        _send_verification_email(user)

        return render(request, 'registration/signup_verify.html', {'email': email})

    return render(request, 'registration/signup.html', {'pre_plan': pre_plan})


def verify_email(request, uidb64, token):
    """Verify email and activate account."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=['is_active'])

        profile = user.business_profile
        profile.account_status = 'trial'
        profile.trial_leads_remaining = 10
        profile.save(update_fields=['account_status', 'trial_leads_remaining'])

        login(request, user)
        return redirect('onboarding')
    else:
        return render(request, 'registration/verify_failed.html')


def _send_verification_email(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"https://salessignalai.com/verify/{uid}/{token}/"

    send_mail(
        'Verify your email — SalesSignal AI',
        f'Hi {user.first_name or "there"},\n\n'
        f'Click the link below to verify your email and get started with SalesSignal AI:\n\n'
        f'{link}\n\n'
        f'This link expires in 3 days.\n\n'
        f'— The SalesSignal AI Team\nsupport@salessignalai.com',
        settings.SUPPORT_EMAIL,
        [user.email],
        fail_silently=True,
    )


# ─── Stripe Checkout ──────────────────────────────────────────────

@login_required
def create_checkout_session(request):
    """Create a Stripe Checkout session for subscription + setup fee."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    plan = data.get('plan', '')
    profile = request.user.business_profile

    if plan not in PLAN_PRICES:
        return JsonResponse({'error': 'Invalid plan'}, status=400)

    plan_info = PLAN_PRICES[plan]

    if not plan_info['stripe_price']:
        # No Stripe price configured — mark plan and continue
        profile.subscription_tier = plan
        profile.account_status = 'pending_payment'
        profile.save(update_fields=['subscription_tier', 'account_status'])
        return JsonResponse({'ok': True, 'message': 'Plan selected (Stripe not configured)'})

    try:
        # Create or retrieve Stripe customer
        if not profile.stripe_customer_id:
            customer = stripe.Customer.create(
                email=request.user.email,
                name=profile.owner_name,
                phone=profile.phone,
                metadata={'user_id': request.user.id, 'business': profile.business_name},
            )
            profile.stripe_customer_id = customer.id
            profile.save(update_fields=['stripe_customer_id'])

        line_items = [
            {'price': plan_info['stripe_price'], 'quantity': 1},
        ]

        # Add setup fee if configured
        if settings.STRIPE_SETUP_FEE_PRICE_ID:
            line_items.append({
                'price': settings.STRIPE_SETUP_FEE_PRICE_ID,
                'quantity': 1,
            })

        session = stripe.checkout.Session.create(
            customer=profile.stripe_customer_id,
            payment_method_types=['card'],
            line_items=line_items,
            mode='subscription',
            success_url='https://salessignalai.com/onboarding/?payment=success',
            cancel_url='https://salessignalai.com/onboarding/?payment=cancelled',
            metadata={
                'user_id': request.user.id,
                'plan': plan,
            },
        )

        profile.subscription_tier = plan
        profile.account_status = 'pending_payment'
        profile.save(update_fields=['subscription_tier', 'account_status'])

        return JsonResponse({'session_id': session.id, 'url': session.url})

    except stripe.error.StripeError as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def create_portal_session(request):
    """Create Stripe Customer Portal session for billing management."""
    profile = request.user.business_profile
    if not profile.stripe_customer_id:
        return redirect('dashboard_home')

    try:
        session = stripe.billing_portal.Session.create(
            customer=profile.stripe_customer_id,
            return_url='https://salessignalai.com/dashboard/settings/billing/',
        )
        return redirect(session.url)
    except stripe.error.StripeError:
        return redirect('dashboard_home')


# ─── Stripe Webhook ───────────────────────────────────────────────

@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Handle Stripe webhook events."""
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

    if settings.STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except (ValueError, stripe.error.SignatureVerificationError):
            return HttpResponse(status=400)
    else:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

    event_type = event.get('type', '')
    data = event.get('data', {}).get('object', {})

    if event_type == 'checkout.session.completed':
        _handle_checkout_completed(data)
    elif event_type == 'invoice.paid':
        _handle_invoice_paid(data)
    elif event_type == 'invoice.payment_failed':
        _handle_payment_failed(data)
    elif event_type == 'customer.subscription.deleted':
        _handle_subscription_deleted(data)
    elif event_type == 'customer.subscription.updated':
        _handle_subscription_updated(data)

    return HttpResponse(status=200)


def _handle_checkout_completed(session):
    customer_id = session.get('customer', '')
    subscription_id = session.get('subscription', '')
    plan = session.get('metadata', {}).get('plan', '')

    try:
        profile = BusinessProfile.objects.get(stripe_customer_id=customer_id)
        profile.stripe_subscription_id = subscription_id or ''
        profile.account_status = 'active'
        if plan:
            profile.subscription_tier = plan
        profile.save(update_fields=['stripe_subscription_id', 'account_status', 'subscription_tier'])
    except BusinessProfile.DoesNotExist:
        pass


def _handle_invoice_paid(invoice):
    customer_id = invoice.get('customer', '')
    try:
        profile = BusinessProfile.objects.get(stripe_customer_id=customer_id)
        if profile.account_status == 'pending_payment':
            profile.account_status = 'active'
            profile.save(update_fields=['account_status'])
    except BusinessProfile.DoesNotExist:
        pass


def _handle_payment_failed(invoice):
    customer_id = invoice.get('customer', '')
    try:
        profile = BusinessProfile.objects.get(stripe_customer_id=customer_id)
        profile.account_status = 'paused'
        profile.save(update_fields=['account_status'])

        send_mail(
            f'Payment failed: {profile.business_name}',
            f'Payment failed for {profile.business_name} ({profile.email}).\n'
            f'Stripe customer: {customer_id}',
            settings.SUPPORT_EMAIL,
            [settings.SUPPORT_EMAIL],
            fail_silently=True,
        )
    except BusinessProfile.DoesNotExist:
        pass


def _handle_subscription_deleted(subscription):
    customer_id = subscription.get('customer', '')
    try:
        profile = BusinessProfile.objects.get(stripe_customer_id=customer_id)
        profile.account_status = 'cancelled'
        profile.subscription_tier = 'none'
        profile.stripe_subscription_id = ''
        profile.save(update_fields=['account_status', 'subscription_tier', 'stripe_subscription_id'])
    except BusinessProfile.DoesNotExist:
        pass


def _handle_subscription_updated(subscription):
    customer_id = subscription.get('customer', '')
    try:
        profile = BusinessProfile.objects.get(stripe_customer_id=customer_id)
        status = subscription.get('status', '')
        if status == 'active':
            profile.account_status = 'active'
        elif status in ('past_due', 'unpaid'):
            profile.account_status = 'paused'
        profile.save(update_fields=['account_status'])
    except BusinessProfile.DoesNotExist:
        pass


# ─── Sales-Assisted Account Creation ─────────────────────────────

def _generate_temp_password(length=12):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


@login_required
def sales_create_customer(request):
    """Staff-only: create customer account on the phone."""
    if not request.user.is_staff:
        return redirect('dashboard_home')

    if request.method == 'POST':
        business_name = request.POST.get('business_name', '').strip()
        owner_name = request.POST.get('owner_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        phone = request.POST.get('phone', '').strip()
        trade = request.POST.get('trade', '').strip()
        city = request.POST.get('city', '').strip()
        state = request.POST.get('state', '').strip()
        plan = request.POST.get('plan', 'trial')
        payment_method = request.POST.get('payment_method', 'no_payment')
        selected_services = request.POST.getlist('alacarte_services')

        if not business_name or not email:
            return render(request, 'sales/create_customer.html', {
                'error': 'Business name and email are required.',
                'form_data': request.POST,
                'package_bundles': PACKAGE_BUNDLES,
                'alacarte_services': ALACARTE_SERVICES,
            })

        if User.objects.filter(email=email).exists():
            return render(request, 'sales/create_customer.html', {
                'error': f'An account with {email} already exists.',
                'form_data': request.POST,
                'package_bundles': PACKAGE_BUNDLES,
                'alacarte_services': ALACARTE_SERVICES,
            })

        temp_password = _generate_temp_password()

        user = User.objects.create_user(
            username=email,
            email=email,
            password=temp_password,
            is_active=True,
            first_name=owner_name.split()[0] if owner_name else '',
            last_name=' '.join(owner_name.split()[1:]) if len(owner_name.split()) > 1 else '',
        )

        # Determine account status
        if payment_method == 'no_payment' or plan == 'trial':
            account_status = 'trial'
        elif payment_method == 'send_link':
            account_status = 'pending_payment'
        else:
            account_status = 'pending_payment'

        # For a la carte, store 'custom' as tier
        tier = plan if plan != 'alacarte' else 'custom'

        profile = BusinessProfile.objects.create(
            user=user,
            business_name=business_name,
            owner_name=owner_name,
            email=email,
            phone=phone,
            city=city,
            state=state,
            subscription_tier=tier,
            account_status=account_status,
            onboarding_complete=True,
            created_by_sales=True,
            must_change_password=True,
            temp_password=temp_password,
        )

        # Send welcome email
        send_mail(
            'Welcome to SalesSignal AI!',
            f'Hi {owner_name or "there"},\n\n'
            f'Your SalesSignal AI account is ready!\n\n'
            f'Log in at: https://salessignalai.com/auth/login/\n'
            f'Email: {email}\n'
            f'Temporary password: {temp_password}\n\n'
            f'Please change your password after your first login.\n\n'
            f'— The SalesSignal AI Team\nsupport@salessignalai.com',
            settings.SUPPORT_EMAIL,
            [email],
            fail_silently=True,
        )

        # Build payment link if applicable
        payment_link = ''
        if payment_method == 'send_link':
            if plan in PACKAGE_BUNDLES:
                payment_link = PACKAGE_BUNDLES[plan]['payment_link']

        # Build selected service details for a la carte
        selected_service_details = []
        if selected_services:
            for svc_key in selected_services:
                svc = ALACARTE_SERVICES.get(svc_key)
                if svc:
                    selected_service_details.append({
                        'name': svc['name'],
                        'price': svc['price'],
                        'link': svc['link'],
                    })

        # Get plan display info
        plan_display = ''
        plan_price = ''
        if plan in PACKAGE_BUNDLES:
            plan_display = PACKAGE_BUNDLES[plan]['name']
            plan_price = f"Starting at ${PACKAGE_BUNDLES[plan]['price']:,}/mo"
        elif plan == 'trial':
            plan_display = 'Free Trial'
            plan_price = 'Full access — no payment'
        elif plan in ('alacarte', 'custom'):
            plan_display = 'Custom / A La Carte'
            plan_price = 'See selected services'

        return render(request, 'sales/create_customer_success.html', {
            'profile': profile,
            'temp_password': temp_password,
            'payment_link': payment_link,
            'payment_method': payment_method,
            'plan_display': plan_display,
            'plan_price': plan_price,
            'selected_services': selected_service_details,
            'setup_fee_link': SETUP_FEE_LINK,
        })

    return render(request, 'sales/create_customer.html', {
        'package_bundles': PACKAGE_BUNDLES,
        'alacarte_services': ALACARTE_SERVICES,
    })


# ─── Billing Page ─────────────────────────────────────────────────

@login_required
def billing_page(request):
    """Customer billing page."""
    profile = request.user.business_profile
    plan_name = dict(BusinessProfile.TIER_CHOICES).get(profile.subscription_tier, 'No Plan')
    plan_price = PLAN_PRICES.get(profile.subscription_tier, {}).get('price', 0)

    context = {
        'profile': profile,
        'plan_name': plan_name,
        'plan_price': plan_price,
        'has_stripe': bool(profile.stripe_customer_id),
        'stripe_key': settings.STRIPE_PUBLISHABLE_KEY,
    }
    return render(request, 'settings/billing.html', context)


# ─── Password Change (for sales-assisted first login) ────────────

@login_required
def force_password_change(request):
    """Force password change for sales-assisted accounts."""
    profile = request.user.business_profile
    if not profile.must_change_password:
        return redirect('dashboard_home')

    if request.method == 'POST':
        new_password = request.POST.get('new_password', '').strip()
        confirm = request.POST.get('confirm_password', '').strip()

        if len(new_password) < 8:
            return render(request, 'registration/force_password_change.html', {
                'error': 'Password must be at least 8 characters.',
            })
        if new_password != confirm:
            return render(request, 'registration/force_password_change.html', {
                'error': 'Passwords do not match.',
            })

        request.user.set_password(new_password)
        request.user.save()
        profile.must_change_password = False
        profile.temp_password = ''
        profile.save(update_fields=['must_change_password', 'temp_password'])

        login(request, request.user)
        return redirect('dashboard_home')

    return render(request, 'registration/force_password_change.html')
