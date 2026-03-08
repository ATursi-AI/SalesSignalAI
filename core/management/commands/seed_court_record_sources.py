"""
Seed CourtRecordSource records for top 5 US counties by population.
Usage: python manage.py seed_court_record_sources
"""
from django.core.management.base import BaseCommand

from core.models import CourtRecordSource


SOURCES = [
    {
        'name': 'Los Angeles County Court — Evictions',
        'county': 'Los Angeles',
        'state': 'CA',
        'source_url': 'https://www.lacourt.org/casesummary/ui/',
        'scrape_method': 'html_table',
        'css_selectors': {
            'address': '0',
            'filing_date': '1',
            'case_number': '2',
            'plaintiff': '3',
            'property_type': '4',
            'status': '5',
        },
    },
    {
        'name': 'Cook County Court — Evictions',
        'county': 'Cook',
        'state': 'IL',
        'source_url': 'https://www.cookcountyclerkofcourt.org/NewWebsite/CaseLookup',
        'scrape_method': 'html_table',
        'css_selectors': {
            'address': '0',
            'filing_date': '1',
            'case_number': '2',
            'plaintiff': '3',
            'property_type': '4',
        },
    },
    {
        'name': 'Harris County Court — Evictions',
        'county': 'Harris',
        'state': 'TX',
        'source_url': 'https://www.hcdistrictclerk.com/edocs/public/search.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'address': '0',
            'filing_date': '1',
            'case_number': '2',
            'plaintiff': '3',
        },
    },
    {
        'name': 'Maricopa County Court — Evictions',
        'county': 'Maricopa',
        'state': 'AZ',
        'source_url': 'https://www.superiorcourt.maricopa.gov/docket/CivilCourtCases/',
        'scrape_method': 'html_table',
        'css_selectors': {
            'address': '0',
            'filing_date': '1',
            'case_number': '2',
            'plaintiff': '3',
        },
    },
    {
        'name': 'Miami-Dade County Court — Evictions',
        'county': 'Miami-Dade',
        'state': 'FL',
        'source_url': 'https://www2.miami-dadeclerk.com/cjis/CaseSearch.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'address': '0',
            'filing_date': '1',
            'case_number': '2',
            'plaintiff': '3',
            'property_type': '4',
        },
    },
]


class Command(BaseCommand):
    help = 'Seed CourtRecordSource records for top 5 US counties'

    def handle(self, *args, **options):
        created_count = 0
        for data in SOURCES:
            source, created = CourtRecordSource.objects.get_or_create(
                county=data['county'],
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
