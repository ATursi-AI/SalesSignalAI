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

# ─────────────────────────────────────────────────────────────
# Weak single-word keywords that must appear in a qualifying
# phrase to count as a match.  If a keyword appears here, a
# bare word-boundary hit is ignored — only the compound phrases
# listed will count.
# ─────────────────────────────────────────────────────────────
KEYWORD_PHRASES = {
    # Keyword  →  list of qualifying phrases (checked case-insensitively)
    'moving': [
        'moving company', 'need movers', 'hiring movers', 'moving truck',
        'moving help', 'moving service', 'local movers', 'moving quote',
        'moving cost', 'help me move', 'moving out', 'moving in',
        'moving to', 'moving from', 'interstate move', 'long distance move',
    ],
    'stone': [
        'stone work', 'stonework', 'stone patio', 'stone wall', 'natural stone',
        'stone veneer', 'stone mason', 'stone steps', 'stone walkway',
        'flagstone', 'bluestone', 'stone repair',
    ],
    'addition': [
        'home addition', 'room addition', 'building addition', 'house addition',
        'add an addition', 'build an addition', 'addition to my house',
        'addition to my home', 'garage addition',
    ],
    'maintenance': [
        'home maintenance', 'property maintenance', 'maintenance service',
        'maintenance man', 'maintenance person', 'building maintenance',
        'maintenance work', 'hvac maintenance', 'lawn maintenance',
    ],
    'painting': [
        'house painting', 'interior painting', 'exterior painting',
        'painter needed', 'painting contractor', 'painting company',
        'need a painter', 'hire a painter', 'paint my house', 'paint my room',
        'painting quote', 'painting estimate', 'wall painting', 'trim painting',
        'painting service', 'recommend a painter', 'looking for a painter',
    ],
    'lighting': [
        'lighting installation', 'lighting electrician', 'recessed lighting',
        'lighting fixture', 'install lighting', 'outdoor lighting',
        'landscape lighting', 'lighting upgrade', 'led lighting',
        'lighting repair', 'track lighting',
    ],
    'panel': [
        'electrical panel', 'breaker panel', 'panel upgrade', 'panel box',
        'fuse panel', 'sub panel', 'subpanel', 'panel replacement',
        'service panel', '200 amp panel',
    ],
    'leak': [
        'plumbing leak', 'pipe leak', 'water leak', 'roof leak', 'leak repair',
        'gas leak', 'faucet leak', 'toilet leak', 'slab leak', 'leaking pipe',
        'leaking faucet', 'leaking toilet', 'leaking roof', 'leaking water',
        'fix a leak', 'fix the leak', 'stop a leak',
    ],
    'drain': [
        'drain cleaning', 'clogged drain', 'drain repair', 'drain line',
        'drain snake', 'slow drain', 'drain backup', 'drain clog',
        'floor drain', 'shower drain', 'blocked drain', 'drain service',
    ],
    'pipe': [
        'pipe repair', 'pipe leak', 'burst pipe', 'frozen pipe', 'pipe replacement',
        'broken pipe', 'pipe fitting', 'copper pipe', 'pvc pipe',
        'leaking pipe', 'pipe burst', 'repiping', 'pipe insulation',
    ],
    'outlet': [
        'electrical outlet', 'power outlet', 'outlet repair', 'gfci outlet',
        'install outlet', 'outlet replacement', 'add outlet', 'dead outlet',
    ],
    'tile': [
        'tile installation', 'tile work', 'floor tile', 'bathroom tile',
        'shower tile', 'tile repair', 'tile floor', 'backsplash tile',
        'tile contractor', 'retile', 'tile guy', 'subway tile',
    ],
    'deck': [
        'deck building', 'deck repair', 'deck staining', 'build a deck',
        'deck contractor', 'deck replacement', 'composite deck',
        'deck installation', 'wood deck', 'deck railing',
    ],
    'patio': [
        'patio installation', 'patio repair', 'build a patio', 'paver patio',
        'patio contractor', 'patio design', 'concrete patio', 'stone patio',
        'patio cover',
    ],
    'clogged': [
        'clogged drain', 'clogged toilet', 'clogged pipe', 'clogged sink',
        'clogged sewer', 'clogged shower',
    ],
    'insulation': [
        'attic insulation', 'wall insulation', 'insulation contractor',
        'spray foam insulation', 'blown-in insulation', 'insulation install',
        'insulation upgrade', 'home insulation', 'insulation company',
    ],
    'concrete': [
        'concrete work', 'concrete repair', 'concrete slab', 'pour concrete',
        'concrete contractor', 'concrete driveway', 'concrete patio',
        'concrete sidewalk', 'concrete steps', 'concrete foundation',
        'concrete crack', 'stamped concrete',
    ],
    'brick': [
        'brick work', 'brickwork', 'brick repair', 'brick wall', 'brick patio',
        'brick pointing', 'tuckpointing', 'brick mason', 'brick layer',
        'brick steps', 'brick house',
    ],
    'solar': [
        'solar panel', 'solar installation', 'solar energy', 'solar power',
        'solar company', 'solar quote', 'go solar', 'solar roof',
        'solar contractor', 'solar installer',
    ],
    'heating': [
        'heating repair', 'heating system', 'heating company', 'heating unit',
        'heating service', 'no heating', 'heating broke', 'central heating',
        'heating installation', 'heating contractor',
    ],
    'cooling': [
        'cooling system', 'cooling repair', 'cooling service', 'cooling unit',
        'cooling company', 'cooling contractor',
    ],
    'construction': [
        'construction company', 'construction contractor', 'construction project',
        'construction work', 'new construction', 'construction crew',
        'construction estimate', 'construction quote',
    ],
    'renovation': [
        'home renovation', 'house renovation', 'kitchen renovation',
        'bathroom renovation', 'renovation contractor', 'renovation project',
        'renovation company', 'renovation cost', 'renovation quote',
    ],
    'remodel': [
        'home remodel', 'house remodel', 'kitchen remodel', 'bathroom remodel',
        'basement remodel', 'remodel contractor', 'remodel project',
        'remodel company', 'remodel cost',
    ],
    'wiring': [
        'electrical wiring', 'house wiring', 'home wiring', 'wiring upgrade',
        'rewiring', 'wiring repair', 'wiring issue', 'wiring problem',
        'knob and tube', 'romex',
    ],
    'mold': [
        'mold removal', 'mold remediation', 'mold testing', 'mold inspection',
        'black mold', 'mold problem', 'mold issue', 'mold in',
        'mold damage', 'mold company',
    ],
    'fencing': [
        'fence installation', 'fence repair', 'fence company', 'privacy fence',
        'fence contractor', 'build a fence', 'new fence', 'wood fence',
        'vinyl fence', 'chain link fence', 'fence quote',
    ],
    'entertainment': [
        'event entertainment', 'party entertainment', 'wedding entertainment',
        'live entertainment', 'hire entertainment',
    ],
    'bookkeeping': [
        'bookkeeping service', 'bookkeeper needed', 'need a bookkeeper',
        'looking for a bookkeeper', 'bookkeeping help', 'bookkeeping company',
        'hire a bookkeeper', 'recommend a bookkeeper', 'small business bookkeeping',
    ],
    'staining': [
        'deck staining', 'wood staining', 'fence staining', 'stain the deck',
        'stain the fence', 'staining service', 'staining contractor',
        'floor staining', 'cabinet staining', 'furniture staining',
    ],
    'sidewalk': [
        'sidewalk repair', 'sidewalk replacement', 'pour sidewalk',
        'sidewalk crack', 'sidewalk contractor', 'new sidewalk',
        'fix sidewalk', 'broken sidewalk', 'sidewalk leveling',
    ],
    'foundation': [
        'foundation repair', 'foundation crack', 'foundation issue',
        'foundation problem', 'foundation contractor', 'foundation inspection',
        'foundation settling', 'foundation work', 'slab foundation',
    ],
    'transmission': [
        'transmission repair', 'transmission shop', 'transmission fluid',
        'transmission rebuild', 'transmission replacement', 'transmission problem',
        'transmission issue', 'transmission service', 'transmission mechanic',
    ],
    'parking lot': [
        'parking lot repair', 'parking lot paving', 'parking lot seal',
        'parking lot striping', 'parking lot resurfac', 'repave parking lot',
        'pave parking lot',
    ],
}

