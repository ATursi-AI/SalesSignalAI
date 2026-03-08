"""
Monitor runner with error handling, retry logic, and MonitorRun logging.

Wraps all individual monitors to provide:
- Structured logging via MonitorRun records
- Retry with exponential backoff on transient failures
- Graceful error handling that doesn't crash the full run
- Centralized stats collection
"""
import logging
import time
import traceback

from django.utils import timezone

from core.models.monitoring import MonitorRun

logger = logging.getLogger(__name__)

# Retry settings
MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 30  # seconds


def run_monitor(monitor_name, monitor_fn, *args, **kwargs):
    """
    Execute a monitor function with logging, error handling, and retry logic.

    Args:
        monitor_name: identifier like 'craigslist', 'reddit', 'facebook'
        monitor_fn: callable that returns a dict with keys:
            posts_scraped/items_scraped, created, duplicates, errors
        *args, **kwargs: passed to monitor_fn

    Returns:
        MonitorRun instance with final stats
    """
    run = MonitorRun.objects.create(
        monitor_name=monitor_name,
        status='running',
    )

    logger.info(f'[{monitor_name}] Monitor run #{run.id} started')

    last_error = ''
    stats = None

    for attempt in range(1, MAX_RETRIES + 2):  # +2 because range is exclusive and attempt starts at 1
        try:
            stats = monitor_fn(*args, **kwargs)

            # Handle explicit error returns (like facebook's credential errors)
            if isinstance(stats, dict) and 'error' in stats:
                last_error = stats['error']
                logger.warning(f'[{monitor_name}] Attempt {attempt}: {last_error}')
                # Don't retry configuration errors
                if last_error in ('playwright_not_installed', 'credentials_not_configured',
                                  'api_not_configured'):
                    break
                if attempt <= MAX_RETRIES:
                    backoff = RETRY_BACKOFF_BASE * attempt
                    logger.info(f'[{monitor_name}] Retrying in {backoff}s...')
                    time.sleep(backoff)
                    continue
                break

            # Handle cooldown skips (not an error, just skipped this run)
            if isinstance(stats, dict) and 'skipped_reason' in stats:
                run.finish(status='success')
                run.details = stats
                run.save(update_fields=['details'])
                logger.info(f'[{monitor_name}] Skipped: {stats["skipped_reason"]}')
                return run

            # Success — populate stats
            run.items_scraped = (
                stats.get('posts_scraped', 0) or
                stats.get('items_scraped', 0) or
                stats.get('articles_found', 0) or 0
            )
            run.leads_created = stats.get('created', 0) or 0
            run.duplicates = stats.get('duplicates', 0) or 0
            run.errors = stats.get('errors', 0) or 0
            run.details = {k: v for k, v in stats.items()
                          if k not in ('error',)}

            if run.errors > 0 and run.leads_created > 0:
                run.finish(status='partial')
            else:
                run.finish(status='success')

            logger.info(
                f'[{monitor_name}] Run #{run.id} complete: '
                f'{run.items_scraped} scraped, {run.leads_created} leads, '
                f'{run.duplicates} dupes, {run.errors} errors'
            )
            return run

        except Exception as e:
            last_error = f'{type(e).__name__}: {str(e)}'
            tb = traceback.format_exc()
            logger.error(f'[{monitor_name}] Attempt {attempt} failed: {last_error}\n{tb}')

            if attempt <= MAX_RETRIES:
                backoff = RETRY_BACKOFF_BASE * attempt
                logger.info(f'[{monitor_name}] Retrying in {backoff}s...')
                time.sleep(backoff)
            else:
                break

    # All attempts failed
    run.error_message = last_error
    run.finish(status='failed', error_message=last_error)
    logger.error(f'[{monitor_name}] Run #{run.id} FAILED after {MAX_RETRIES + 1} attempts: {last_error}')
    return run


def run_all_monitors(dry_run=False, monitors=None):
    """
    Run all (or selected) monitors sequentially with logging.

    Args:
        dry_run: if True, monitors that support it will log but not create leads
        monitors: list of monitor names to run (default: all)

    Returns:
        list of MonitorRun instances
    """
    # Import all monitor functions
    monitor_registry = _get_monitor_registry()

    if monitors:
        monitor_registry = {k: v for k, v in monitor_registry.items() if k in monitors}

    runs = []
    for name, (fn, default_kwargs) in monitor_registry.items():
        kwargs = dict(default_kwargs)
        if dry_run and 'dry_run' in kwargs:
            kwargs['dry_run'] = True

        logger.info(f'--- Running monitor: {name} ---')
        run = run_monitor(name, fn, **kwargs)
        runs.append(run)

    # Summary
    total_scraped = sum(r.items_scraped for r in runs)
    total_leads = sum(r.leads_created for r in runs)
    total_errors = sum(r.errors for r in runs)
    failed = sum(1 for r in runs if r.status == 'failed')

    logger.info(
        f'=== Monitor run complete: {len(runs)} monitors, '
        f'{total_scraped} items, {total_leads} leads, '
        f'{total_errors} errors, {failed} failures ==='
    )
    return runs


