from django import template
from django.utils import timezone

register = template.Library()

PLATFORM_COLORS = {
    'craigslist': '#7B2FBE',
    'reddit': '#FF4500',
    'patch': '#0EA5E9',
    'google_qna': '#4285F4',
    'google_reviews': '#FBBC04',
    'yelp_review': '#D32323',
    'angi_review': '#39B54A',
    'houzz': '#4DBC5B',
    'alignable': '#1B3A5C',
    'thumbtack': '#009FD9',
    'porch': '#00BFA5',
    'citydata': '#2D5F8A',
    'biggerpockets': '#F57C00',
    'local_news': '#607D8B',
    'parent_community': '#E91E63',
    'trade_forum': '#795548',
    'facebook': '#1877F2',
    'nextdoor': '#8ED500',
    'twitter': '#1DA1F2',
    'manual': '#6B7280',
}

PLATFORM_LABELS = {
    'craigslist': 'Craigslist',
    'reddit': 'Reddit',
    'patch': 'Patch',
    'google_qna': 'Google Q&A',
    'google_reviews': 'Google Reviews',
    'yelp_review': 'Yelp',
    'angi_review': 'Angi',
    'houzz': 'Houzz',
    'alignable': 'Alignable',
    'thumbtack': 'Thumbtack',
    'porch': 'Porch',
    'citydata': 'City-Data',
    'biggerpockets': 'BiggerPockets',
    'local_news': 'Local News',
    'parent_community': 'Parent Community',
    'trade_forum': 'Trade Forum',
    'facebook': 'Facebook',
    'nextdoor': 'Nextdoor',
    'twitter': 'Twitter/X',
    'manual': 'Manual',
}


@register.filter
def platform_color(platform):
    return PLATFORM_COLORS.get(platform, '#6B7280')


@register.filter
def platform_label(platform):
    return PLATFORM_LABELS.get(platform, platform)


@register.filter
def urgency_class(level):
    mapping = {
        'hot': 'urgency-hot',
        'warm': 'urgency-warm',
        'new': 'urgency-new',
        'stale': 'urgency-stale',
    }
    return mapping.get(level, 'urgency-new')


@register.filter
def get_stage(pipeline_data, stage_key):
    """Get contacts for a pipeline stage from the pipeline_data dict."""
    return pipeline_data.get(stage_key, [])


@register.filter
def time_ago(dt):
    if not dt:
        return ''
    now = timezone.now()
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return 'just now'
    elif seconds < 3600:
        mins = int(seconds / 60)
        return f'{mins}m ago'
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f'{hours}h ago'
    else:
        days = int(seconds / 86400)
        return f'{days}d ago'


@register.filter
def get_item(dictionary, key):
    """Lookup a dict value by key in templates: {{ mydict|get_item:key }}"""
    if isinstance(dictionary, dict):
        return dictionary.get(str(key), [])
    return []