# ─────────────────────────────────────────────────────────────
# Keywords to remove entirely — too ambiguous in all contexts
# ─────────────────────────────────────────────────────────────
REMOVED_KEYWORDS = {
    'fix it',       # matches any discussion of fixing anything
    'contacts',     # matches eyewear / phone contacts
    'glasses',      # matches eyeglasses discussions
    'vision',       # too generic
    'packing',      # too generic (packing for trips, etc)
}

# ─────────────────────────────────────────────────────────────
# Negative keywords per category slug — if any appear in the
# text, the category is disqualified even if keywords matched
# ─────────────────────────────────────────────────────────────
NEGATIVE_KEYWORDS = {
    'plumbing': ['car', 'dealership', 'ev', 'tesla', 'bitcoin', 'crypto'],
    'painting': ['art', 'canvas', 'gallery', 'museum', 'watercolor', 'acrylic paint', 'oil painting'],
    'tree-service': ['cuttings', 'propagat', 'christmas tree', 'family tree'],
    'moving': ['emotionally', 'grocery', 'roth', 'ira', '401k', 'hysa', 'bank account',
               'savings account', 'investment', 'stock market', 'portfolio'],
    'concrete-masonry': ['custody', 'jewelry', 'jeweler', 'estate jewel', 'gemstone',
                         'engagement ring', 'diamond'],
    'electrical': ['car', 'ev charger', 'electric vehicle', 'guitar', 'amp ', 'amplifier'],
    'therapist-counselor': ['massage', 'physical therap'],
    'florist': ['flour', 'bakery', 'baking'],
    'veterinarian': ['nutritionist'],
    'real-estate-agent': ['commission rate', 'commission split', 'nar settlement',
                          'broker fee', 'agent to agent', 'listing agent commission'],
    'paving-asphalt': ['parking ticket', 'parking meter', 'parking garage',
                       'street parking', 'parking permit'],
    'physical-therapy': ['physical therapist career', 'become a pt', 'pt school',
                         'dpt program'],
    'insurance-agent': ['health insurance marketplace', 'obamacare', 'medicaid',
                        'medicare part'],
}

