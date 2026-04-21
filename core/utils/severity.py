"""
Shared severity helpers for public-records lead monitors.

One source of truth for mapping raw inspection / violation data to the
(urgency_level, urgency_score, flag, note) tuple that the UI needs.

Two families of sources:

1. Letter-grade health inspections (NYC DOHMH, Vegas SNHD, Arizona county
   depts, California counties, NY State, Maricopa, Pima, etc.)
        -> compute_health_grade_flag(...)

2. Non-graded violations with hazard classes (NYC DOB, HPD, ECB summonses,
   fire violations, facade inspections, code enforcement, etc.)
        -> compute_violation_severity_flag(...)

Each function returns:
    (urgency_level, urgency_score, flag, note)

Where:
    urgency_level: 'hot' | 'warm' | 'new'
    urgency_score: int 0-100 — how bad this is on the urgency scale
    flag:          short string keyed to a UI badge class (may be '')
    note:          human-readable reason ("Grade C — failing", etc.)

Monitors call these helpers and then:
    - pass (urgency_level, urgency_score) to process_lead via urgency_override
    - stash flag into raw_data under the key the UI expects:
        'grade_flag' for health monitors
        'severity_flag' for violation monitors

Keeping this module pure (no Django imports) so it can be unit-tested and
reused from management commands, Celery tasks, and live monitors alike.
"""
from __future__ import annotations

from typing import Optional


# ─── Health Inspections ───────────────────────────────────────────────

# Grade letters that signal a restaurant is failing a re-inspection
# cycle — pending closure or already closed by the health dept.
_PENDING_GRADES = ('Z', 'P')


def compute_health_grade_flag(
    grade: Optional[str],
    action: Optional[str],
    score: Optional[int] = None,
    has_critical: bool = False,
    jurisdiction: Optional[str] = None,
    source_label: str = 'Health dept',
) -> tuple[str, int, str, str]:
    """
    Map a health-inspection record to urgency + flag.

    Works for any jurisdiction that uses letter grades A/B/C (+ variants
    like Z/P/X for pending / closed). Priority order, highest first:

        1. Restaurant closed by dept  (action contains 'closed')
        2. Grade Z or P               (pending closure / re-inspection)
        3. Grade C                    (failing — must improve)
        4. Critical violation + score >= 28 (borderline closure)
        5. Grade B                    (borderline)
        6. Has critical violation but no grade (critical_ungraded)
        7. Score 14-27                (must fix before follow-up)
        8. Otherwise                  (new / minor)

    Args:
        grade:          Letter grade string (case-insensitive). '' / None = no grade.
        action:         Free-text action taken by inspector (DOHMH 'action' field etc.)
        score:          Total demerit score from the inspection.
        has_critical:   True if the record contains any critical flag.
        jurisdiction:   Optional — reserved for future per-jurisdiction tweaks.

    Returns:
        (urgency_level, urgency_score, grade_flag, note)
    """
    grade_u = (grade or '').strip().upper()
    action_s = (action or '').strip()
    action_lower = action_s.lower()
    try:
        score_i = int(score or 0)
    except (ValueError, TypeError):
        score_i = 0

    is_closed = 'closed' in action_lower

    # --- Determine flag (for UI badge) ---
    if is_closed:
        flag = 'closed'
    elif grade_u in _PENDING_GRADES:
        flag = 'pending_closure'
    elif grade_u == 'C':
        flag = 'grade_c'
    elif grade_u == 'B':
        flag = 'grade_b'
    elif has_critical and not grade_u:
        flag = 'critical_ungraded'
    else:
        flag = ''

    # --- Determine urgency ---
    if is_closed:
        label = source_label.upper()
        note = f'{label} CLOSURE — {action_s}' if action_s else f'{source_label} closure'
        return 'hot', 95, flag, note
    if grade_u in _PENDING_GRADES:
        return 'hot', 90, flag, f'Grade {grade_u} — pending closure / re-inspection'
    if grade_u == 'C':
        return 'hot', 85, flag, 'Grade C — failing, must improve before re-inspection'
    if has_critical or score_i >= 28:
        return 'hot', 80, flag, 'CRITICAL violation or score >= 28 — restaurant risks closure'
    if score_i >= 14:
        return 'warm', 65, flag, f'Score {score_i} — must fix before follow-up inspection'
    return 'new', 40, flag, 'Minor violations'


# ─── Building / Code / Fire Violations ────────────────────────────────

# NYC HPD uses numeric hazard classes; other jurisdictions have their own
# shorthand. We normalize to strings '1', '2', '3'.
#   Class 1 = immediately hazardous (must fix in 24h)
#   Class 2 = hazardous
#   Class 3 = non-hazardous


def compute_violation_severity_flag(
    hazard_class: Optional[str] = None,
    is_vacate: bool = False,
    is_immediate: bool = False,
    violation_text: Optional[str] = None,
    jurisdiction: Optional[str] = None,
) -> tuple[str, int, str, str]:
    """
    Map a non-graded violation (DOB, HPD, ECB, fire, facade, code
    enforcement) to urgency + severity_flag.

    Priority, highest first:
        1. Vacate order                 (tenants ordered out)
        2. Immediately hazardous        (class 1 / 'IMMEDIATELY HAZARDOUS')
        3. Hazardous                    (class 2)
        4. Non-hazardous                (class 3)
        5. Otherwise                    (new / minor)

    Args:
        hazard_class:   Normalized class string ('1', '2', '3') or free text.
        is_vacate:      True if the violation includes a vacate order.
        is_immediate:   True if the text/code flags this as immediately hazardous.
        violation_text: Description — used as a fallback heuristic.
        jurisdiction:   Optional — reserved for future per-jurisdiction tweaks.

    Returns:
        (urgency_level, urgency_score, severity_flag, note)
    """
    cls = (hazard_class or '').strip()
    text_lower = (violation_text or '').lower()

    # Heuristic fallback from free text when hazard_class is missing.
    if not is_vacate:
        is_vacate = 'vacate' in text_lower
    if not is_immediate and not cls:
        is_immediate = 'immediately hazardous' in text_lower

    # --- Determine flag ---
    if is_vacate:
        flag = 'vacate'
    elif is_immediate or cls == '1':
        flag = 'class_1'
    elif cls == '2':
        flag = 'class_2'
    elif cls == '3':
        flag = 'class_3'
    else:
        flag = ''

    # --- Determine urgency ---
    if is_vacate:
        return 'hot', 95, flag, 'Vacate order — tenants displaced'
    if is_immediate or cls == '1':
        return 'hot', 90, flag, 'Class 1 — immediately hazardous violation'
    if cls == '2':
        return 'hot', 75, flag, 'Class 2 — hazardous violation'
    if cls == '3':
        return 'warm', 55, flag, 'Class 3 — non-hazardous violation'
    return 'new', 40, flag, 'Violation on record'
