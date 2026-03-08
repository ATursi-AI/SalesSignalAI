"""
Email validation via ZeroBounce API.
Validates prospect email addresses before sending outreach emails.
"""
import logging

import requests
from django.conf import settings

from core.models import ProspectBusiness

logger = logging.getLogger(__name__)


def validate_email(email):
    """
    Validate a single email address via ZeroBounce.

    Returns:
        dict with:
            status: 'valid', 'invalid', 'catch-all', 'unknown', 'error'
            sub_status: detailed status from ZeroBounce
            did_you_mean: suggested correction (if any)
    """
    api_key = getattr(settings, 'ZEROBOUNCE_API_KEY', '')
    if not api_key:
        logger.warning('ZEROBOUNCE_API_KEY not configured — skipping validation')
        return {'status': 'unknown', 'sub_status': 'api_not_configured', 'did_you_mean': ''}

    url = 'https://api.zerobounce.net/v2/validate'
    params = {
        'api_key': api_key,
        'email': email,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f'ZeroBounce API error: {resp.status_code}')
            return {'status': 'error', 'sub_status': f'http_{resp.status_code}', 'did_you_mean': ''}

        data = resp.json()
        zb_status = data.get('status', '').lower()
        sub_status = data.get('sub_status', '')
        did_you_mean = data.get('did_you_mean', '')

        # Map ZeroBounce statuses
        if zb_status == 'valid':
            status = 'valid'
        elif zb_status == 'invalid':
            status = 'invalid'
        elif zb_status == 'catch-all':
            status = 'catch-all'
        elif zb_status == 'do_not_mail':
            status = 'invalid'
        else:
            status = 'unknown'

        logger.info(f'Email validation: {email} -> {status} ({sub_status})')
        return {'status': status, 'sub_status': sub_status, 'did_you_mean': did_you_mean}

    except requests.RequestException as e:
        logger.error(f'ZeroBounce request failed: {e}')
        return {'status': 'error', 'sub_status': 'request_failed', 'did_you_mean': ''}


def validate_prospect_email(prospect_id):
    """
    Validate a ProspectBusiness's email and update the record.

    Returns:
        The validation status string.
    """
    try:
        prospect = ProspectBusiness.objects.get(id=prospect_id)
    except ProspectBusiness.DoesNotExist:
        return 'not_found'

    email = prospect.email or prospect.owner_email
    if not email:
        prospect.email_validation_status = 'no_email'
        prospect.save(update_fields=['email_validation_status'])
        return 'no_email'

    result = validate_email(email)
    prospect.email_validation_status = result['status']
    prospect.email_validated = result['status'] == 'valid'
    prospect.save(update_fields=['email_validated', 'email_validation_status'])

    return result['status']


def batch_validate_prospects(prospect_ids):
    """
    Validate emails for a batch of prospects.

    Returns:
        dict with counts: validated, valid, invalid, unknown
    """
    stats = {'validated': 0, 'valid': 0, 'invalid': 0, 'unknown': 0}

    for pid in prospect_ids:
        status = validate_prospect_email(pid)
        stats['validated'] += 1
        if status == 'valid':
            stats['valid'] += 1
        elif status in ('invalid', 'no_email'):
            stats['invalid'] += 1
        else:
            stats['unknown'] += 1

    logger.info(f'Batch validation complete: {stats}')
    return stats