def _get_monitor_registry():
    """
    Returns a dict of {name: (function, default_kwargs)} for all monitors.
    Imports are lazy to avoid startup failures if a dependency is missing.
    """
    registry = {}

    # Each entry: name -> (import_path_fn, default_kwargs)
    monitor_defs = [
        ('craigslist', 'core.utils.monitors.craigslist', 'monitor_craigslist', {}),
        ('reddit', 'core.utils.monitors.reddit_local', 'monitor_reddit', {}),
        ('patch', 'core.utils.monitors.patch', 'monitor_patch', {}),
        ('houzz', 'core.utils.monitors.houzz', 'monitor_houzz', {}),
        ('alignable', 'core.utils.monitors.alignable', 'monitor_alignable', {}),
        ('google_qna', 'core.utils.monitors.google_qna', 'monitor_google_qna', {}),
        ('yelp_reviews', 'core.utils.monitors.yelp_reviews', 'monitor_yelp_reviews', {}),
        ('citydata', 'core.utils.monitors.citydata', 'monitor_citydata', {}),
        ('biggerpockets', 'core.utils.monitors.biggerpockets', 'monitor_biggerpockets', {}),
        ('angi_reviews', 'core.utils.monitors.angi_reviews', 'monitor_angi_reviews', {}),
        ('thumbtack', 'core.utils.monitors.thumbtack', 'monitor_thumbtack', {}),
        ('porch_reviews', 'core.utils.monitors.porch_reviews', 'monitor_porch', {}),
        ('google_reviews', 'core.utils.monitors.google_reviews', 'monitor_google_reviews', {}),
        ('local_news', 'core.utils.monitors.local_news', 'monitor_local_news', {}),
        ('parent_communities', 'core.utils.monitors.parent_communities', 'monitor_parent_communities', {}),
        ('trade_forums', 'core.utils.monitors.trade_forums', 'monitor_trade_forums', {}),
        ('facebook', 'core.utils.monitors.facebook_groups', 'monitor_facebook_groups',
         {'dry_run': False}),
        ('facebook_apify', 'core.utils.monitors.apify_facebook', 'monitor_facebook_apify',
         {'dry_run': False}),
        ('nextdoor', 'core.utils.monitors.apify_nextdoor', 'monitor_nextdoor',
         {'dry_run': False}),
        ('permits', 'core.utils.monitors.permits', 'monitor_permits',
         {'dry_run': False}),
        ('property_sales', 'core.utils.monitors.property_sales', 'monitor_property_sales',
         {'dry_run': False}),
        ('business_filings', 'core.utils.monitors.business_filings', 'monitor_business_filings',
         {'dry_run': False}),
        ('weather', 'core.utils.monitors.weather', 'monitor_weather',
         {'dry_run': False}),
        ('twitter_apify', 'core.utils.monitors.apify_twitter', 'monitor_twitter',
         {'dry_run': False}),
        ('tiktok', 'core.utils.monitors.apify_tiktok', 'monitor_tiktok',
         {'dry_run': False}),
        ('quora', 'core.utils.monitors.apify_quora', 'monitor_quora',
         {'dry_run': False}),
        ('threads', 'core.utils.monitors.apify_threads', 'monitor_threads',
         {'dry_run': False}),
        ('trustpilot', 'core.utils.monitors.apify_trustpilot', 'monitor_trustpilot',
         {'dry_run': False}),
        ('code_violations', 'core.utils.monitors.code_violations', 'monitor_code_violations',
         {'dry_run': False}),
        ('health_inspections', 'core.utils.monitors.health_inspections', 'monitor_health_inspections',
         {'dry_run': False}),
        ('license_expirations', 'core.utils.monitors.license_expirations', 'monitor_license_expirations',
         {'dry_run': False}),
        ('evictions', 'core.utils.monitors.eviction_filings', 'monitor_evictions',
         {'dry_run': False}),
        ('bbb', 'core.utils.monitors.bbb', 'monitor_bbb',
         {'dry_run': False}),
    ]

    for name, module_path, fn_name, default_kwargs in monitor_defs:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            fn = getattr(mod, fn_name)
            registry[name] = (fn, default_kwargs)
        except (ImportError, AttributeError) as e:
            logger.debug(f'Monitor {name} not available: {e}')

    return registry
