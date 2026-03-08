"""
Angi (formerly Angie's List / HomeAdvisor) review monitor for SalesSignal AI.
Scrapes public reviews on competitor listings. Flags 1-2 star reviews
as opportunities. Uses Claude API to analyze if reviewer is seeking alternative.
"""
import hashlib
import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from core.models.competitors import TrackedCompetitor, CompetitorReview
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.angi.com'

# Service type URL slugs for Angi directory browsing
ANGI_SERVICE_SLUGS = {
    'plumbing': 'plumbing',
    'electrical': 'electrician',
    'hvac': 'heating-cooling',
    'roofing': 'roofing',
    'painting': 'painting',
    'landscaping': 'landscaping',
    'cleaning': 'house-cleaning',
    'handyman': 'handyman',
    'pest_control': 'pest-control',
    'flooring': 'flooring',
    'remodeling': 'remodeling',
}


class AngiReviewScraper(BaseScraper):
    MONITOR_NAME = 'angi_reviews'
    DELAY_MIN = 4.0
    DELAY_MAX = 10.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 60


def scrape_angi_reviews(scraper, angi_url):
    """
    Scrape reviews from an Angi business profile page.
    Returns list of review dicts with: author, rating, text, date.
    """
    resp = scraper.get(angi_url)
    if not resp:
        return []
    if resp.status_code == 404:
        logger.debug(f"Angi page not found: {angi_url}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    reviews = []

    # Angi review containers
    review_els = soup.select(
        '[data-testid="review"], .review-card, '
        '.review-item, .review, [class*="ReviewCard"]'
    )

    if not review_els:
        review_els = soup.select('div[class*="review"]')

    for rev_el in review_els:
        try:
            # Rating
            rating = 0
            rating_el = rev_el.select_one(
                '[aria-label*="star"], [class*="star"], '
                '[class*="rating"], [data-testid="rating"]'
            )
            if rating_el:
                aria = rating_el.get('aria-label', '')
                import re
                nums = re.findall(r'[\d.]+', aria)
                if nums:
                    rating = int(float(nums[0]))
                elif rating_el.get('data-rating'):
                    rating = int(float(rating_el['data-rating']))

            # Review text
            text_el = rev_el.select_one(
                'p[class*="review"], [class*="ReviewText"], '
                '.review-text, .review-content, p'
            )
            text = text_el.get_text(strip=True) if text_el else ''
            if not text:
                continue

            # Author
            author_el = rev_el.select_one(
                '[class*="author"], [class*="name"], '
                '.reviewer-name, .review-author'
            )
            author = author_el.get_text(strip=True) if author_el else ''

            # Date
            date_el = rev_el.select_one(
                'time, [class*="date"], .review-date, '
                '[data-testid="review-date"]'
            )
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get_text(strip=True)

            reviews.append({
                'author': author,
                'rating': rating,
                'text': text,
                'date': date_str,
            })
        except Exception as e:
            logger.debug(f"Error parsing Angi review: {e}")
            continue

    logger.info(f"Scraped {len(reviews)} reviews from {angi_url}")
    return reviews


def parse_angi_date(date_str):
    """Parse Angi date strings."""
    if not date_str:
        return None

    date_str = date_str.strip()
    formats = [
        '%m/%d/%Y',
        '%b %d, %Y',
        '%B %d, %Y',
        '%Y-%m-%d',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt)
        except ValueError:
            continue
    return None


def analyze_angi_opportunity(review_text, competitor_name):
    """
    Use Claude API to analyze if a negative Angi review indicates
    the reviewer needs an alternative provider.
    """
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        return heuristic_check(review_text)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"Analyze this negative Angi review of '{competitor_name}' (a local service business). "
            f"Is the reviewer likely looking for an alternative provider? "
            f"Consider: unresolved problems, need for redo work, urgency.\n\n"
            f"Review:\n\"{review_text}\"\n\n"
            f"Respond JSON only:\n"
            f'{{"is_opportunity": true/false, "reason": "brief", '
            f'"urgency": "high/medium/low", "issue_type": "no-show/quality/price/communication/other"}}'
        )

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=200,
            messages=[{'role': 'user', 'content': prompt}],
        )

        import json
        result_text = response.content[0].text.strip()
        if result_text.startswith('```'):
            result_text = result_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        result = json.loads(result_text)

        return {
            'is_opportunity': result.get('is_opportunity', False),
            'analysis': result.get('reason', ''),
            'urgency': result.get('urgency', 'low'),
            'issue_type': result.get('issue_type', 'other'),
        }
    except Exception as e:
        logger.warning(f"Claude API failed for Angi review, using heuristic: {e}")
        return heuristic_check(review_text)


