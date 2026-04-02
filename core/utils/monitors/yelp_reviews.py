"""
Yelp competitor review monitor for SalesSignal AI.
Scrapes recent reviews on tracked competitor Yelp pages.
Flags 1-2 star reviews as opportunities. Uses Claude API to analyze
if the reviewer is looking for an alternative provider.
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


class YelpReviewScraper(BaseScraper):
    MONITOR_NAME = 'yelp_reviews'
    DELAY_MIN = 4.0
    DELAY_MAX = 10.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 60


def scrape_yelp_reviews(scraper, yelp_url, sort_by='date_desc'):
    """
    Scrape reviews from a Yelp business page.
    Returns list of review dicts with: author, rating, text, date.
    """
    # Ensure URL points to reviews sorted by date
    if '?' in yelp_url:
        url = f"{yelp_url}&sort_by={sort_by}"
    else:
        url = f"{yelp_url}?sort_by={sort_by}"

    resp = scraper.get(url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    reviews = []

    # Yelp review containers
    review_els = soup.select(
        '[data-review-id], .review, .review--with-sidebar, '
        '.css-1qn0b6x, li[class*="review"]'
    )

    if not review_els:
        # Broader fallback
        review_els = soup.select('section[aria-label*="review"], div[class*="review"]')

    for rev_el in review_els:
        try:
            # Rating (star count)
            rating = 0
            rating_el = rev_el.select_one(
                '[aria-label*="star rating"], [class*="star"], '
                'div[aria-label*="star"]'
            )
            if rating_el:
                aria = rating_el.get('aria-label', '')
                # "5 star rating" or "1.0 star rating"
                for word in aria.split():
                    try:
                        rating = int(float(word))
                        break
                    except ValueError:
                        continue

            # Review text
            text_el = rev_el.select_one(
                'p[class*="comment"], span[class*="raw"], '
                '.review-content p, [lang]'
            )
            text = text_el.get_text(strip=True) if text_el else ''

            if not text:
                continue

            # Author
            author_el = rev_el.select_one(
                'a[href*="/user_details"], .user-passport-info .user-name a, '
                '[class*="user-name"], a[class*="css-"]'
            )
            author = author_el.get_text(strip=True) if author_el else ''

            # Date
            date_el = rev_el.select_one(
                'span[class*="css-"][class*="date"], .rating-qualifier, '
                'time, span.date'
            )
            date_str = date_el.get_text(strip=True) if date_el else ''

            reviews.append({
                'author': author,
                'rating': rating,
                'text': text,
                'date': date_str,
            })

        except Exception as e:
            logger.debug(f"Error parsing Yelp review: {e}")
            continue

    logger.info(f"Scraped {len(reviews)} reviews from {yelp_url}")
    return reviews


def parse_yelp_date(date_str):
    """
    Parse Yelp date strings like "1/15/2026" or "Jan 15, 2026".
    Returns timezone-aware datetime or None.
    """
    if not date_str:
        return None

    # Clean up common prefixes
    date_str = date_str.strip()
    for prefix in ['Updated review', 'Previous review']:
        if date_str.startswith(prefix):
            date_str = date_str[len(prefix):].strip()

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


def analyze_review_opportunity(review_text, competitor_name):
    """
    Use Claude API to analyze if a negative review indicates
    the reviewer is looking for an alternative provider.

    Returns dict with: is_opportunity (bool), analysis (str).
    """
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        # Fall back to simple heuristic
        return heuristic_opportunity_check(review_text)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"Analyze this negative review of '{competitor_name}' (a local service business). "
            f"Determine if the reviewer is likely looking for an alternative service provider. "
            f"Consider: Did they mention needing the work redone? Did they say they'd look elsewhere? "
            f"Did they describe an urgent unresolved problem?\n\n"
            f"Review text:\n\"{review_text}\"\n\n"
            f"Respond with JSON only:\n"
            f'{{"is_opportunity": true/false, "reason": "brief explanation", '
            f'"urgency": "high/medium/low", "issue_type": "no-show/quality/price/communication/other"}}'
        )

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=200,
            messages=[{'role': 'user', 'content': prompt}],
        )

        import json
        result_text = response.content[0].text.strip()
        # Handle potential markdown code blocks
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
        logger.warning(f"Claude API analysis failed, using heuristic: {e}")
        return heuristic_opportunity_check(review_text)


def heuristic_opportunity_check(review_text):
    """Simple keyword-based opportunity detection fallback."""
    text_lower = review_text.lower()

    opportunity_signals = [
        'never showed up', 'no show', 'didn\'t show',
        'looking for another', 'find someone else', 'hired someone else',
        'do not use', 'don\'t use', 'avoid', 'worst',
        'still broken', 'not fixed', 'need to find',
        'overcharged', 'rip off', 'ripoff', 'scam',
        'never coming back', 'won\'t use again', 'never again',
        'had to hire another', 'called another', 'found another',
        'left us without', 'unfinished', 'incomplete',
        'emergency', 'urgent', 'flooding', 'leaking',
    ]

    matched = [s for s in opportunity_signals if s in text_lower]

    return {
        'is_opportunity': len(matched) > 0,
        'analysis': f"Matched signals: {', '.join(matched)}" if matched else 'No opportunity signals detected',
        'urgency': 'high' if any(w in text_lower for w in ['emergency', 'urgent', 'flooding', 'leaking']) else 'medium',
        'issue_type': 'no-show' if any(w in text_lower for w in ['no show', 'didn\'t show', 'never showed']) else 'quality',
    }


def monitor_yelp_reviews(competitors=None, max_age_hours=168, dry_run=False):
    """
    Main monitoring function. Scrapes Yelp reviews on tracked competitors.

    Flags 1-2 star reviews as opportunities. Uses Claude API to analyze
    if the reviewer is looking for an alternative.

    Args:
        competitors: QuerySet of TrackedCompetitor (default: all active with yelp_url)
        max_age_hours: Skip reviews older than this (default: 7 days)
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts: checked, reviews_found, opportunities, created, duplicates, assigned
    """
    scraper = YelpReviewScraper()

    # Cooldown check
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(f"Skipping Yelp reviews monitor: {reason}")
        return {
            'checked': 0, 'reviews_found': 0, 'opportunities': 0,
            'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
            'skipped_reason': reason,
        }

    if competitors is None:
        competitors = TrackedCompetitor.objects.filter(
            is_active=True,
        ).exclude(yelp_url='').select_related('business')

    stats = {
        'checked': 0, 'reviews_found': 0, 'opportunities': 0,
        'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
    }
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize competitor order
    competitors = scraper.shuffle(competitors)

    try:
        for competitor in competitors:
            if scraper.is_stopped:
                break

            logger.info(f"Checking Yelp reviews for: {competitor.name}")
            stats['checked'] += 1

            reviews = scrape_yelp_reviews(scraper, competitor.yelp_url)
            stats['reviews_found'] += len(reviews)

            for review in reviews:
                if scraper.is_stopped:
                    break

                try:
                    rating = review.get('rating', 0)

                    # Only process negative reviews (1-2 stars)
                    if rating > 2:
                        continue

                    review_text = review.get('text', '')
                    if not review_text:
                        continue

                    posted_at = parse_yelp_date(review.get('date'))

                    # Skip old reviews
                    if posted_at and posted_at < cutoff:
                        continue

                    # Analyze if this is an opportunity
                    analysis = analyze_review_opportunity(review_text, competitor.name)

                    # Create CompetitorReview record regardless
                    review_hash = hashlib.sha256(
                        f"{competitor.id}|{review_text[:200]}".encode()
                    ).hexdigest()[:32]

                    comp_review, cr_created = CompetitorReview.objects.get_or_create(
                        competitor=competitor,
                        review_text=review_text,
                        defaults={
                            'platform': 'yelp',
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

                    # Build lead content
                    author = review.get('author', 'Anonymous Reviewer')
                    content = (
                        f"Opportunity: {rating}-star Yelp review on {competitor.name}\n\n"
                        f"Review by {author}:\n\"{review_text}\"\n\n"
                        f"AI Analysis: {analysis.get('analysis', '')}\n"
                        f"Issue type: {analysis.get('issue_type', 'unknown')}\n"
                        f"Urgency: {analysis.get('urgency', 'medium')}"
                    )

                    if dry_run:
                        logger.info(f"[DRY RUN] Would create lead: {rating}-star review on {competitor.name}")
                        stats['created'] += 1
                        continue

                    lead, created, num_assigned = process_lead(
                        platform='yelp_review',
                        source_url=competitor.yelp_url,
                        content=content,
                        author=author,
                        posted_at=posted_at,
                        raw_data={
                            'competitor_id': competitor.id,
                            'competitor_name': competitor.name,
                            'rating': rating,
                            'is_opportunity': True,
                            'ai_analysis': analysis,
                            'yelp_url': competitor.yelp_url,
                        },
                    )

                    if created:
                        stats['created'] += 1
                        stats['assigned'] += num_assigned
                    else:
                        stats['duplicates'] += 1

                except Exception as e:
                    logger.error(f"Error processing Yelp review on {competitor.name}: {e}")
                    stats['errors'] += 1
                    continue

    except RateLimitHit:
        logger.warning(f"Yelp reviews monitor stopped early — rate limit hit after {scraper.request_count} requests")

    logger.info(f"Yelp review monitor complete: {stats}")
    return stats
