"""Register the Telegram bot webhook with Telegram's API."""
import requests
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Register or manage the Telegram bot webhook'

    def add_arguments(self, parser):
        parser.add_argument('--url', type=str, default='https://salessignalai.com')
        parser.add_argument('--remove', action='store_true', help='Remove webhook')
        parser.add_argument('--info', action='store_true', help='Get webhook info')

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            self.stdout.write(self.style.ERROR('TELEGRAM_BOT_TOKEN not set in .env'))
            return

        if options['info']:
            r = requests.get(f'https://api.telegram.org/bot{token}/getWebhookInfo')
            self.stdout.write(str(r.json()))
            return

        if options['remove']:
            r = requests.post(f'https://api.telegram.org/bot{token}/deleteWebhook')
            self.stdout.write(str(r.json()))
            return

        wh = f"{options['url']}/api/telegram/webhook/"
        r = requests.post(f'https://api.telegram.org/bot{token}/setWebhook', json={'url': wh})
        result = r.json()
        if result.get('ok'):
            self.stdout.write(self.style.SUCCESS(f'Webhook set: {wh}'))
        else:
            self.stdout.write(self.style.ERROR(f'Failed: {result}'))
