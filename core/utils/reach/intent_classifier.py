"""
AI-powered intent classifier for social media leads.

Uses Gemini 2.5 Flash-Lite (free tier) to determine whether a social media
post is a genuine service request or noise (discussion, advice, job posting,
unrelated content).

This runs at lead ingest time for social media sources AND can be called
on-demand to reclassify existing leads.
"""
import json
import logging
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Social media platforms that need intent classification
# ─────────────────────────────────────────────────────────────
SOCIAL_PLATFORMS = {
    'reddit', 'nextdoor', 'facebook', 'twitter', 'tiktok',
    'threads', 'quora', 'instagram', 'fb_marketplace',
    'craigslist', 'patch', 'houzz', 'alignable', 'biggerpockets',
    'citydata', 'local_news', 'parent_community', 'trade_forum',
}

# ─────────────────────────────────────────────────────────────
# Blue-collar / home service trades — our customer profile
# ─────────────────────────────────────────────────────────────
TARGET_TRADES = [
    'plumber', 'electrician', 'HVAC', 'roofer', 'painter', 'landscaper',
    'pest control', 'exterminator', 'handyman', 'general contractor',
    'cleaner', 'house cleaning', 'carpet cleaning', 'mover', 'locksmith',
    'tree service', 'concrete', 'masonry', 'fencing', 'gutter',
    'pressure washing', 'flooring', 'drywall', 'siding', 'window replacement',
    'garage door', 'appliance repair', 'water damage restoration',
    'mold remediation', 'foundation repair', 'paving', 'asphalt',
    'pool service', 'septic', 'junk removal', 'snow removal',
    'deck building', 'bathroom remodel', 'kitchen remodel',
    'insulation', 'solar', 'fire restoration', 'demolition',
]

CLASSIFIER_PROMPT = """You are a lead quality classifier for SalesSignalAI, a company that sells marketing services to local blue-collar trade businesses (plumbers, electricians, HVAC techs, roofers, painters, cleaners, pest control, landscapers, contractors, etc.).

Analyze this social media post and classify it. Your job is to determine if the poster is ACTIVELY LOOKING TO HIRE a local service provider.

POST TITLE: {title}
POST CONTENT: {content}
PLATFORM: {platform}
SUBREDDIT/GROUP: {source}

Classify as exactly ONE of:
- REAL_LEAD: Person is actively seeking to hire a service provider, asking for recommendations, requesting quotes, or describing a problem that needs professional help
- MENTION_ONLY: Post mentions a trade/service but the person is NOT looking to hire (e.g., sharing an experience, asking a general question, discussing costs hypothetically)
- FALSE_POSITIVE: Post has nothing to do with hiring a service provider (legal questions, personal problems, news, community events, unrelated discussions)
- JOB_POSTING: Someone is offering employment or looking to hire employees (not hire a service)
- ADVICE_GIVING: A professional giving tips or advice, not someone seeking a service

Respond in this exact JSON format (no markdown, no code fences):
{{"classification": "REAL_LEAD|MENTION_ONLY|FALSE_POSITIVE|JOB_POSTING|ADVICE_GIVING", "confidence": 0.0-1.0, "service_type": "the specific trade/service if REAL_LEAD else empty string", "reasoning": "one sentence explanation"}}"""


def _call_gemini_classifier(prompt):
    """Call Gemini Flash-Lite for classification. Returns parsed JSON or None."""
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model_name = 'gemini-2.5-flash-lite'
    if not api_key:
        logger.warning('[IntentClassifier] GEMINI_API_KEY not configured')
        return None

    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent'
    headers = {'Content-Type': 'application/json'}
    params = {'key': api_key}
    body = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {
            'maxOutputTokens': 256,
            'temperature': 0.1,  # Low temp for consistent classification
        },
    }

    try:
        resp = requests.post(url, json=body, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f'[IntentClassifier] Gemini API error {resp.status_code}: {resp.text[:200]}')
            return None

        data = resp.json()
        candidates = data.get('candidates', [])
        if not candidates:
            return None

        text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
        if not text:
            return None

        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith('```'):
            text = re.sub(r'^```\w*\n?', '', text)
            text = re.sub(r'\n?```$', '', text)
            text = text.strip()

        return json.loads(text)

    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        logger.error(f'[IntentClassifier] Error: {e}')
        return None


