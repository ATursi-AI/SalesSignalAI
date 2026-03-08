"""
Unified Apify integration for SalesSignal AI.

Wraps the Apify Python SDK to provide a clean interface for running
actors (cloud scrapers) with error handling, logging, and cost tracking.

Usage:
    from core.utils.apify_client import ApifyIntegration

    apify = ApifyIntegration()
    results = apify.run_actor(
        'apify/facebook-groups-scraper',
        run_input={'startUrls': [{'url': 'https://facebook.com/groups/123'}]},
    )
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class ApifyError(Exception):
    """Raised when an Apify actor run fails or times out."""
    pass


class ApifyIntegration:
    """
    Unified wrapper around the Apify Python SDK.

    Handles:
    - API token configuration
    - Actor run execution with timeout and memory limits
    - Dataset result retrieval
    - Structured logging and error handling
    - Cost tracking per run
    """

    # Default limits
    DEFAULT_TIMEOUT_SECS = 300  # 5 minutes
    DEFAULT_MEMORY_MBYTES = 512
    MAX_ITEMS = 200  # max dataset items to fetch per run

    def __init__(self, api_token=None):
        token = api_token or getattr(settings, 'APIFY_API_TOKEN', '')
        if not token:
            raise ApifyError(
                'APIFY_API_TOKEN not configured. '
                'Set it in .env or Django settings.'
            )
        try:
            from apify_client import ApifyClient
        except ImportError:
            raise ApifyError(
                'apify-client not installed. Run: pip install apify-client'
            )

        self._client = ApifyClient(token)
        logger.debug('ApifyIntegration initialized')

    def run_actor(self, actor_id, run_input, timeout_secs=None,
                  memory_mbytes=None, max_items=None):
        """
        Run an Apify actor and return the dataset items.

        Args:
            actor_id: Apify actor ID (e.g. 'apify/facebook-groups-scraper')
            run_input: dict of input parameters for the actor
            timeout_secs: max run time in seconds
            memory_mbytes: memory allocation for the actor
            max_items: max items to fetch from the result dataset

        Returns:
            list of dicts — the actor's output dataset items

        Raises:
            ApifyError on failure or timeout
        """
        timeout = timeout_secs or self.DEFAULT_TIMEOUT_SECS
        memory = memory_mbytes or self.DEFAULT_MEMORY_MBYTES
        limit = max_items or self.MAX_ITEMS

        logger.info(
            f'[Apify] Starting actor {actor_id} '
            f'(timeout={timeout}s, memory={memory}MB)'
        )

        try:
            run = self._client.actor(actor_id).call(
                run_input=run_input,
                timeout_secs=timeout,
                memory_mbytes=memory,
            )
        except Exception as e:
            raise ApifyError(f'Actor {actor_id} call failed: {e}') from e

        if not run:
            raise ApifyError(f'Actor {actor_id} returned no run object')

        status = run.get('status', 'UNKNOWN')
        run_id = run.get('id', '?')

        logger.info(
            f'[Apify] Actor {actor_id} run {run_id} finished '
            f'with status: {status}'
        )

        if status not in ('SUCCEEDED',):
            error_msg = run.get('statusMessage', 'No error message')
            raise ApifyError(
                f'Actor {actor_id} run {run_id} failed '
                f'(status={status}): {error_msg}'
            )

        # Log cost info
        usage = run.get('usage', {})
        cost_usd = run.get('usageTotalUsd', 0)
        if cost_usd:
            logger.info(f'[Apify] Run cost: ${cost_usd:.4f}')

        # Fetch dataset items
        dataset_id = run.get('defaultDatasetId')
        if not dataset_id:
            logger.warning(f'[Apify] No dataset ID in run {run_id}')
            return []

        try:
            items = list(
                self._client.dataset(dataset_id)
                .iterate_items(limit=limit)
            )
        except Exception as e:
            raise ApifyError(
                f'Failed to fetch dataset {dataset_id}: {e}'
            ) from e

        logger.info(f'[Apify] Fetched {len(items)} items from dataset')
        return items

    def get_actor_info(self, actor_id):
        """Get metadata about an actor (name, description, pricing)."""
        try:
            return self._client.actor(actor_id).get()
        except Exception as e:
            logger.error(f'[Apify] Failed to get actor info for {actor_id}: {e}')
            return None

    def is_configured(self):
        """Check if the Apify API token is set and valid."""
        try:
            user = self._client.user().get()
            return user is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Platform-specific convenience methods
    # ------------------------------------------------------------------

    def scrape_facebook_groups(self, group_urls, max_posts=50):
        """Scrape Facebook Group posts via Apify's Facebook Groups Scraper."""
        return self.run_actor(
            'apify/facebook-groups-scraper',
            run_input={
                'startUrls': [{'url': u} for u in group_urls],
                'maxPosts': max_posts,
                'maxComments': 0,
            },
            timeout_secs=300,
        )

    def scrape_nextdoor(self, search_urls, max_results=50):
        """Scrape Nextdoor posts by search URLs."""
        return self.run_actor(
            'curious_coder/nextdoor-scraper',
            run_input={
                'startUrls': [{'url': u} for u in search_urls],
                'maxItems': max_results,
            },
            timeout_secs=300,
        )

    def scrape_twitter(self, keywords, locations=None, max_tweets=100):
        """Search Twitter/X for keyword matches via Apify Tweet Scraper V2."""
        queries = keywords if isinstance(keywords, list) else [keywords]
        if locations:
            expanded = []
            for q in queries:
                for loc in locations:
                    expanded.append(f'{q} {loc}')
            queries = expanded
        return self.run_actor(
            'apidojo/tweet-scraper',
            run_input={
                'searchTerms': queries,
                'maxTweets': max_tweets,
                'sort': 'Latest',
            },
            timeout_secs=300,
        )

    def scrape_google_maps(self, search_terms, locations, max_results=100):
        """Scrape Google Maps business listings via Apify."""
        queries = []
        for term in search_terms:
            for loc in locations:
                queries.append(f'{term} in {loc}')
        return self.run_actor(
            'compass/crawler-google-places',
            run_input={
                'searchStringsArray': queries,
                'maxCrawledPlacesPerSearch': max_results,
                'language': 'en',
                'maxReviews': 0,
            },
            timeout_secs=600,
        )

    def scrape_google_reviews(self, place_urls, max_reviews=50):
        """Scrape Google Maps reviews for competitor monitoring."""
        return self.run_actor(
            'compass/crawler-google-places',
            run_input={
                'startUrls': [{'url': u} for u in place_urls],
                'maxReviews': max_reviews,
                'scrapeReviewsPersonalData': False,
            },
            timeout_secs=300,
        )

    def scrape_tiktok(self, keywords, max_videos=50):
        """Search TikTok for relevant content via Apify."""
        return self.run_actor(
            'clockworks/free-tiktok-scraper',
            run_input={
                'searchQueries': keywords if isinstance(keywords, list) else [keywords],
                'resultsPerPage': max_videos,
            },
            timeout_secs=300,
        )

    def scrape_quora(self, keywords, max_questions=50):
        """Search Quora for service recommendation questions."""
        return self.run_actor(
            'curious_coder/quora-scraper',
            run_input={
                'searchTerms': keywords if isinstance(keywords, list) else [keywords],
                'maxItems': max_questions,
            },
            timeout_secs=300,
        )

    def scrape_threads(self, keywords, max_posts=50):
        """Search Threads for local service discussions."""
        return self.run_actor(
            'apidojo/threads-scraper',
            run_input={
                'searchTerms': keywords if isinstance(keywords, list) else [keywords],
                'maxItems': max_posts,
            },
            timeout_secs=300,
        )

    def scrape_facebook_marketplace(self, locations, categories, max_listings=50):
        """Scrape Facebook Marketplace service requests."""
        search_urls = []
        for loc in locations:
            for cat in categories:
                search_urls.append({
                    'url': f'https://www.facebook.com/marketplace/{loc}/search/?query={cat}'
                })
        return self.run_actor(
            'apify/facebook-marketplace-scraper',
            run_input={
                'startUrls': search_urls,
                'maxItems': max_listings,
            },
            timeout_secs=300,
        )

    def scrape_trustpilot(self, company_urls, max_reviews=50):
        """Scrape Trustpilot competitor reviews."""
        return self.run_actor(
            'curious_coder/trustpilot-scraper',
            run_input={
                'startUrls': [{'url': u} for u in company_urls],
                'maxReviews': max_reviews,
            },
            timeout_secs=300,
        )

    def scrape_instagram(self, hashtags=None, profiles=None, max_posts=50):
        """Scrape Instagram posts by hashtag or profile."""
        run_input = {'resultsLimit': max_posts}
        if hashtags:
            run_input['hashtags'] = hashtags if isinstance(hashtags, list) else [hashtags]
        if profiles:
            run_input['profiles'] = profiles if isinstance(profiles, list) else [profiles]
        return self.run_actor(
            'apify/instagram-scraper',
            run_input=run_input,
            timeout_secs=300,
        )
