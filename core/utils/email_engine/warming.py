"""
Domain warming strategy and send-rate management for outreach emails.

Warming schedule: start at 5/day, ramp over 2 weeks:
  Day 1-2:    5/day
  Day 3-4:   10/day
  Day 5-7:   20/day
  Day 8-10:  35/day
  Day 11-14: 50/day
  Day 15+:   max_emails_per_day (campaign setting)

Also tracks bounce/complaint rates and pauses sending if thresholds exceeded.
"""
import logging
from datetime import date, timedelta

from django.utils import timezone

from core.models.monitoring import EmailSendLog

logger = logging.getLogger(__name__)

# Warming ramp schedule: (day_threshold, daily_limit)
WARMING_SCHEDULE = [
    (2, 5),
    (4, 10),
    (7, 20),
    (10, 35),
    (14, 50),
]

# Safety thresholds — pause sending if exceeded
MAX_BOUNCE_RATE = 5.0    # percent
MAX_COMPLAINT_RATE = 0.5  # percent


def get_warming_day():
    """
    Returns how many days since first email was sent (warming day number).
    Day 1 = first day an email was ever sent.
    """
    first_log = EmailSendLog.objects.filter(emails_sent__gt=0).order_by('date').first()
    if not first_log:
        return 1
    return (date.today() - first_log.date).days + 1


def get_daily_limit(campaign_max=50):
    """
    Returns today's send limit based on warming schedule.
    Respects both warming ramp and campaign-level max.
    """
    warming_day = get_warming_day()

    warming_limit = WARMING_SCHEDULE[-1][1]  # default to highest tier
    for day_threshold, limit in WARMING_SCHEDULE:
        if warming_day <= day_threshold:
            warming_limit = limit
            break

    return min(warming_limit, campaign_max)


def get_today_log():
    """Get or create today's EmailSendLog."""
    today = date.today()
    log, created = EmailSendLog.objects.get_or_create(
        date=today,
        defaults={'warming_limit': get_daily_limit()},
    )
    if created:
        log.warming_limit = get_daily_limit()
        log.save(update_fields=['warming_limit'])
    return log


def can_send_today(campaign_max=50):
    """
    Check if we can send more emails today.
    Returns (allowed: bool, remaining: int, reason: str).
    """
    log = get_today_log()
    limit = get_daily_limit(campaign_max)

    # Update limit on the log
    if log.warming_limit != limit:
        log.warming_limit = limit
        log.save(update_fields=['warming_limit'])

    # Check bounce rate over last 7 days
    week_ago = date.today() - timedelta(days=7)
    recent_logs = EmailSendLog.objects.filter(date__gte=week_ago)
    total_sent = sum(l.emails_sent for l in recent_logs)
    total_bounced = sum(l.emails_bounced for l in recent_logs)
    total_complained = sum(l.emails_complained for l in recent_logs)

    if total_sent >= 10:  # only check rates after meaningful volume
        bounce_rate = total_bounced / total_sent * 100
        if bounce_rate > MAX_BOUNCE_RATE:
            logger.warning(f'Sending paused: bounce rate {bounce_rate:.1f}% exceeds {MAX_BOUNCE_RATE}%')
            return False, 0, f'bounce_rate_{bounce_rate:.1f}%'

        complaint_rate = total_complained / total_sent * 100
        if complaint_rate > MAX_COMPLAINT_RATE:
            logger.warning(f'Sending paused: complaint rate {complaint_rate:.1f}% exceeds {MAX_COMPLAINT_RATE}%')
            return False, 0, f'complaint_rate_{complaint_rate:.1f}%'

    remaining = max(0, limit - log.emails_sent)
    if remaining == 0:
        return False, 0, 'daily_limit_reached'

    return True, remaining, 'ok'


def record_send():
    """Record a successful email send."""
    log = get_today_log()
    log.emails_sent += 1
    log.save(update_fields=['emails_sent'])


def record_delivery():
    """Record a confirmed delivery."""
    log = get_today_log()
    log.emails_delivered += 1
    log.save(update_fields=['emails_delivered'])


def record_bounce():
    """Record a bounce."""
    log = get_today_log()
    log.emails_bounced += 1
    log.save(update_fields=['emails_bounced'])
    logger.warning(f'Bounce recorded. Today: {log.emails_bounced}/{log.emails_sent}')


def record_complaint():
    """Record a spam complaint."""
    log = get_today_log()
    log.emails_complained += 1
    log.save(update_fields=['emails_complained'])
    logger.warning(f'Complaint recorded. Today: {log.emails_complained}/{log.emails_sent}')
