"""
Email sending backends for SalesSignal outreach campaigns.

Architecture:
  - EmailSender: abstract base class with send_email() method
  - SESEmailSender: Amazon SES via boto3
  - InstantlyEmailSender: Instantly.ai API (stub for future)
  - get_email_sender(): factory function that reads EMAIL_BACKEND setting

Configuration (.env):
  EMAIL_BACKEND=ses (default) or EMAIL_BACKEND=instantly
  AWS_SES_ACCESS_KEY, AWS_SES_SECRET_KEY, AWS_SES_REGION
  INSTANTLY_API_KEY (future)
"""
import logging
import uuid
from abc import ABC, abstractmethod

from django.conf import settings

logger = logging.getLogger(__name__)


class EmailSender(ABC):
    """Abstract base class for email sending backends."""

    @abstractmethod
    def send_email(self, to_email, subject, body, from_email=None,
                   reply_to=None, html_body=None, headers=None):
        """
        Send a single email.

        Args:
            to_email: recipient email address
            subject: email subject line
            body: plain text body
            from_email: sender email (uses default if None)
            reply_to: reply-to email address
            html_body: optional HTML version of the body
            headers: optional dict of custom headers

        Returns:
            dict with 'success' (bool), 'message_id' (str), 'error' (str)
        """
        pass

    @abstractmethod
    def check_quota(self):
        """
        Check remaining send quota.

        Returns:
            dict with 'max_24hr' (int), 'sent_24hr' (int), 'remaining' (int)
        """
        pass


class SESEmailSender(EmailSender):
    """Amazon SES email sender using boto3."""

    def __init__(self):
        self.access_key = getattr(settings, 'AWS_SES_ACCESS_KEY', '')
        self.secret_key = getattr(settings, 'AWS_SES_SECRET_KEY', '')
        self.region = getattr(settings, 'AWS_SES_REGION', 'us-east-1')
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client(
                    'ses',
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    region_name=self.region,
                )
            except ImportError:
                logger.error('boto3 package not installed — pip install boto3')
                raise
        return self._client

    def send_email(self, to_email, subject, body, from_email=None,
                   reply_to=None, html_body=None, headers=None):
        if not self.access_key or not self.secret_key:
            logger.warning('[SES] AWS_SES_ACCESS_KEY / AWS_SES_SECRET_KEY not configured')
            return {'success': False, 'message_id': '', 'error': 'ses_not_configured'}

        from_email = from_email or getattr(settings, 'ALERT_FROM_EMAIL', 'campaigns@salessignalai.com')

        message_body = {'Text': {'Data': body, 'Charset': 'UTF-8'}}
        if html_body:
            message_body['Html'] = {'Data': html_body, 'Charset': 'UTF-8'}

        destination = {'ToAddresses': [to_email]}

        kwargs = {
            'Source': from_email,
            'Destination': destination,
            'Message': {
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': message_body,
            },
        }

        if reply_to:
            kwargs['ReplyToAddresses'] = [reply_to]

        # Custom headers via Tags
        if headers:
            kwargs['Tags'] = [
                {'Name': k, 'Value': str(v)} for k, v in headers.items()
            ]

        try:
            response = self.client.send_email(**kwargs)
            message_id = response.get('MessageId', '')
            logger.info(f'[SES] Email sent to {to_email} (MessageId: {message_id})')
            return {'success': True, 'message_id': message_id, 'error': ''}

        except self.client.exceptions.MessageRejected as e:
            logger.error(f'[SES] Message rejected for {to_email}: {e}')
            return {'success': False, 'message_id': '', 'error': 'message_rejected'}
        except self.client.exceptions.MailFromDomainNotVerifiedException:
            logger.error(f'[SES] Domain not verified for {from_email}')
            return {'success': False, 'message_id': '', 'error': 'domain_not_verified'}
        except Exception as e:
            logger.error(f'[SES] Send failed for {to_email}: {e}')
            return {'success': False, 'message_id': '', 'error': str(e)}

    def check_quota(self):
        try:
            quota = self.client.get_send_quota()
            return {
                'max_24hr': int(quota.get('Max24HourSend', 0)),
                'sent_24hr': int(quota.get('SentLast24Hours', 0)),
                'remaining': int(quota.get('Max24HourSend', 0) - quota.get('SentLast24Hours', 0)),
            }
        except Exception as e:
            logger.error(f'[SES] Failed to get quota: {e}')
            return {'max_24hr': 0, 'sent_24hr': 0, 'remaining': 0}


