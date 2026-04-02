"""
Seed PermitSource and PropertyTransferSource with initial records
for the 5 most populated US counties. These serve as configuration
templates showing how to set up sources for different portal structures.

Usage: python manage.py seed_permit_sources
"""
from django.core.management.base import BaseCommand

from core.models import PermitSource, PropertyTransferSource


PERMIT_SOURCES = [
    {
        'name': 'Los Angeles County Building Permits',
        'county': 'Los Angeles',
        'state': 'CA',
        'source_url': 'https://www.ladbsservices2.lacity.org/OnlineServices/PermitReport/PermitResults',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table.grid',
            'row_selector': 'tr',
            'permit_type': '1',
            'address': '2',
            'filing_date': '3',
            'estimated_value': '4',
            'owner_name': '5',
            'status': '6',
        },
    },
    {
        'name': 'Cook County (Chicago) Building Permits',
        'county': 'Cook',
        'state': 'IL',
        'source_url': 'https://data.cityofchicago.org/resource/ydr8-5enu.json',
        'scrape_method': 'api',
        'css_selectors': {
            'permit_type': 'permit_type',
            'address': 'street_number',
            'filing_date': 'issue_date',
            'estimated_value': 'estimated_cost',
            'contractor_name': 'contractor_1_name',
        },
        'api_config': {
            'endpoint': 'https://data.cityofchicago.org/resource/ydr8-5enu.json',
            'params': {'$limit': 50, '$order': 'issue_date DESC'},
            'result_key': '',
        },
    },
    {
        'name': 'Harris County (Houston) Building Permits',
        'county': 'Harris',
        'state': 'TX',
        'source_url': 'https://www.houstontx.gov/planning/Permitting/permit-activity.html',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table',
            'row_selector': 'tr',
            'permit_type': '0',
            'address': '1',
            'filing_date': '2',
            'estimated_value': '3',
            'owner_name': '4',
        },
    },
    {
        'name': 'Maricopa County (Phoenix) Building Permits',
        'county': 'Maricopa',
        'state': 'AZ',
        'source_url': 'https://planning.phoenix.gov/permits',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table.permit-table, table',
            'row_selector': 'tr',
            'permit_type': '0',
            'address': '1',
            'filing_date': '2',
            'estimated_value': '3',
            'status': '4',
        },
    },
    {
        'name': 'San Diego County Building Permits',
        'county': 'San Diego',
        'state': 'CA',
        'source_url': 'https://www.sandiegocounty.gov/pds/bldg/bpermits.html',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table',
            'row_selector': 'tr',
            'permit_type': '0',
            'address': '1',
            'filing_date': '2',
            'estimated_value': '3',
        },
    },
]


PROPERTY_SOURCES = [
    {
        'name': 'Los Angeles County Recorder',
        'county': 'Los Angeles',
        'state': 'CA',
        'source_url': 'https://www.lavote.gov/home/records/property-document-recording',
        'scrape_method': 'apify_zillow',
        'css_selectors': {},
        'api_config': {
            'search_area': 'Los Angeles, CA',
            'max_items': 50,
            'days': 30,
        },
    },
    {
        'name': 'Cook County (Chicago) Recorder',
        'county': 'Cook',
        'state': 'IL',
        'source_url': 'https://www.cookcountyrecorder.com/',
        'scrape_method': 'apify_zillow',
        'css_selectors': {},
        'api_config': {
            'search_area': 'Chicago, IL',
            'max_items': 50,
            'days': 30,
        },
    },
    {
        'name': 'Harris County (Houston) Clerk',
        'county': 'Harris',
        'state': 'TX',
        'source_url': 'https://www.cclerk.hctx.net/Applications/WebSearch/RP.aspx',
        'scrape_method': 'apify_zillow',
        'css_selectors': {},
        'api_config': {
            'search_area': 'Houston, TX',
            'max_items': 50,
            'days': 30,
        },
    },
    {
        'name': 'Maricopa County (Phoenix) Recorder',
        'county': 'Maricopa',
        'state': 'AZ',
        'source_url': 'https://recorder.maricopa.gov/',
        'scrape_method': 'apify_zillow',
        'css_selectors': {},
        'api_config': {
            'search_area': 'Phoenix, AZ',
            'max_items': 50,
            'days': 30,
        },
    },
    {
        'name': 'San Diego County Recorder',
        'county': 'San Diego',
        'state': 'CA',
        'source_url': 'https://arcc.sdcounty.ca.gov/',
        'scrape_method': 'apify_zillow',
        'css_selectors': {},
        'api_config': {
            'search_area': 'San Diego, CA',
            'max_items': 50,
            'days': 30,
        },
    },
]


class Command(BaseCommand):
    help = 'Seed PermitSource and PropertyTransferSource with 5 initial US county records each'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Seeding Permit Sources...'))
        permit_count = 0
        for data in PERMIT_SOURCES:
            api_config = data.pop('api_config', {})
            _, created = PermitSource.objects.get_or_create(
                county=data['county'],
                state=data['state'],
                defaults={**data, 'api_config': api_config},
            )
            if created:
                permit_count += 1
                self.stdout.write(f"  Created: {data['name']}")
            else:
                self.stdout.write(f"  Exists:  {data['name']}")

        self.stdout.write('')
        self.stdout.write(self.style.HTTP_INFO('Seeding Property Transfer Sources...'))
        property_count = 0
        for data in PROPERTY_SOURCES:
            api_config = data.pop('api_config', {})
            _, created = PropertyTransferSource.objects.get_or_create(
                county=data['county'],
                state=data['state'],
                defaults={**data, 'api_config': api_config},
            )
            if created:
                property_count += 1
                self.stdout.write(f"  Created: {data['name']}")
            else:
                self.stdout.write(f"  Exists:  {data['name']}")

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {permit_count} permit sources + {property_count} property sources '
            f'({len(PERMIT_SOURCES)} + {len(PROPERTY_SOURCES)} total)'
        ))
