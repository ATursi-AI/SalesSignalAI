"""
Lead processing pipeline shared by all monitors.
Handles keyword matching, service category detection, location extraction,
lead creation with deduplication, and assignment to matching businesses.

Uses each business's personalized UserKeyword list (active only) for matching.
Falls back to ServiceCategory defaults if no UserKeywords are configured.
"""
import hashlib
import logging
import re
from datetime import timedelta

from django.utils import timezone

from core.models.business import ServiceCategory, BusinessProfile, UserKeyword
from core.models.leads import Lead, LeadAssignment
from core.utils.location import extract_location, is_in_service_area

logger = logging.getLogger(__name__)


def compute_content_hash(platform, url, content):
    """Generate a SHA-256 hash for deduplication."""
    raw = f"{platform}|{url}|{content}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def match_keywords(text, categories=None):
    """
    Match text against ServiceCategory.default_keywords.
    Returns list of (category, matched_keywords, score) sorted by score desc.
    """
    if not text:
        return []

    text_lower = text.lower()

    if categories is None:
        categories = ServiceCategory.objects.filter(is_active=True)

    results = []
    for cat in categories:
        keywords = cat.default_keywords or []
        # Also include subcategory keywords
        for sub in cat.subcategories.all():
            keywords.extend(sub.additional_keywords or [])

        matched = []
        for kw in keywords:
            kw_lower = kw.lower()
            # Multi-word keywords: exact phrase match
            # Single words: word boundary match
            if ' ' in kw_lower:
                if kw_lower in text_lower:
                    matched.append(kw)
            else:
                # Simple word boundary check
                import re
                if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
                    matched.append(kw)

        if matched:
            # Score: number of matched keywords, weighted by specificity
            score = len(matched)
            # Bonus for multi-word keyword matches (more specific)
            score += sum(0.5 for kw in matched if ' ' in kw)
            results.append((cat, matched, score))

    results.sort(key=lambda x: x[2], reverse=True)
    return results


def calculate_urgency(posted_at):
    """Calculate urgency level based on post age."""
    if not posted_at:
        return 'new', 50

    now = timezone.now()
    age = now - posted_at

    if age < timedelta(hours=1):
        return 'hot', 90
    elif age < timedelta(hours=4):
        return 'warm', 70
    elif age < timedelta(hours=24):
        return 'new', 50
    else:
        return 'stale', 20


def process_lead(platform, source_url, content, author='', posted_at=None, raw_data=None):
    """
    Full lead processing pipeline:
    1. Deduplicate via content hash
    2. Extract location
    3. Match keywords to detect service category
    4. Calculate urgency
    5. Create Lead record
    6. Assign to matching businesses

    Returns (lead, created, num_assignments) or (None, False, 0) if duplicate.
    """
    content_hash = compute_content_hash(platform, source_url, content)

    # Check for duplicates
    if Lead.objects.filter(content_hash=content_hash).exists():
        logger.debug(f"Duplicate lead skipped: {source_url}")
        return None, False, 0

    # Extract location
    location = extract_location(content)

    # Match keywords
    keyword_matches = match_keywords(content)
    best_category = None
    matched_keywords = []
    if keyword_matches:
        best_category, matched_keywords, _ = keyword_matches[0]

    # Calculate urgency
    urgency_level, urgency_score = calculate_urgency(posted_at)

    # Create Lead
    lead = Lead.objects.create(
        platform=platform,
        source_url=source_url,
        source_content=content,
        source_author=author,
        source_posted_at=posted_at,
        detected_location=location.get('display', ''),
        detected_zip=location.get('zip_code', ''),
        detected_service_type=best_category,
        matched_keywords=matched_keywords,
        urgency_score=urgency_score,
        urgency_level=urgency_level,
        content_hash=content_hash,
        raw_data=raw_data or {},
    )

    logger.info(f"Created lead #{lead.id}: [{urgency_level.upper()}] {platform} - {content[:60]}")

    # Assign to matching businesses
    num_assignments = assign_lead_to_businesses(lead, location, best_category)

    return lead, True, num_assignments


def matches_business_keywords(text, business):
    """
    Check if text matches a business's personalized keyword list.
    Uses UserKeyword (active only). Falls back to ServiceCategory defaults
    if no UserKeywords are configured.

    Returns (matched: bool, matched_keywords: list).
    """
    text_lower = text.lower()

    # Get active keywords for this business
    active_keywords = business.get_active_keywords()

    # Fallback to category defaults if no UserKeywords exist
    if not active_keywords and business.service_category:
        active_keywords = list(business.service_category.default_keywords or [])
        for sub in business.service_category.subcategories.all():
            active_keywords.extend(sub.additional_keywords or [])

    if not active_keywords:
        # No keywords at all — category match is sufficient
        return True, []

    matched = []
    for kw in active_keywords:
        kw_lower = kw.lower()
        if ' ' in kw_lower:
            if kw_lower in text_lower:
                matched.append(kw)
        else:
            if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
                matched.append(kw)

    return len(matched) > 0, matched


def assign_lead_to_businesses(lead, location, service_category):
    """
    Find matching businesses and create LeadAssignment records.
    Matches on: service category + geography + personalized keywords.
    """
    businesses = BusinessProfile.objects.filter(
        is_active=True,
        onboarding_complete=True,
    ).select_related('service_category').prefetch_related('keywords')

    # If we detected a service category, prefer businesses in that category
    # But also include businesses with no category (they get all leads)
    if service_category:
        businesses = businesses.filter(
            service_category__in=[service_category, None]
        )

    assignments_created = 0
    for bp in businesses:
        # Check geographic match
        if not is_in_service_area(location, bp):
            continue

        # Check keyword match against business's personalized keywords
        kw_match, kw_matched = matches_business_keywords(lead.source_content, bp)
        if not kw_match:
            logger.debug(
                f"Lead #{lead.id} skipped for {bp.business_name}: no keyword match"
            )
            continue

        # Avoid duplicate assignments
        _, created = LeadAssignment.objects.get_or_create(
            lead=lead,
            business=bp,
            defaults={'status': 'new'},
        )
        if created:
            assignments_created += 1
            logger.info(f"Assigned lead #{lead.id} to {bp.business_name} (matched: {kw_matched[:3]})")

    if assignments_created == 0:
        logger.warning(f"Lead #{lead.id} matched no businesses")

    return assignments_created
