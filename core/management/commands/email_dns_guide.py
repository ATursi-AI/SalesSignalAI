"""
Print SPF/DKIM/DMARC DNS configuration guide for email deliverability.

Usage:
    python manage.py email_dns_guide --domain yourdomain.com
"""
from django.core.management.base import BaseCommand
from core.utils.email_engine.dns_config import get_dns_guide


class Command(BaseCommand):
    help = 'Print DNS configuration guide for email deliverability (SPF, DKIM, DMARC)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--domain', type=str, default='yourdomain.com',
            help='Your sending domain',
        )
        parser.add_argument(
            '--sendgrid-id', type=str, default='XXXXXXX',
            help='Your SendGrid account ID',
        )

    def handle(self, *args, **options):
        guide = get_dns_guide(
            domain=options['domain'],
            sendgrid_id=options['sendgrid_id'],
        )
        self.stdout.write(guide)
