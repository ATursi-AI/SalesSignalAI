"""
Backfill urgency_level, urgency_score, and grade_flag on existing NYC
restaurant health inspection leads.

Recomputes these fields using the same logic as the updated
ny_health_violations.py monitor, so leads previously written with
STALE (20) because they were older than 24 hours get their correct
HOT / WARM / NEW status plus a grade_flag the UI can highlight.

The command ONLY touches leads where:
    source_type='health_inspections' AND raw_data.data_source='nyc_dohmh'

Other monitors are left alone.

Usage:
    # Preview changes
    python manage.py backfill_health_urgency --dry-run

    # Apply changes
    python manage.py backfill_health_urgency

    # Apply and re-query SODA per-CAMIS to fill missing fields
    # (action, inspection_type, lat/lon, bin, bbl, etc.)
    python manage.py backfill_health_urgency --enrich

    # Limit for testing / sampling
    python manage.py backfill_health_urgency --limit 10 --dry-run

    # Target a single restaurant by CAMIS
    python manage.py backfill_health_urgency --camis 41564083 --enrich
"""
import logging
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from core.models.leads import Lead

logger = logging.getLogger(__name__)

SODA_URL = 'https://data.cityofnewyork.us/resource/43nn-pn8j.json'

ENRICH_FIELDS = (
    'action', 'inspection_type', 'grade_date', 'record_date',
    'latitude', 'longitude', 'bin', 'bbl',
    'council_district', 'community_board', 'census_tract',
    'grade', 'score',
)


def _soda_headers():
    h = {}
    token = getattr(settings, 'NYC_OPEN_DATA_APP_TOKEN', '')
    if token:
        h['X-App-Token'] = token
    return h


def _compute_flags(raw):
    """
    Given a raw_data dict, return (urgency_level, urgency_score, grade_flag, note).
    Mirrors the priority logic in ny_health_violations.py so the backfill and
    the live monitor stay in sync.
    """
    grade = str(raw.get('grade') or '').strip().upper()
    action = str(raw.get('action') or '').strip()
    action_lower = action.lower()
    has_critical = bool(raw.get('has_critical'))
    try:
        score = int(raw.get('score') or 0)
    except (ValueError, TypeError):
        score = 0

    is_closed = 'closed' in action_lower

    # --- grade_flag ---
    grade_flag = ''
    if is_closed:
        grade_flag = 'closed'
    elif grade in ('Z', 'P'):
        grade_flag = 'pending_closure'
    elif grade == 'C':
        grade_flag = 'grade_c'
    elif grade == 'B':
        grade_flag = 'grade_b'
    elif has_critical and not grade:
        grade_flag = 'critical_ungraded'

    # --- urgency ---
    if is_closed:
        return 'hot', 95, grade_flag, (
            f'DOHMH CLOSURE — {action}' if action else 'DOHMH closure'
        )
    if grade in ('Z', 'P'):
        return 'hot', 90, grade_flag, f'Grade {grade} — pending closure / re-inspection'
    if grade == 'C':
        return 'hot', 85, grade_flag, 'Grade C — failing, must improve before re-inspection'
    if has_critical or score >= 28:
        return 'hot', 80, grade_flag, 'CRITICAL violation or score >= 28 — restaurant risks closure'
    if score >= 14:
        return 'warm', 65, grade_flag, f'Score {score} — must fix before follow-up inspection'
    return 'new', 40, grade_flag, 'Minor violations'


def _fetch_latest_by_camis(camis):
    """Fetch the most recent real inspection record for a CAMIS from SODA."""
    params = {
        '$where': (
            f"camis='{camis}' AND "
            "inspection_date != '1900-01-01T00:00:00.000'"
        ),
        '$select': (
            'camis,dba,boro,building,street,zipcode,phone,'
            'cuisine_description,inspection_date,violation_code,'
            'violation_description,critical_flag,score,grade,'
            'action,inspection_type,grade_date,record_date,'
            'latitude,longitude,bin,bbl,'
            'council_district,community_board,census_tract'
        ),
        '$order': 'inspection_date DESC',
        '$limit': 1,
    }
    try:
        r = requests.get(SODA_URL, params=params, headers=_soda_headers(), timeout=30)
        if r.status_code != 200:
            logger.warning('SODA %s for camis=%s', r.status_code, camis)
            return None
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
    except Exception as e:
        logger.warning('SODA error for camis=%s: %s', camis, e)
    return None


def _merge_fresh(raw, fresh):
    """Merge fresh SODA fields into raw without overwriting with blanks."""
    for k in ENRICH_FIELDS:
        v = fresh.get(k)
        if v in (None, ''):
            continue
        raw[k] = v.strip() if isinstance(v, str) else v
    cf = (fresh.get('critical_flag') or '').strip().lower()
    if cf == 'critical':
        raw['has_critical'] = True


