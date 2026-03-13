from core.models.outreach import OutreachEmail, OutreachProspect


def crm_counts(request):
    """Provide CRM counts (inbox unread) for sidebar badges."""
    if not request.user.is_authenticated:
        return {}

    bp = getattr(request.user, 'business_profile', None)
    if not bp:
        return {}

    # Legacy emails
    legacy_count = OutreachEmail.objects.filter(
        campaign__business=bp,
        status='replied',
    ).count()

    # New system prospects
    prospect_count = OutreachProspect.objects.filter(
        campaign__business=bp,
        status__in=['replied', 'interested'],
    ).count()

    unread = legacy_count + prospect_count

    return {
        'inbox_unread_count': unread if unread > 0 else 0,
    }
