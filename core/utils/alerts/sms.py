import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def send_sms(to_number, message):
    """Send an SMS via Twilio. Returns True on success, False on failure."""
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    from_number = settings.TWILIO_PHONE_NUMBER

    if not account_sid or not auth_token or not from_number:
        logger.warning(
            f"Twilio not configured. Would have sent SMS to {to_number}: {message[:80]}..."
        )
        return False

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        msg = client.messages.create(
            body=message,
            from_=from_number,
            to=to_number,
        )
        logger.info(f"SMS sent to {to_number}, SID: {msg.sid}")
        return True
    except ImportError:
        logger.warning("twilio package not installed. SMS not sent.")
        return False
    except Exception as e:
        logger.error(f"Failed to send SMS to {to_number}: {e}")
        return False