class Command(BaseCommand):
    help = 'Backfill urgency + grade_flag on existing NYC health inspection leads'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would change without saving')
        parser.add_argument('--limit', type=int, default=0,
                            help='Cap number of leads processed (0 = all)')
        parser.add_argument('--camis', type=str, default='',
                            help='Target a single CAMIS (restaurant ID)')
        parser.add_argument('--enrich', action='store_true',
                            help='Re-query SODA API per CAMIS to fill missing fields')
        parser.add_argument('--sleep', type=float, default=0.1,
                            help='Seconds to sleep between SODA calls when --enrich')

    def handle(self, *args, **options):
        qs = Lead.objects.filter(source_type='health_inspections').order_by('id')
        if options['camis']:
            qs = qs.filter(raw_data__camis=options['camis'])
        if options['limit']:
            qs = qs[:options['limit']]

        total = qs.count()
        mode_prefix = '[DRY RUN] ' if options['dry_run'] else ''
        enrich_suffix = ' (+SODA enrich)' if options['enrich'] else ''
        self.stdout.write(
            f'{mode_prefix}Scanning {total} health-inspection leads{enrich_suffix}...'
        )

        stats = {
            'changed': 0, 'unchanged': 0, 'skipped_empty': 0,
            'skipped_wrong_source': 0, 'enriched': 0, 'enrich_failed': 0,
            'hot': 0, 'warm': 0, 'new': 0,
            'flag_closed': 0, 'flag_grade_c': 0, 'flag_grade_b': 0,
            'flag_pending': 0, 'flag_critical_ungraded': 0, 'flag_none': 0,
        }

        for lead in qs.iterator():
            raw = lead.raw_data if isinstance(lead.raw_data, dict) else {}
            if not raw:
                stats['skipped_empty'] += 1
                continue
            if raw.get('data_source') != 'nyc_dohmh':
                # Defensive — only touch records we know came from this monitor
                stats['skipped_wrong_source'] += 1
                continue

            # Optional API refresh
            if options['enrich'] and raw.get('camis'):
                fresh = _fetch_latest_by_camis(raw['camis'])
                if fresh:
                    _merge_fresh(raw, fresh)
                    stats['enriched'] += 1
                else:
                    stats['enrich_failed'] += 1
                if options['sleep']:
                    time.sleep(options['sleep'])

            new_level, new_score, new_flag, new_note = _compute_flags(raw)
            old_level = lead.urgency_level
            old_score = lead.urgency_score
            old_flag = raw.get('grade_flag', '')

            no_change = (
                new_level == old_level
                and new_score == old_score
                and new_flag == old_flag
                and not options['enrich']
            )
            if no_change:
                stats['unchanged'] += 1
                continue

            raw['grade_flag'] = new_flag
            raw['urgency'] = new_level
            raw['urgency_note'] = new_note

            biz = (raw.get('business_name') or raw.get('dba') or '?')[:40]
            self.stdout.write(
                f'  {mode_prefix}#{lead.id:>6} {biz:<40} '
                f'{old_level}({old_score}) -> {new_level}({new_score})  '
                f'flag={new_flag or "-"}'
            )

            if not options['dry_run']:
                lead.urgency_level = new_level
                lead.urgency_score = new_score
                lead.raw_data = raw
                lead.save(update_fields=['urgency_level', 'urgency_score', 'raw_data'])

            stats['changed'] += 1
            if new_level in stats:
                stats[new_level] += 1
            flag_key = f'flag_{new_flag}' if new_flag else 'flag_none'
            stats[flag_key] = stats.get(flag_key, 0) + 1

        verb = 'would change' if options['dry_run'] else 'changed'
        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {verb} {stats["changed"]}, '
            f'unchanged {stats["unchanged"]}, '
            f'skipped_empty {stats["skipped_empty"]}, '
            f'skipped_wrong_source {stats["skipped_wrong_source"]}'
        ))
        if options['enrich']:
            self.stdout.write(
                f'  enrich: success {stats["enriched"]}, failed {stats["enrich_failed"]}'
            )
        self.stdout.write(
            f'  urgency: hot={stats["hot"]} warm={stats["warm"]} new={stats["new"]}'
        )
        self.stdout.write(
            f'  flags: closed={stats["flag_closed"]} '
            f'grade_c={stats["flag_grade_c"]} '
            f'grade_b={stats["flag_grade_b"]} '
            f'pending={stats["flag_pending"]} '
            f'critical_ungraded={stats["flag_critical_ungraded"]} '
            f'none={stats["flag_none"]}'
        )
