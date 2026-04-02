"""
NOAA Weather Alert monitor for SalesSignal AI.

Uses the free NOAA National Weather Service API (https://api.weather.gov)
to check for active severe weather alerts. No API key needed.

When severe weather hits an area where customers operate, creates HOT leads
because speed matters — the first service providers to reach affected
homeowners get the work.

Weather event → service category mapping:
- Severe thunderstorm → tree service, roofing, gutter, window replacement
- Tornado → restoration, general contractor, roofing, tree service
- Flood → water damage restoration, plumber, mold remediation
- Winter storm → snow removal, plumber (frozen pipes), HVAC
- Hurricane → every trade
- Hail → roofing, auto body, window replacement
- High wind → tree service, roofing, fencing, siding

Nationwide — dynamically checks areas where active BusinessProfiles operate.
"""
import logging
from datetime import timedelta

import requests
from django.utils import timezone

from core.models.business import BusinessProfile
from core.models.monitoring import MonitorRun
from .lead_processor import process_lead

logger = logging.getLogger(__name__)

# NOAA API base URL — completely free, no key needed
NOAA_API_BASE = 'https://api.weather.gov'

# Cooldown between runs (15 minutes for weather — time-sensitive)
COOLDOWN_MINUTES = 15

# Weather event type → relevant services
WEATHER_SERVICE_MAP = {
    'Severe Thunderstorm': ['tree service', 'roofing', 'gutter cleaning', 'window replacement', 'siding'],
    'Tornado': ['restoration', 'general contractor', 'roofing', 'tree service', 'fencing', 'electrician'],
    'Flash Flood': ['water damage restoration', 'plumber', 'mold remediation', 'carpet cleaning', 'basement waterproofing'],
    'Flood': ['water damage restoration', 'plumber', 'mold remediation', 'carpet cleaning'],
    'Winter Storm': ['snow removal', 'plumber', 'HVAC', 'roofing'],
    'Blizzard': ['snow removal', 'plumber', 'HVAC', 'roofing', 'generator repair'],
    'Ice Storm': ['tree service', 'plumber', 'electrician', 'roofing'],
    'Hurricane': ['general contractor', 'roofing', 'tree service', 'restoration', 'plumber',
                  'electrician', 'fencing', 'siding', 'window replacement', 'mold remediation'],
    'Tropical Storm': ['general contractor', 'roofing', 'tree service', 'restoration', 'plumber'],
    'High Wind': ['tree service', 'roofing', 'fencing', 'siding', 'gutter cleaning'],
    'Hail': ['roofing', 'auto body', 'window replacement', 'siding', 'gutter cleaning'],
    'Extreme Heat': ['HVAC', 'electrician', 'pool service'],
    'Extreme Cold': ['plumber', 'HVAC', 'insulation', 'generator repair'],
    'Fire Weather': ['landscaping', 'tree service', 'restoration'],
    'Dust Storm': ['auto detailing', 'window cleaning', 'HVAC'],
}

# Severity levels we care about (skip minor advisories)
RELEVANT_SEVERITIES = {'Extreme', 'Severe', 'Moderate'}

# Event categories worth monitoring
RELEVANT_CATEGORIES = {'Met'}  # meteorological


def _get_active_states():
    """Get unique states where active businesses operate."""
    states = (
        BusinessProfile.objects
        .filter(is_active=True, onboarding_complete=True)
        .exclude(state='')
        .values_list('state', flat=True)
        .distinct()
    )
    return list(states)


def _detect_services(event_type):
    """Map a weather event type to relevant services."""
    for key, services in WEATHER_SERVICE_MAP.items():
        if key.lower() in event_type.lower():
            return services
    return ['general contractor', 'restoration']


