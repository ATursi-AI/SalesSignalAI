"""
Seed CodeViolationSource records for the top 10 largest US cities.
Usage: python manage.py seed_code_violation_sources
"""
from django.core.management.base import BaseCommand

from core.models import CodeViolationSource


SOURCES = [
    {
        'name': 'NYC DOB Violations',
        'municipality': 'New York City',
        'county': 'New York',
        'state': 'NY',
        'source_url': 'https://data.cityofnewyork.us/Housing-Development/DOB-Violations/3h2n-5cm9',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://data.cityofnewyork.us/resource/3h2n-5cm9.json',
            'params': {'$limit': 200, '$order': 'issue_date DESC'},
            'result_key': '',
        },
        'css_selectors': {
            'address': 'house__',
            'violation_type': 'violation_type',
            'violation_date': 'issue_date',
            'status': 'disposition_comments',
        },
    },
    {
        'name': 'Los Angeles Code Enforcement',
        'municipality': 'Los Angeles',
        'county': 'Los Angeles',
        'state': 'CA',
        'source_url': 'https://data.lacity.org/Housing-and-Real-Estate/Code-Enforcement-Cases/2uz8-3tj3',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://data.lacity.org/resource/2uz8-3tj3.json',
            'params': {'$limit': 200, '$order': 'date_case_generated DESC'},
        },
        'css_selectors': {
            'address': 'address',
            'violation_type': 'case_type',
            'violation_date': 'date_case_generated',
            'status': 'case_status',
        },
    },
    {
        'name': 'Chicago Code Violations',
        'municipality': 'Chicago',
        'county': 'Cook',
        'state': 'IL',
        'source_url': 'https://data.cityofchicago.org/Buildings/Building-Violations/22u3-xenr',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://data.cityofchicago.org/resource/22u3-xenr.json',
            'params': {'$limit': 200, '$order': 'violation_date DESC'},
        },
        'css_selectors': {
            'address': 'address',
            'violation_type': 'violation_description',
            'violation_date': 'violation_date',
            'status': 'violation_status',
        },
    },
    {
        'name': 'Houston Code Enforcement',
        'municipality': 'Houston',
        'county': 'Harris',
        'state': 'TX',
        'source_url': 'https://cohgis-mycity.opendata.arcgis.com/datasets/code-enforcement-cases',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://services.arcgis.com/query',
            'params': {'resultRecordCount': 200, 'orderByFields': 'CreatedDate DESC'},
        },
        'css_selectors': {
            'address': 'Address',
            'violation_type': 'ViolationType',
            'violation_date': 'CreatedDate',
            'status': 'Status',
        },
    },
    {
        'name': 'Phoenix Code Enforcement',
        'municipality': 'Phoenix',
        'county': 'Maricopa',
        'state': 'AZ',
        'source_url': 'https://www.phoenix.gov/nsd/programs/code-enforcement',
        'scrape_method': 'html_table',
        'css_selectors': {
            'address': '0',
            'violation_type': '1',
            'violation_date': '2',
            'status': '3',
        },
    },
    {
        'name': 'Philadelphia L&I Violations',
        'municipality': 'Philadelphia',
        'county': 'Philadelphia',
        'state': 'PA',
        'source_url': 'https://phl.carto.com/api/v2/sql?q=SELECT+*+FROM+violations',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://phl.carto.com/api/v2/sql',
            'params': {'q': 'SELECT * FROM violations ORDER BY violationdate DESC LIMIT 200'},
            'result_key': 'rows',
        },
        'css_selectors': {
            'address': 'address',
            'violation_type': 'violationcodetitle',
            'violation_date': 'violationdate',
            'status': 'violationstatus',
        },
    },
    {
        'name': 'San Antonio Code Compliance',
        'municipality': 'San Antonio',
        'county': 'Bexar',
        'state': 'TX',
        'source_url': 'https://data.sanantonio.gov/dataset/code-compliance-cases',
        'scrape_method': 'api',
        'css_selectors': {
            'address': 'address',
            'violation_type': 'violation_type',
            'violation_date': 'created_date',
            'status': 'status',
        },
    },
    {
        'name': 'San Diego Code Enforcement',
        'municipality': 'San Diego',
        'county': 'San Diego',
        'state': 'CA',
        'source_url': 'https://data.sandiego.gov/datasets/code-enforcement-violations/',
        'scrape_method': 'api',
        'css_selectors': {
            'address': 'address',
            'violation_type': 'violation_name',
            'violation_date': 'date_opened',
            'status': 'case_status',
        },
    },
    {
        'name': 'Dallas Code Compliance',
        'municipality': 'Dallas',
        'county': 'Dallas',
        'state': 'TX',
        'source_url': 'https://www.dallasopendata.com/Services/Code-Compliance-Active-Cases/cdiy-cuz4',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://www.dallasopendata.com/resource/cdiy-cuz4.json',
            'params': {'$limit': 200, '$order': 'date_filed DESC'},
        },
        'css_selectors': {
            'address': 'full_address',
            'violation_type': 'cas_violation_type_desc',
            'violation_date': 'date_filed',
            'status': 'cas_status',
        },
    },
    {
        'name': 'Jacksonville Code Enforcement',
        'municipality': 'Jacksonville',
        'county': 'Duval',
        'state': 'FL',
        'source_url': 'https://data.coj.net/datasets/code-enforcement-cases',
        'scrape_method': 'api',
        'css_selectors': {
            'address': 'address',
            'violation_type': 'violation_type',
            'violation_date': 'date_opened',
            'status': 'status',
        },
    },
]


class Command(BaseCommand):
    help = 'Seed CodeViolationSource records for top 10 US cities'

    def handle(self, *args, **options):
        created_count = 0
        for data in SOURCES:
            source, created = CodeViolationSource.objects.get_or_create(
                municipality=data['municipality'],
                state=data['state'],
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
