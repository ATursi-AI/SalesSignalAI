"""
Settings views — Keywords, email preferences, and SMTP configuration.
"""
import json
import logging
import smtplib
from email.mime.text import MIMEText

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.models import UserKeyword

logger = logging.getLogger(__name__)


@login_required
def settings_page(request):
    profile = request.user.business_profile

    # Auto-populate defaults if user has a category but zero keywords
    if profile.service_category and not profile.keywords.exists():
        profile.populate_default_keywords()

    keywords = profile.keywords.all().order_by('source', 'keyword')

    active_count = keywords.filter(is_active=True).count()
    total_count = keywords.count()
    custom_count = keywords.filter(source='custom').count()

    context = {
        'profile': profile,
        'keywords': keywords,
        'active_count': active_count,
        'total_count': total_count,
        'custom_count': custom_count,
    }
    return render(request, 'settings/page.html', context)


@login_required
@require_POST
def keyword_toggle(request, keyword_id):
    """Toggle a keyword's active state."""
    try:
        kw = UserKeyword.objects.get(id=keyword_id, business=request.user.business_profile)
    except UserKeyword.DoesNotExist:
        return JsonResponse({'error': 'not_found'}, status=404)

    kw.is_active = not kw.is_active
    kw.save(update_fields=['is_active'])

    return JsonResponse({
        'id': kw.id,
        'keyword': kw.keyword,
        'is_active': kw.is_active,
    })


@login_required
@require_POST
def keyword_add(request):
    """Add a custom keyword."""
    profile = request.user.business_profile

    try:
        data = json.loads(request.body)
        keyword = data.get('keyword', '').strip()
    except (json.JSONDecodeError, AttributeError):
        keyword = request.POST.get('keyword', '').strip()

    if not keyword:
        return JsonResponse({'error': 'Keyword cannot be empty'}, status=400)

    if len(keyword) > 100:
        return JsonResponse({'error': 'Keyword too long (max 100 chars)'}, status=400)

    kw, created = UserKeyword.objects.get_or_create(
        business=profile,
        keyword=keyword,
        defaults={'source': 'custom', 'is_active': True},
    )

    if not created:
        # Already exists — just make sure it's active
        if not kw.is_active:
            kw.is_active = True
            kw.save(update_fields=['is_active'])

    return JsonResponse({
        'id': kw.id,
        'keyword': kw.keyword,
        'is_active': kw.is_active,
        'source': kw.source,
        'created': created,
    })


@login_required
@require_POST
def keyword_delete(request, keyword_id):
    """Delete a custom keyword. Category defaults can only be toggled, not deleted."""
    try:
        kw = UserKeyword.objects.get(id=keyword_id, business=request.user.business_profile)
    except UserKeyword.DoesNotExist:
        return JsonResponse({'error': 'not_found'}, status=404)

    if kw.source != 'custom':
        return JsonResponse({'error': 'Cannot delete category defaults. Use toggle instead.'}, status=400)

    kw.delete()
    return JsonResponse({'deleted': True})


@login_required
@require_POST
def save_email_prefs(request):
    """Save email style guide or email signature."""
    profile = request.user.business_profile

    try:
        data = json.loads(request.body)
        field = data.get('field', '')
        value = data.get('value', '')
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if field not in ('email_style_guide', 'email_signature'):
        return JsonResponse({'error': 'Invalid field'}, status=400)

    setattr(profile, field, value)
    profile.save(update_fields=[field])

    return JsonResponse({'saved': True})


@login_required
@require_POST
def keyword_reset_defaults(request):
    """Re-populate category defaults (won't duplicate existing ones)."""
    profile = request.user.business_profile
    created = profile.populate_default_keywords()
    return JsonResponse({'created': created})