def heuristic_check(review_text):
    """Simple keyword-based opportunity detection."""
    text_lower = review_text.lower()
    signals = [
        'never showed', 'no show', 'didn\'t show',
        'looking for another', 'find someone else',
        'do not use', 'don\'t use', 'avoid', 'worst',
        'still broken', 'not fixed', 'need to find',
        'overcharged', 'rip off', 'scam',
        'never again', 'won\'t use again',
        'had to hire another', 'called another',
        'unfinished', 'incomplete', 'left us',
        'emergency', 'urgent', 'flooding', 'leaking',
    ]
    matched = [s for s in signals if s in text_lower]
    return {
        'is_opportunity': len(matched) > 0,
        'analysis': f"Matched: {', '.join(matched)}" if matched else 'No signals',
        'urgency': 'high' if any(w in text_lower for w in ['emergency', 'urgent', 'flooding']) else 'medium',
        'issue_type': 'no-show' if any(w in text_lower for w in ['no show', 'didn\'t show', 'never showed']) else 'quality',
    }


def monitor_angi_reviews(competitors=None, max_age_hours=168, dry_run=False):
    """
    Main monitoring function. Scrapes Angi reviews on tracked competitors.

    For competitors that have a website field containing 'angi.com', or
    finds them via Angi search using their name and location.

    Args:
        competitors: QuerySet of TrackedCompetitor (default: all active)
        max_age_hours: Skip reviews older than this (default: 7 days)
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts
    """
    scraper = AngiReviewScraper()

    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"Skipping Angi review monitor: {reason}")
        return {'skipped': True, 'reason': reason}

    if competitors is None:
        competitors = TrackedCompetitor.objects.filter(
            is_active=True,
        ).select_related('business')

    stats = {
        'checked': 0, 'reviews_found': 0, 'opportunities': 0,
        'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
    }
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    competitors = scraper.shuffle(competitors)

    try:
        for competitor in competitors:
            if scraper.is_stopped:
                break

            # Try to find Angi URL — check if website is an angi link
            angi_url = ''
            if competitor.website and 'angi.com' in competitor.website:
                angi_url = competitor.website
            elif competitor.yelp_url:
                # Try to construct an Angi search URL from competitor name
                name_slug = competitor.name.lower().replace(' ', '-')
                bp = competitor.business
                if bp.city and bp.state:
                    angi_url = f"{BASE_URL}/companylist/us/{bp.state.lower()}/{bp.city.lower().replace(' ', '-')}/{name_slug}.htm"

            if not angi_url:
                logger.debug(f"No Angi URL for competitor: {competitor.name}")
                continue

            logger.info(f"Checking Angi reviews for: {competitor.name}")
            stats['checked'] += 1

            reviews = scrape_angi_reviews(scraper, angi_url)
            stats['reviews_found'] += len(reviews)

            for review in reviews:
                try:
                    rating = review.get('rating', 0)
                    if rating > 2:
                        continue

                    review_text = review.get('text', '')
                    if not review_text:
                        continue

                    posted_at = parse_angi_date(review.get('date'))
                    if posted_at and posted_at < cutoff:
                        continue

                    analysis = analyze_angi_opportunity(review_text, competitor.name)

                    # Store CompetitorReview
                    CompetitorReview.objects.get_or_create(
                        competitor=competitor,
                        review_text=review_text,
                        defaults={
                            'platform': 'angi',
                            'reviewer_name': review.get('author', ''),
                            'rating': rating,
                            'review_date': posted_at.date() if posted_at else None,
                            'is_negative': True,
                            'is_opportunity': analysis.get('is_opportunity', False),
                            'ai_analysis': analysis.get('analysis', ''),
                        },
                    )

                    if not analysis.get('is_opportunity'):
                        continue

                    stats['opportunities'] += 1

                    author = review.get('author', 'Anonymous Reviewer')
                    content = (
                        f"Opportunity: {rating}-star Angi review on {competitor.name}\n\n"
                        f"Review by {author}:\n\"{review_text}\"\n\n"
                        f"AI Analysis: {analysis.get('analysis', '')}\n"
                        f"Issue: {analysis.get('issue_type', 'unknown')} | "
                        f"Urgency: {analysis.get('urgency', 'medium')}"
                    )

                    if dry_run:
                        logger.info(f"[DRY RUN] Would create lead: {rating}-star Angi review on {competitor.name}")
                        stats['created'] += 1
                        continue

                    lead, created, num_assigned = process_lead(
                        platform='angi_review',
                        source_url=angi_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'competitor_id': competitor.id,
                            'competitor_name': competitor.name,
                            'rating': rating,
                            'ai_analysis': analysis,
                            'angi_url': angi_url,
                        },
                    )

                    if created:
                        stats['created'] += 1
                        stats['assigned'] += num_assigned
                    else:
                        stats['duplicates'] += 1

                except Exception as e:
                    logger.error(f"Error processing Angi review on {competitor.name}: {e}")
                    stats['errors'] += 1
                    continue

    except RateLimitHit:
        logger.warning("Angi review monitor stopped: rate limit hit")

    logger.info(f"Angi review monitor complete: {stats}")
    return stats
