"""
Seed LicensingBoardSource records for top 5 states by contractor volume.
Usage: python manage.py seed_licensing_board_sources
"""
from django.core.management.base import BaseCommand

from core.models import LicensingBoardSource


SOURCES = [
    {
        'name': 'California CSLB — General Contractor',
        'state': 'CA',
        'license_type': 'general contractor',
        'source_url': 'https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
            'business_address': '5',
        },
    },
    {
        'name': 'California CSLB — Plumbing',
        'state': 'CA',
        'license_type': 'plumbing',
        'source_url': 'https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
        },
    },
    {
        'name': 'California CSLB — Electrical',
        'state': 'CA',
        'license_type': 'electrical',
        'source_url': 'https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
        },
    },
    {
        'name': 'Texas TDLR — General Contractor',
        'state': 'TX',
        'license_type': 'general contractor',
        'source_url': 'https://www.tdlr.texas.gov/LicenseSearch/',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
        },
    },
    {
        'name': 'Florida DBPR — General Contractor',
        'state': 'FL',
        'license_type': 'general contractor',
        'source_url': 'https://www.myfloridalicense.com/wl11.asp',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
        },
    },
    {
        'name': 'Florida DBPR — Plumbing',
        'state': 'FL',
        'license_type': 'plumbing',
        'source_url': 'https://www.myfloridalicense.com/wl11.asp',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
        },
    },
    {
        'name': 'New York DOS — Home Improvement Contractor',
        'state': 'NY',
        'license_type': 'general contractor',
        'source_url': 'https://appext20.dos.ny.gov/lcns_public/chk_caseno',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
        },
    },
    {
        'name': 'Illinois IDFPR — Roofing',
        'state': 'IL',
        'license_type': 'roofing',
        'source_url': 'https://online-dfpr.micropact.com/lookup/licenselookup.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'contractor_name': '0',
            'license_number': '1',
            'license_type': '2',
            'expiration_date': '3',
            'status': '4',
        },
    },
]


class Command(BaseCommand):
    help = 'Seed LicensingBoardSource records for top 5 states'

    def handle(self, *args, **options):
        created_count = 0
        for data in SOURCES:
            source, created = LicensingBoardSource.objects.get_or_create(
                state=data['state'],
                license_type=data['license_type'],
                defaults=data,
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {source}")
            else:
                self.stdout.write(f"  Exists:  {source}")

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {created_count} new sources created '
            f'({len(SOURCES) - created_count} already existed).'
        ))