# ─────────────────────────────────────────────────────────────
# Strong intent phrases — if any of these appear in text,
# a single keyword match is sufficient (high confidence)
# ─────────────────────────────────────────────────────────────
STRONG_INTENT_PATTERNS = [
    r'recommend\w* (?:a |an |me a |me an )',
    r'looking for (?:a |an )',
    r'need (?:a |an )',
    r'hire (?:a |an )',
    r'hiring (?:a |an )',
    r'anyone know (?:a |an )',
    r'can anyone recommend',
    r'who do you (?:use|recommend|call)',
    r'know (?:a |of a |any )good',
    r'suggest (?:a |an )',
    r'best .{0,20} in (?:the |my )',
    r'affordable .{0,15} near',
    r'quote for',
    r'estimate for',
    r'(?:free |get a )(?:quote|estimate)',
]

_strong_intent_re = re.compile(
    '|'.join(STRONG_INTENT_PATTERNS), re.IGNORECASE
)


def compute_content_hash(platform, url, content):
    """Generate a SHA-256 hash for deduplication."""
    raw = f"{platform}|{url}|{content}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _keyword_matches_text(kw, text_lower):
    """
    Check if a keyword matches text, applying phrase restrictions
    for weak single-word keywords.
    Returns True if the keyword matches.
    """
    kw_lower = kw.lower()

    # Skip entirely removed keywords
    if kw_lower in REMOVED_KEYWORDS:
        return False

    # Multi-word keywords: whole-word phrase match (word boundaries)
    if ' ' in kw_lower:
        return bool(re.search(
            r'\b' + re.escape(kw_lower) + r'\b', text_lower
        ))

    # Single-word keyword: check if it requires a qualifying phrase
    if kw_lower in KEYWORD_PHRASES:
        phrases = KEYWORD_PHRASES[kw_lower]
        return any(
            re.search(r'\b' + re.escape(p) + r'\b', text_lower)
            for p in phrases
        )

    # Regular single-word keyword: word boundary match
    return bool(re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower))


def _check_negative_keywords(text_lower, category_slug):
    """Return True if text contains negative keywords for this category."""
    negatives = NEGATIVE_KEYWORDS.get(category_slug, [])
    if not negatives:
        return False
    for neg in negatives:
        neg_lower = neg.lower()
        if neg_lower in text_lower:
            return True
    return False


