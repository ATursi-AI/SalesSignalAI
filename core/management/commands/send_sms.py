"""
Send SMS via SignalWire.

Usage:
    python manage.py send_sms --to "+15165551234" --message "Hey Joe - check this out"
    python manage.py send_sms --to "+15165551234" --message "See your video" --lead-id 123
"""
from django.core.management.base import BaseCommand

from core.services import signalwire_service
from core.models import SMSMessage, Lead


class Command(BaseCommand):
    help = 'Send an SMS via SignalWire'

    def add_arguments(self, parser):
        parser.add_argument('--to', type=str, required=True, help='Phone number to send to (E.164 format)')
        parser.add_argument('--message', type=str, required=True, help='Message body')
        parser.add_argument('--media-url', type=str, default=None, help='Optional media URL')
        parser.add_argument('--lead-id', type=int, default=None, help='Optional lead ID to link to')

    def handle(self, *args, **options):
        to = options['to']
        message = options['message']
        media_url = options['media_url']
        lead_id = options['lead_id']

        self.stdout.write(f'Sending SMS to {to}...')
        result = signalwire_service.send_sms(to, message, media_url=media_url)

        if result.get('ok'):
            lead = None
            if lead_id:
                lead = Lead.objects.filter(pk=lead_id).first()

            SMSMessage.objects.create(
                message_sid=result.get('sid', ''),
                direction='outbound',
                from_number=signalwire_service._from_number(),
                to_number=to,
                body=message,
                media_url=media_url or '',
                status=result.get('status', 'sent'),
                lead=lead,
            )
            self.stdout.write(self.style.SUCCESS(f'SMS sent! SID: {result["sid"]}'))
        else:
            self.stdout.write(self.style.ERROR(f'Failed: {result.get("error")}'))
