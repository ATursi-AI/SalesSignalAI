from django.db.models import Q

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


def lead_sidebar_counts(request):
    """Provide unreviewed lead counts by source_group and source_type for sidebar."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return {}

    from core.models.leads import Lead

    base = Lead.objects.filter(review_status='unreviewed')

    # Source type counts
    from django.db.models import Count
    type_counts = dict(
        base.values_list('source_type')
        .annotate(c=Count('id'))
        .values_list('source_type', 'c')
    )

    # Group counts
    group_counts = dict(
        base.values_list('source_group')
        .annotate(c=Count('id'))
        .values_list('source_group', 'c')
    )

    # Urgency counts
    urgency_counts = dict(
        base.values_list('urgency_level')
        .annotate(c=Count('id'))
        .values_list('urgency_level', 'c')
    )

    return {
        'lead_type_counts': type_counts,
        'lead_group_counts': group_counts,
        'lead_urgency_counts': urgency_counts,
        'lead_total_unreviewed': sum(group_counts.values()),
    }