@login_required
@require_POST
def save_smtp_settings(request):
    """Save custom SMTP configuration."""
    profile = request.user.business_profile

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    profile.use_custom_smtp = data.get('use_custom_smtp', False)
    profile.custom_smtp_host = data.get('custom_smtp_host', '').strip()
    profile.custom_smtp_port = int(data.get('custom_smtp_port', 587))
    profile.custom_smtp_username = data.get('custom_smtp_username', '').strip()
    profile.custom_from_email = data.get('custom_from_email', '').strip()
    profile.custom_from_name = data.get('custom_from_name', '').strip()

    # Only update password if a new one is provided (not the placeholder)
    password = data.get('custom_smtp_password', '')
    if password and password != '••••••••':
        profile.set_smtp_password(password)

    # Validate required fields when enabling
    if profile.use_custom_smtp:
        missing = []
        if not profile.custom_smtp_host:
            missing.append('SMTP Host')
        if not profile.custom_smtp_username:
            missing.append('Username')
        if not profile.custom_smtp_password_encrypted:
            missing.append('Password')
        if not profile.custom_from_email:
            missing.append('From Email')
        if missing:
            return JsonResponse({
                'error': f"Missing required fields: {', '.join(missing)}",
            }, status=400)

    profile.save(update_fields=[
        'use_custom_smtp', 'custom_smtp_host', 'custom_smtp_port',
        'custom_smtp_username', 'custom_smtp_password_encrypted',
        'custom_from_email', 'custom_from_name',
    ])

    return JsonResponse({'saved': True})


@login_required
@require_POST
def dismiss_welcome(request):
    """Mark the welcome banner as seen so it doesn't show again."""
    profile = request.user.business_profile
    profile.has_seen_welcome = True
    profile.save(update_fields=['has_seen_welcome'])
    return JsonResponse({'dismissed': True})


@login_required
@require_POST
def save_theme(request):
    """Save user's theme preference (dark/light)."""
    try:
        data = json.loads(request.body)
        theme = data.get('theme', 'dark')
    except (json.JSONDecodeError, AttributeError):
        theme = 'dark'

    if theme not in ('dark', 'light'):
        theme = 'dark'

    profile = request.user.business_profile
    profile.theme_preference = theme
    profile.save(update_fields=['theme_preference'])

    return JsonResponse({'saved': True, 'theme': theme})


@login_required
@require_POST
def send_test_email(request):
    """Send a test email through the customer's custom SMTP to verify it works."""
    profile = request.user.business_profile

    if not profile.custom_smtp_host or not profile.custom_smtp_password_encrypted:
        return JsonResponse({'error': 'SMTP not configured. Save settings first.'}, status=400)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        data = {}

    to_email = data.get('to_email', profile.email).strip()
    if not to_email:
        return JsonResponse({'error': 'No recipient email address.'}, status=400)

    password = profile.get_smtp_password()
    if not password:
        return JsonResponse({'error': 'Could not decrypt SMTP password.'}, status=400)

    from_name = profile.custom_from_name or profile.business_name
    from_addr = profile.custom_from_email

    msg = MIMEText(
        f"This is a test email from SalesSignal AI.\n\n"
        f"Your custom SMTP configuration is working correctly.\n\n"
        f"SMTP Host: {profile.custom_smtp_host}\n"
        f"SMTP Port: {profile.custom_smtp_port}\n"
        f"From: {from_name} <{from_addr}>\n\n"
        f"You can now send outreach campaigns through your own email server.",
        'plain',
    )
    msg['Subject'] = 'SalesSignal AI — SMTP Test Successful'
    msg['From'] = f'{from_name} <{from_addr}>'
    msg['To'] = to_email

    try:
        if profile.custom_smtp_port == 465:
            server = smtplib.SMTP_SSL(profile.custom_smtp_host, profile.custom_smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(profile.custom_smtp_host, profile.custom_smtp_port, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(profile.custom_smtp_username, password)
        server.sendmail(from_addr, [to_email], msg.as_string())
        server.quit()

        logger.info(f'SMTP test email sent for {profile.business_name} to {to_email}')
        return JsonResponse({'success': True, 'message': f'Test email sent to {to_email}'})

    except smtplib.SMTPAuthenticationError:
        return JsonResponse({'error': 'Authentication failed. Check username and password.'}, status=400)
    except smtplib.SMTPConnectError:
        return JsonResponse({'error': f'Could not connect to {profile.custom_smtp_host}:{profile.custom_smtp_port}'}, status=400)
    except TimeoutError:
        return JsonResponse({'error': f'Connection timed out to {profile.custom_smtp_host}:{profile.custom_smtp_port}'}, status=400)
    except Exception as e:
        logger.error(f'SMTP test failed for {profile.business_name}: {e}')
        return JsonResponse({'error': str(e)}, status=400)
