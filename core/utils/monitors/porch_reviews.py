"""
Porch.com review monitor for SalesSignal AI.
Scrapes competitor reviews on Porch profiles.
Same opportunity-flagging pattern as Yelp/Angi monitors.
"""
import logging
import random
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from core.models.competitors import TrackedCompetitor, CompetitorReview
from .base import BaseScraper, RateLimitHit
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

BASE_URL = 'https://porch.com'


class PorchReviewScraper(BaseScraper):
    MONITOR_NAME = 'porch_reviews'
    DELAY_MIN = 4.0
    DELAY_MAX = 10.0
    MAX_REQUESTS_PER_RUN = 30
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 60


def scrape_porch_reviews(scraper, porch_url):
    """
    Scrape reviews from a Porch.com business profile page.
    Returns list of review dicts with: author, rating, text, date.
    """
    resp = scraper.get(porch_url)
    if not resp or resp.status_code == 404:
        logger.debug(f"Porch page not found or blocked: {porch_url}")
        return []
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    reviews = []

    # Porch review containers
    review_els = soup.select(
        '.review-card, .review-item, [data-testid="review"], '
        '[class*="ReviewCard"], [class*="review-container"], '
        '.review, div[itemtype*="Review"]'
    )

    if not review_els:
        review_els = soup.select('div[class*="review"]')

    for rev_el in review_els:
        try:
            # Rating
            rating = 0
            rating_el = rev_el.select_one(
                '[aria-label*="star"], [class*="star"], '
                '[class*="rating"], [itemprop="ratingValue"]'
            )
            if rating_el:
                aria = rating_el.get('aria-label', '')
                content_val = rating_el.get('content', '')
                import re
                if aria:
                    nums = re.findall(r'[\d.]+', aria)
                    if nums:
                        rating = int(float(nums[0]))
                elif content_val:
                    rating = int(float(content_val))

            # Review text
            text_el = rev_el.select_one(
                'p[class*="review"], [class*="ReviewText"], '
                '.review-text, .review-content, '
                '[itemprop="reviewBody"], p'
            )
            text = text_el.get_text(strip=True) if text_el else ''
            if not text:
                continue

            # Author
            author_el = rev_el.select_one(
                '[class*="author"], [class*="name"], '
                '.reviewer-name, [itemprop="author"]'
            )
            author = author_el.get_text(strip=True) if author_el else ''

            # Date
            date_el = rev_el.select_one(
                'time, [class*="date"], [itemprop="datePublished"]'
            )
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime', '') or date_el.get('content', '') or date_el.get_text(strip=True)

            reviews.append({
                'author': author,
                'rating': rating,
                'text': text,
                'date': date_str,
            })
        except Exception as e:
            logger.debug(f"Error parsing Porch review: {e}")
            continue

    logger.info(f"Scraped {len(reviews)} reviews from {porch_url}")
    return reviews


def parse_porch_date(date_str):
    """Parse Porch date strings."""
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = ['%m/%d/%Y', '%b %d, %Y', '%B %d, %Y', '%Y-%m-%d']
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return timezone.make_aware(dt)
        except ValueError:
            continue
    # Try ISO format
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError):
        pass
    return None


def heuristic_opportunity_check(review_text):
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


def analyze_porch_opportunity(review_text, competitor_name):
    """Use Claude API or fall back to heuristic for opportunity analysis."""
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        return heuristic_opportunity_check(review_text)

    try:
        import anthropic
        import json
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"Analyze this negative Porch.com review of '{competitor_name}'. "
            f"Is the reviewer looking for an alternative service provider?\n\n"
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
        logger.warning(f"Claude API failed for Porch review: {e}")
        return heuristic_opportunity_check(review_text)


def monitor_porch_reviews(competitors=None, max_age_hours=168, dry_run=False):
    """
    Main monitoring function. Scrapes Porch reviews on tracked competitors.

    Args:
        competitors: QuerySet of TrackedCompetitor (default: all active)
        max_age_hours: Skip reviews older than this (default: 7 days)
        dry_run: If True, log matches but don't create Lead records

    Returns:
        dict with counts
    """
    scraper = PorchReviewScraper()

    # Check cooldown
    allowed, reason = scraper.check_cooldown()
    if not allowed:
        logger.info(reason)
        return {
            'checked': 0, 'reviews_found': 0, 'opportunities': 0,
            'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
            'skipped_reason': reason,
        }

    if competitors is None:
        competitors = TrackedCompetitor.objects.filter(
            is_active=True,
        ).select_related('business')

    stats = {
        'checked': 0, 'reviews_found': 0, 'opportunities': 0,
        'created': 0, 'duplicates': 0, 'assigned': 0, 'errors': 0,
    }
    cutoff = timezone.now() - timedelta(hours=max_age_hours)

    # Randomize scraping order
    competitors = scraper.shuffle(competitors)

    for competitor in competitors:
        if scraper.is_stopped:
            break

        # Build Porch URL from competitor info
        porch_url = ''
        if competitor.website and 'porch.com' in competitor.website:
            porch_url = competitor.website
        else:
            # Try to construct URL from name + location
            bp = competitor.business
            if bp.city and bp.state:
                name_slug = competitor.name.lower().replace(' ', '-').replace('&', 'and')
                city_slug = bp.city.lower().replace(' ', '-')
                state_slug = bp.state.lower()
                porch_url = f"{BASE_URL}/{city_slug}-{state_slug}/{name_slug}"

        if not porch_url:
            logger.debug(f"No Porch URL for competitor: {competitor.name}")
            continue

        logger.info(f"Checking Porch reviews for: {competitor.name}")
        stats['checked'] += 1

        try:
            reviews = scrape_porch_reviews(scraper, porch_url)
        except RateLimitHit:
            break
        stats['reviews_found'] += len(reviews)

        for review in reviews:
            if scraper.is_stopped:
                break
            try:
                rating = review.get('rating', 0)
                if rating > 2:
                    continue

                review_text = review.get('text', '')
                if not review_text:
                    continue

                posted_at = parse_porch_date(review.get('date'))
                if posted_at and posted_at < cutoff:
                    continue

                analysis = analyze_porch_opportunity(review_text, competitor.name)

                CompetitorReview.objects.get_or_create(
                    competitor=competitor,
                    review_text=review_text,
                    defaults={
                        'platform': 'porch',
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
                    f"Opportunity: {rating}-star Porch review on {competitor.name}\n\n"
                    f"Review by {author}:\n\"{review_text}\"\n\n"
                    f"AI Analysis: {analysis.get('analysis', '')}\n"
                    f"Issue: {analysis.get('issue_type', 'unknown')} | "
                    f"Urgency: {analysis.get('urgency', 'medium')}"
                )

                if dry_run:
                    logger.info(f"[DRY RUN] Would create lead: {rating}-star Porch review on {competitor.name}")
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='porch',
                    source_url=porch_url,
                    content=content,
                    author=author,
                    posted_at=posted_at,
                    raw_data={
                        'competitor_id': competitor.id,
                        'competitor_name': competitor.name,
                        'rating': rating,
                        'ai_analysis': analysis,
                        'porch_url': porch_url,
                    },
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned
                else:
                    stats['duplicates'] += 1

            except RateLimitHit:
                break
            except Exception as e:
                logger.error(f"Error processing Porch review on {competitor.name}: {e}")
                stats['errors'] += 1
                continue

    logger.info(f"Porch review monitor complete: {stats}")
    return stats