def classify_lead_intent(title, content, platform, source=''):
    """
    Classify a social media post's intent.

    Args:
        title: Post title
        content: Post body/content
        platform: Platform name (reddit, nextdoor, etc.)
        source: Subreddit, group name, or community name

    Returns:
        dict with keys: classification, confidence, service_type, reasoning
        or None if classification failed
    """
    # Truncate long content to save tokens
    display_content = content[:800] if content else ''
    display_title = title[:200] if title else ''

    prompt = CLASSIFIER_PROMPT.format(
        title=display_title,
        content=display_content,
        platform=platform,
        source=source or 'unknown',
    )

    result = _call_gemini_classifier(prompt)
    if not result:
        return None

    # Normalize classification to our model field values
    classification_map = {
        'REAL_LEAD': 'real_lead',
        'MENTION_ONLY': 'mention_only',
        'FALSE_POSITIVE': 'false_positive',
        'JOB_POSTING': 'job_posting',
        'ADVICE_GIVING': 'advice_giving',
    }

    raw_class = result.get('classification', 'FALSE_POSITIVE').upper()
    normalized = classification_map.get(raw_class, 'false_positive')

    return {
        'classification': normalized,
        'confidence': min(1.0, max(0.0, float(result.get('confidence', 0.5)))),
        'service_type': result.get('service_type', ''),
        'reasoning': result.get('reasoning', ''),
    }


def classify_lead(lead):
    """
    Classify an existing Lead record in-place.
    Updates the lead's intent fields and saves.

    Returns the classification result dict or None.
    """
    # Extract title from content (Reddit-style: title is first line or in raw_data)
    title = ''
    if lead.raw_data:
        title = lead.raw_data.get('title', '') or lead.raw_data.get('post_title', '')
    if not title:
        # Use first line of content as title
        lines = lead.source_content.split('\n')
        title = lines[0][:200] if lines else ''

    source = ''
    if lead.raw_data:
        source = (lead.raw_data.get('subreddit', '')
                  or lead.raw_data.get('group_name', '')
                  or lead.raw_data.get('neighborhood', '')
                  or lead.raw_data.get('community', ''))

    result = classify_lead_intent(
        title=title,
        content=lead.source_content,
        platform=lead.platform,
        source=source,
    )

    if result:
        lead.intent_classification = result['classification']
        lead.intent_confidence = result['confidence']
        lead.intent_service_detected = result.get('service_type', '')[:100]
        lead.intent_classified_at = timezone.now()
        lead.intent_classified_by = 'ai'
        lead.save(update_fields=[
            'intent_classification', 'intent_confidence',
            'intent_service_detected', 'intent_classified_at',
            'intent_classified_by',
        ])
        logger.info(
            f'[IntentClassifier] Lead #{lead.id}: {result["classification"]} '
            f'(conf={result["confidence"]:.2f}) — {result.get("reasoning", "")[:80]}'
        )

    return result


def classify_leads_bulk(queryset=None, limit=100):
    """
    Classify multiple unclassified social media leads.
    Default: classify up to 100 unclassified social leads.

    Returns dict with counts: {classified, real_leads, false_positives, errors}
    """
    from core.models.leads import Lead

    if queryset is None:
        queryset = Lead.objects.filter(
            platform__in=SOCIAL_PLATFORMS,
            intent_classification='not_classified',
        ).order_by('-discovered_at')[:limit]

    stats = {'classified': 0, 'real_leads': 0, 'false_positives': 0,
             'mention_only': 0, 'job_posting': 0, 'advice_giving': 0, 'errors': 0}

    for lead in queryset:
        result = classify_lead(lead)
        if result:
            stats['classified'] += 1
            cls = result['classification']
            if cls == 'real_lead':
                stats['real_leads'] += 1
            elif cls == 'false_positive':
                stats['false_positives'] += 1
            elif cls in stats:
                stats[cls] += 1
        else:
            stats['errors'] += 1

    logger.info(f'[IntentClassifier] Bulk run: {stats}')
    return stats


def needs_classification(platform):
    """Check if a platform's leads should be AI-classified."""
    return platform in SOCIAL_PLATFORMS