def match_keywords(text, categories=None):
    """
    Match text against ServiceCategory.default_keywords.

    Returns list of (category, matched_keywords, score, confidence)
    sorted by score desc.  confidence is 'high' or 'low'.

    High confidence = 2+ keyword matches OR 1 match + strong intent phrase.
    Low confidence  = only 1 weak keyword match with no intent signal.
    """
    if not text:
        return []

    text_lower = text.lower()
    has_strong_intent = bool(_strong_intent_re.search(text))

    if categories is None:
        categories = ServiceCategory.objects.filter(
            is_active=True
        ).prefetch_related('subcategories')

    results = []
    for cat in categories:
        # Check negative keywords first
        if _check_negative_keywords(text_lower, cat.slug):
            continue

        keywords = list(cat.default_keywords or [])
        for sub in cat.subcategories.all():
            keywords.extend(sub.additional_keywords or [])

        matched = []
        for kw in keywords:
            if _keyword_matches_text(kw, text_lower):
                matched.append(kw)

        if matched:
            # Score: number of matched keywords, weighted by specificity
            score = len(matched)
            # Bonus for multi-word keyword matches (more specific)
            score += sum(0.5 for kw in matched if ' ' in kw)

            # Confidence assessment
            has_phrase_match = any(' ' in kw for kw in matched)
            if len(matched) >= 2 or has_phrase_match or has_strong_intent:
                confidence = 'high'
            else:
                confidence = 'low'

            results.append((cat, matched, score, confidence))

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


def process_lead(platform, source_url, content, author='', posted_at=None,
                  raw_data=None, **extra_fields):
    """
    Full lead processing pipeline:
    1. Deduplicate via content hash
    2. Extract location
    3. Match keywords to detect service category
    4. Calculate urgency
    5. Create Lead record
    6. Assign to matching businesses

    Optional extra_fields kwargs are set directly on the Lead after creation.
    Supported: state, region, source_group, source_type, contact_name,
    contact_phone, contact_email, contact_business, contact_address.

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
    confidence = 'low'
    if keyword_matches:
        best_category, matched_keywords, _, confidence = keyword_matches[0]

    # Calculate urgency
    urgency_level, urgency_score = calculate_urgency(posted_at)

    # Separate known Lead fields from extra_fields
    lead_kwargs = {}
    valid_extra = {
        'state', 'region', 'source_group', 'source_type',
        'contact_name', 'contact_phone', 'contact_email',
        'contact_business', 'contact_address', 'event_date',
    }
    for k, v in extra_fields.items():
        if k in valid_extra and v:
            lead_kwargs[k] = v

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
        confidence=confidence,
        content_hash=content_hash,
        raw_data=raw_data or {},
        **lead_kwargs,
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
        if _keyword_matches_text(kw, text_lower):
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
        assignment, created = LeadAssignment.objects.get_or_create(
            lead=lead,
            business=bp,
            defaults={'status': 'new'},
        )
        if created:
            assignments_created += 1
            logger.info(f"Assigned lead #{lead.id} to {bp.business_name} (matched: {kw_matched[:3]})")
            # Auto-create CRM Contact
            _create_contact_from_assignment(assignment, lead, bp)

    if assignments_created == 0:
        logger.warning(f"Lead #{lead.id} matched no businesses")

    return assignments_created


def _create_contact_from_assignment(assignment, lead, business):
    """Auto-create a CRM Contact when a lead is assigned to a business."""
    from core.models.crm import Contact, Activity

    # Determine contact name from lead author or content
    name = lead.source_author.strip() if lead.source_author else ''
    if not name or name.lower() in ('', '[deleted]', 'anonymous', 'unknown'):
        name = f'{lead.get_platform_display()} Lead #{lead.id}'

    # Determine service needed
    service = ''
    if lead.detected_service_type:
        service = lead.detected_service_type.name

    contact, created = Contact.objects.get_or_create(
        business=business,
        source_lead=lead,
        defaults={
            'name': name[:200],
            'source': 'lead',
            'source_platform': lead.platform,
            'source_assignment': assignment,
            'service_needed': service,
            'pipeline_stage': 'new',
        },
    )

    if created:
        Activity.objects.create(
            contact=contact,
            activity_type='lead_found',
            description=f'Found on {lead.get_platform_display()} — {lead.source_content[:100]}',
        )
        logger.info(f"Created CRM contact '{contact.name}' from lead #{lead.id}")