class InstantlyEmailSender(EmailSender):
    """
    Instantly.ai email sender.
    TODO: Implement when Instantly integration is ready.
    """

    def __init__(self):
        self.api_key = getattr(settings, 'INSTANTLY_API_KEY', '')

    def send_email(self, to_email, subject, body, from_email=None,
                   reply_to=None, html_body=None, headers=None):
        # TODO: Implement Instantly API integration
        # API docs: https://developer.instantly.ai/
        logger.warning('[Instantly] Backend not yet implemented — email not sent')
        return {
            'success': False,
            'message_id': '',
            'error': 'instantly_not_implemented',
        }

    def check_quota(self):
        # TODO: Implement quota check via Instantly API
        return {'max_24hr': 0, 'sent_24hr': 0, 'remaining': 0}


class GmailAPISender(EmailSender):
    """
    Gmail API sender for customers who connect their Gmail account.
    Uses OAuth2 refresh tokens stored on BusinessProfile.
    """

    def __init__(self, refresh_token, user_email):
        self.refresh_token = refresh_token
        self.user_email = user_email
        self.client_id = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
        self.client_secret = getattr(settings, 'GOOGLE_OAUTH_CLIENT_SECRET', '')

    def send_email(self, to_email, subject, body, from_email=None,
                   reply_to=None, html_body=None, headers=None):
        if not self.refresh_token:
            return {'success': False, 'message_id': '', 'error': 'no_gmail_token'}

        try:
            import base64
            from email.mime.text import MIMEText
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=None,
                refresh_token=self.refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=self.client_id,
                client_secret=self.client_secret,
            )

            service = build('gmail', 'v1', credentials=creds)

            message = MIMEText(body)
            message['to'] = to_email
            message['from'] = from_email or self.user_email
            message['subject'] = subject
            if reply_to:
                message['reply-to'] = reply_to

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            result = service.users().messages().send(
                userId='me',
                body={'raw': raw},
            ).execute()

            message_id = result.get('id', '')
            logger.info(f'[Gmail API] Email sent to {to_email} via {self.user_email} (ID: {message_id})')
            return {'success': True, 'message_id': message_id, 'error': ''}

        except ImportError:
            logger.error('[Gmail API] google-auth / google-api-python-client not installed')
            return {'success': False, 'message_id': '', 'error': 'gmail_packages_not_installed'}
        except Exception as e:
            logger.error(f'[Gmail API] Send failed for {to_email}: {e}')
            return {'success': False, 'message_id': '', 'error': str(e)}

    def check_quota(self):
        # Gmail API has per-user limits of ~500/day for regular, ~2000/day for Workspace
        return {'max_24hr': 500, 'sent_24hr': 0, 'remaining': 500}


def get_email_sender(campaign=None):
    """
    Factory function to get the appropriate email sender backend.

    Priority:
    1. If campaign uses Gmail (send_mode='gmail'), use GmailAPISender
    2. Check EMAIL_BACKEND setting: 'ses' (default), 'instantly', 'sendgrid' (legacy)
    """
    # Check if campaign specifies Gmail
    if campaign and campaign.send_mode == 'gmail':
        bp = campaign.business
        gmail_token = getattr(bp, 'gmail_refresh_token', '')
        if gmail_token:
            return GmailAPISender(
                refresh_token=gmail_token,
                user_email=campaign.reply_to_email or bp.email,
            )
        else:
            logger.warning(f'Campaign {campaign.name} set to Gmail but no token — falling back')

    backend = getattr(settings, 'OUTREACH_EMAIL_BACKEND', 'ses')

    if backend == 'ses':
        return SESEmailSender()
    elif backend == 'instantly':
        return InstantlyEmailSender()
    else:
        # Fallback to SES
        return SESEmailSender()
