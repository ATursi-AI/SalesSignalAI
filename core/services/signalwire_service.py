"""
SignalWire service wrapper for SalesSignal AI.
Handles SMS sending/receiving and voice calls via the SignalWire REST API.
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_client():
    """Get a SignalWire REST client."""
    from signalwire.rest import Client
    project_id = settings.SIGNALWIRE_PROJECT_ID
    api_token = settings.SIGNALWIRE_API_TOKEN
    space_url = settings.SIGNALWIRE_SPACE_URL
    if not all([project_id, api_token, space_url]):
        raise RuntimeError('SignalWire credentials not configured. Set SIGNALWIRE_PROJECT_ID, SIGNALWIRE_API_TOKEN, SIGNALWIRE_SPACE_URL.')
    return Client(project_id, api_token, signalwire_space_url=space_url)


def _from_number():
    return settings.SIGNALWIRE_PHONE_NUMBER


# ── SMS ──────────────────────────────────────────────────────────────

def send_sms(to_number, message, media_url=None):
    """Send a single SMS. Returns dict with sid, status, or error."""
    from core.models import SMSOptOut
    # Check opt-out
    if SMSOptOut.objects.filter(phone_number=to_number).exists():
        logger.warning(f'[signalwire] Blocked SMS to opted-out number {to_number}')
        return {'ok': False, 'error': 'Number has opted out'}

    try:
        client = _get_client()
        kwargs = {
            'from_': _from_number(),
            'to': to_number,
            'body': message,
        }
        if media_url:
            kwargs['media_url'] = [media_url]
        msg = client.messages.create(**kwargs)
        logger.info(f'[signalwire] SMS sent to {to_number}: sid={msg.sid}')
        return {'ok': True, 'sid': msg.sid, 'status': msg.status}
    except Exception as e:
        logger.error(f'[signalwire] SMS send error to {to_number}: {e}')
        return {'ok': False, 'error': str(e)}


def send_bulk_sms(recipients):
    """Send SMS to multiple recipients. recipients = [{number, message}, ...]"""
    results = []
    for r in recipients:
        result = send_sms(r['number'], r['message'], r.get('media_url'))
        result['number'] = r['number']
        results.append(result)
    sent = sum(1 for r in results if r.get('ok'))
    logger.info(f'[signalwire] Bulk SMS: {sent}/{len(recipients)} sent')
    return results


# ── Voice ────────────────────────────────────────────────────────────

def make_call(to_number, from_number=None, webhook_url=None):
    """Place an outbound call. Returns dict with sid, status, or error."""
    try:
        client = _get_client()
        kwargs = {
            'from_': from_number or _from_number(),
            'to': to_number,
            'record': True,
        }
        if webhook_url:
            kwargs['url'] = webhook_url
        else:
            # Default: just connect the call
            kwargs['url'] = f'https://{settings.SIGNALWIRE_SPACE_URL}/laml-bins/connect'
        call = client.calls.create(**kwargs)
        logger.info(f'[signalwire] Call placed to {to_number}: sid={call.sid}')
        return {'ok': True, 'sid': call.sid, 'status': call.status}
    except Exception as e:
        logger.error(f'[signalwire] Call error to {to_number}: {e}')
        return {'ok': False, 'error': str(e)}


def get_call_status(call_sid):
    """Get status of a call by SID."""
    try:
        client = _get_client()
        call = client.calls(call_sid).fetch()
        return {
            'ok': True,
            'sid': call.sid,
            'status': call.status,
            'duration': call.duration,
            'from': call.from_,
            'to': call.to,
        }
    except Exception as e:
        logger.error(f'[signalwire] Call status error for {call_sid}: {e}')
        return {'ok': False, 'error': str(e)}


# ── Number Management ────────────────────────────────────────────────

def list_phone_numbers():
    """List all phone numbers on the account."""
    try:
        client = _get_client()
        numbers = client.incoming_phone_numbers.list()
        return {
            'ok': True,
            'numbers': [{'sid': n.sid, 'number': n.phone_number, 'friendly': n.friendly_name} for n in numbers],
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def buy_phone_number(area_code):
    """Buy a phone number with the given area code."""
    try:
        client = _get_client()
        available = client.available_phone_numbers('US').local.list(area_code=area_code, limit=1)
        if not available:
            return {'ok': False, 'error': f'No numbers available for area code {area_code}'}
        number = client.incoming_phone_numbers.create(phone_number=available[0].phone_number)
        return {'ok': True, 'number': number.phone_number, 'sid': number.sid}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ── Logs ─────────────────────────────────────────────────────────────

def get_call_logs(days=7):
    """Get recent call logs from SignalWire."""
    from datetime import datetime, timedelta
    try:
        client = _get_client()
        since = datetime.utcnow() - timedelta(days=days)
        calls = client.calls.list(start_time_after=since, limit=100)
        return {
            'ok': True,
            'calls': [{
                'sid': c.sid, 'from': c.from_, 'to': c.to,
                'status': c.status, 'duration': c.duration,
                'direction': c.direction, 'start_time': str(c.start_time),
            } for c in calls],
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def get_sms_logs(days=7):
    """Get recent SMS logs from SignalWire."""
    from datetime import datetime, timedelta
    try:
        client = _get_client()
        since = datetime.utcnow() - timedelta(days=days)
        messages = client.messages.list(date_sent_after=since, limit=100)
        return {
            'ok': True,
            'messages': [{
                'sid': m.sid, 'from': m.from_, 'to': m.to,
                'body': m.body, 'status': m.status,
                'direction': m.direction, 'date_sent': str(m.date_sent),
            } for m in messages],
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)}
