"""
BaseScraper — shared anti-detection and rate-limiting infrastructure
for all SalesSignal AI monitors.

Every web-scraping monitor should inherit from BaseScraper to get:
 1. Random delays between requests (not fixed sleep)
 2. Rotating User-Agent strings
 3. robots.txt respect
 4. Session-based requesting with persistent cookies
 5. MAX_REQUESTS_PER_RUN cap
 6. Minimum cooldown between full runs
 7. Randomized scraping order
 8. Automatic back-off on 429/403 errors
 9. Sequential (never parallel) requests
10. Per-domain request cap
"""
import logging
import random
import time
from datetime import timedelta
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# --- Rotating User-Agent pool ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
]


class RateLimitHit(Exception):
    """Raised when a 429 or 403 is received, signaling the run should stop."""
    pass


class BaseScraper:
    """
    Base class for all web-scraping monitors.

    Subclasses set class-level config, then call self.get(url) for all HTTP
    requests.  BaseScraper handles UA rotation, delays, robots.txt, sessions,
    per-domain caps, request caps, and cooldown enforcement.

    Class-level configuration (override in subclass):
        MONITOR_NAME:           str  — identifier (e.g. 'craigslist')
        DELAY_MIN:            float  — min seconds between requests (default 3)
        DELAY_MAX:            float  — max seconds between requests (default 8)
        MAX_REQUESTS_PER_RUN:   int  — stop after this many requests (default 50)
        MAX_PER_DOMAIN:         int  — max requests per domain per run (default 20)
        COOLDOWN_MINUTES:       int  — min minutes between full runs (default 30)
        RESPECT_ROBOTS:        bool  — check robots.txt (default True)
        TIMEOUT:                int  — request timeout in seconds (default 15)
    """

    MONITOR_NAME = 'base'
    DELAY_MIN = 3.0
    DELAY_MAX = 8.0
    MAX_REQUESTS_PER_RUN = 50
    MAX_PER_DOMAIN = 20
    COOLDOWN_MINUTES = 30
    RESPECT_ROBOTS = True
    TIMEOUT = 15

    def __init__(self):
        self._session = requests.Session()
        self._request_count = 0
        self._domain_counts = {}          # domain -> count
        self._robots_cache = {}           # domain -> RobotFileParser | None
        self._stopped = False             # set True on 429/403
        self._backoff_multiplier = 1      # doubles on rate-limit errors

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, url, **kwargs):
        """
        Make a GET request through the shared session with all protections.

        Returns a requests.Response, or None if the request was blocked,
        skipped, or the run has been stopped.

        Raises RateLimitHit on 429/403 so the monitor can stop its run.
        """
        if self._stopped:
            return None

        # Enforce MAX_REQUESTS_PER_RUN
        if self._request_count >= self.MAX_REQUESTS_PER_RUN:
            logger.info(
                f'[{self.MONITOR_NAME}] Hit MAX_REQUESTS_PER_RUN '
                f'({self.MAX_REQUESTS_PER_RUN}), stopping.'
            )
            self._stopped = True
            return None

        # Enforce per-domain cap
        domain = self._domain_of(url)
        domain_hits = self._domain_counts.get(domain, 0)
        if domain_hits >= self.MAX_PER_DOMAIN:
            logger.info(
                f'[{self.MONITOR_NAME}] Hit MAX_PER_DOMAIN ({self.MAX_PER_DOMAIN}) '
                f'for {domain}, skipping.'
            )
            return None

        # Respect robots.txt
        if self.RESPECT_ROBOTS and not self._robots_allowed(url):
            logger.debug(f'[{self.MONITOR_NAME}] robots.txt disallows: {url}')
            return None

        # Random delay (skip before the very first request)
        if self._request_count > 0:
            delay = random.uniform(
                self.DELAY_MIN * self._backoff_multiplier,
                self.DELAY_MAX * self._backoff_multiplier,
            )
            time.sleep(delay)

        # Rotate User-Agent per request
        self._session.headers['User-Agent'] = random.choice(USER_AGENTS)

        # Perform the request
        kwargs.setdefault('timeout', self.TIMEOUT)
        try:
            resp = self._session.get(url, **kwargs)
        except requests.RequestException as e:
            logger.error(f'[{self.MONITOR_NAME}] Request failed for {url}: {e}')
            self._request_count += 1
            self._domain_counts[domain] = domain_hits + 1
            return None

        self._request_count += 1
        self._domain_counts[domain] = domain_hits + 1

        # Back-off on rate-limit or forbidden
        if resp.status_code in (429, 403):
            self._backoff_multiplier *= 2
            logger.warning(
                f'[{self.MONITOR_NAME}] Got {resp.status_code} from {domain}. '
                f'Stopping run, backoff now {self._backoff_multiplier}x.'
            )
            self._stopped = True
            raise RateLimitHit(
                f'{resp.status_code} from {domain} — run stopped'
            )

        return resp

    @property
    def is_stopped(self):
        """True if the scraper has been stopped (rate limit or request cap)."""
        return self._stopped

    @property
    def request_count(self):
        return self._request_count

    def check_cooldown(self):
        """
        Check if enough time has passed since the last successful run.
        Returns (allowed: bool, reason: str).
        """
        from core.models.monitoring import MonitorRun

        last_run = (
            MonitorRun.objects
            .filter(monitor_name=self.MONITOR_NAME, status__in=('success', 'partial'))
            .order_by('-finished_at')
            .first()
        )
        if not last_run or not last_run.finished_at:
            return True, ''

        elapsed = timezone.now() - last_run.finished_at
        cooldown = timedelta(minutes=self.COOLDOWN_MINUTES * self._backoff_multiplier)

        if elapsed < cooldown:
            remaining = cooldown - elapsed
            mins = int(remaining.total_seconds() / 60)
            reason = (
                f'{self.MONITOR_NAME} cooldown: {mins}m remaining '
                f'(last run finished {last_run.finished_at:%H:%M})'
            )
            return False, reason

        return True, ''

    @staticmethod
    def shuffle(items):
        """Return a shuffled copy of the list (randomize scraping order)."""
        shuffled = list(items)
        random.shuffle(shuffled)
        return shuffled

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _domain_of(url):
        return urlparse(url).netloc

    def _robots_allowed(self, url):
        """Check robots.txt for the given URL. Cached per domain."""
        domain = self._domain_of(url)
        if domain not in self._robots_cache:
            self._robots_cache[domain] = self._fetch_robots(url)
        rp = self._robots_cache[domain]
        if rp is None:
            return True  # no robots.txt = allowed
        return rp.can_fetch('*', url)

    def _fetch_robots(self, url):
        """Fetch and parse robots.txt for the domain. Returns None on failure."""
        parsed = urlparse(url)
        robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
        try:
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            return rp
        except Exception:
            logger.debug(f'[{self.MONITOR_NAME}] Could not fetch robots.txt for {parsed.netloc}')
            return None