def _fetch_alerts_for_state(state_code):
    """
    Fetch active weather alerts for a US state from NOAA API.
    Returns list of alert dicts.
    """
    url = f'{NOAA_API_BASE}/alerts/active'
    params = {
        'area': state_code,
        'severity': 'Extreme,Severe,Moderate',
        'status': 'actual',
        'message_type': 'alert',
    }
    headers = {
        'User-Agent': 'SalesSignalAI/1.0 (weather-monitor)',
        'Accept': 'application/geo+json',
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning(f'[weather] NOAA API returned {resp.status_code} for {state_code}')
            return []
        data = resp.json()
        return data.get('features', [])
    except requests.RequestException as e:
        logger.error(f'[weather] NOAA API request failed for {state_code}: {e}')
        return []
    except (ValueError, KeyError) as e:
        logger.error(f'[weather] Error parsing NOAA response for {state_code}: {e}')
        return []


def _parse_alert(feature):
    """Parse a NOAA GeoJSON alert feature into a structured dict."""
    props = feature.get('properties', {})

    return {
        'id': props.get('id', ''),
        'event': props.get('event', ''),
        'severity': props.get('severity', ''),
        'certainty': props.get('certainty', ''),
        'urgency': props.get('urgency', ''),
        'headline': props.get('headline', ''),
        'description': props.get('description', ''),
        'areas': props.get('areaDesc', ''),
        'effective': props.get('effective', ''),
        'expires': props.get('expires', ''),
        'sender': props.get('senderName', ''),
        'category': props.get('category', ''),
    }


def _alert_to_lead_content(alert, services, state_code):
    """Build lead content from a weather alert."""
    parts = [
        f'WEATHER ALERT: {alert["event"]}',
        f'Severity: {alert["severity"]}',
    ]

    if alert['headline']:
        parts.append(f'{alert["headline"]}')

    if alert['areas']:
        # Truncate long area descriptions
        areas = alert['areas'][:500]
        parts.append(f'Affected Areas: {areas}')

    parts.append(f'State: {state_code}')
    parts.append(f'Services in demand: {", ".join(services[:6])}')

    if alert['description']:
        # Include first 300 chars of description for context
        parts.append(f'\n{alert["description"][:300]}')

    return '\n'.join(parts)


def monitor_weather(states=None, dry_run=False):
    """
    Monitor NOAA weather alerts for severe weather events.

    Dynamically checks states where active BusinessProfiles operate.
    Creates HOT leads for severe weather events.

    Args:
        states: list of state codes to check (default: from active profiles)
        dry_run: log matches without creating Lead records

    Returns:
        dict with counts: states_checked, alerts_found, created,
                         duplicates, errors
    """
    # Cooldown check
    last_run = (
        MonitorRun.objects
        .filter(monitor_name='weather', status__in=('success', 'partial'))
        .order_by('-finished_at')
        .first()
    )
    if last_run and last_run.finished_at:
        elapsed = timezone.now() - last_run.finished_at
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = int((timedelta(minutes=COOLDOWN_MINUTES) - elapsed).total_seconds() / 60)
            reason = f'weather cooldown: {remaining}m remaining'
            logger.info(reason)
            return {'states_checked': 0, 'alerts_found': 0, 'created': 0,
                    'duplicates': 0, 'errors': 0, 'skipped_reason': reason}

    # Determine which states to check
    if states is None:
        states = _get_active_states()

    if not states:
        logger.info('[weather] No active business states to monitor')
        return {'states_checked': 0, 'alerts_found': 0, 'created': 0,
                'duplicates': 0, 'errors': 0}

    stats = {
        'states_checked': 0,
        'alerts_found': 0,
        'created': 0,
        'duplicates': 0,
        'errors': 0,
    }

    # Track already-processed alert IDs to avoid dups within this run
    seen_ids = set()

    for state_code in states:
        stats['states_checked'] += 1
        logger.info(f'[weather] Checking NOAA alerts for {state_code}')

        alerts_raw = _fetch_alerts_for_state(state_code)
        if not alerts_raw:
            continue

        for feature in alerts_raw:
            try:
                alert = _parse_alert(feature)

                # Skip if already seen
                alert_id = alert.get('id', '')
                if not alert_id or alert_id in seen_ids:
                    continue
                seen_ids.add(alert_id)

                # Skip irrelevant severities
                if alert['severity'] not in RELEVANT_SEVERITIES:
                    continue

                stats['alerts_found'] += 1

                # Detect relevant services
                services = _detect_services(alert['event'])

                # Build content
                content = _alert_to_lead_content(alert, services, state_code)

                # Source URL — NOAA alert detail page
                source_url = f'{NOAA_API_BASE}/alerts/{alert_id.split("/")[-1]}' if '/' in alert_id else f'{NOAA_API_BASE}/alerts/active'

                if dry_run:
                    logger.info(f'[DRY RUN] Would create weather lead: {alert["event"]} in {state_code}')
                    stats['created'] += 1
                    continue

                lead, created, num_assigned = process_lead(
                    platform='weather_alert',
                    source_url=source_url,
                    content=content,
                    author=alert.get('sender', 'NOAA NWS'),
                    posted_at=timezone.now(),
                    raw_data={
                        'noaa_alert_id': alert_id,
                        'event': alert['event'],
                        'severity': alert['severity'],
                        'urgency': alert['urgency'],
                        'certainty': alert['certainty'],
                        'areas': alert['areas'][:500],
                        'state': state_code,
                        'services_mapped': services,
                        'expires': alert.get('expires', ''),
                    },
                    source_group='weather',
                    source_type='noaa',
                )

                if created:
                    stats['created'] += 1
                    stats['assigned'] += num_assigned

                    # Override urgency to HOT for severe weather
                    if lead and alert['severity'] in ('Extreme', 'Severe'):
                        lead.urgency_level = 'hot'
                        lead.urgency_score = 95
                        lead.save(update_fields=['urgency_level', 'urgency_score'])
                else:
                    stats['duplicates'] += 1

            except Exception as e:
                logger.error(f'[weather] Error processing alert: {e}')
                stats['errors'] += 1

    # Add 'assigned' key if missing (when no leads created)
    stats.setdefault('assigned', 0)

    logger.info(f'Weather monitor complete: {stats}')
    return stats
