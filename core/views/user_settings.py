"""
Settings views — My Keywords management.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.models import UserKeyword


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
