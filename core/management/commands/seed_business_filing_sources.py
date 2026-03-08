"""
Seed StateBusinessFilingSource with the top 10 US states by business
formation volume. Each entry stores the correct portal URL and scrape
configuration for that state's corporation database.

Usage: python manage.py seed_business_filing_sources
"""
from django.core.management.base import BaseCommand

from core.models import StateBusinessFilingSource


FILING_SOURCES = [
    {
        'state': 'DE',
        'state_name': 'Delaware',
        'source_url': 'https://icis.corp.delaware.gov/ecorp/entitysearch/namesearch.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table#ctl00_ContentPlaceHolder1_GridView1',
            'row_selector': 'tr',
            'business_name': '0',
            'filing_date': '1',
            'entity_type': '2',
            'status': '3',
        },
        'search_params': {'date_range_days': 14, 'entity_types': ['LLC', 'Corporation']},
    },
    {
        'state': 'CA',
        'state_name': 'California',
        'source_url': 'https://bizfileonline.sos.ca.gov/search/business',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table.table',
            'row_selector': 'tbody tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'status': '3',
            'address': '4',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'TX',
        'state_name': 'Texas',
        'source_url': 'https://mycpa.cpa.state.tx.us/coa/',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table',
            'row_selector': 'tr',
            'business_name': '0',
            'filing_date': '1',
            'entity_type': '2',
            'address': '3',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'FL',
        'state_name': 'Florida',
        'source_url': 'https://search.sunbiz.org/Inquiry/CorporationSearch/ByName',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table.searchResultTable',
            'row_selector': 'tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'status': '3',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'NY',
        'state_name': 'New York',
        'source_url': 'https://appext20.dos.ny.gov/corp_public/CORPSEARCH.ENTITY_SEARCH_ENTRY',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table',
            'row_selector': 'tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'address': '3',
            'status': '4',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'WY',
        'state_name': 'Wyoming',
        'source_url': 'https://wyobiz.wyo.gov/Business/FilingSearch.aspx',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table#MainContent_SearchResults',
            'row_selector': 'tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'status': '3',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'NV',
        'state_name': 'Nevada',
        'source_url': 'https://esos.nv.gov/EntitySearch/OnlineEntitySearch',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table.table',
            'row_selector': 'tbody tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'status': '3',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'IL',
        'state_name': 'Illinois',
        'source_url': 'https://www.ilsos.gov/corporatellc/CorporateLlcController',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table',
            'row_selector': 'tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'address': '3',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'NJ',
        'state_name': 'New Jersey',
        'source_url': 'https://www.njportal.com/DOR/BusinessNameSearch',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table.table',
            'row_selector': 'tbody tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'status': '3',
        },
        'search_params': {'date_range_days': 14},
    },
    {
        'state': 'GA',
        'state_name': 'Georgia',
        'source_url': 'https://ecorp.sos.ga.gov/BusinessSearch',
        'scrape_method': 'html_table',
        'css_selectors': {
            'table_selector': 'table#SearchResults',
            'row_selector': 'tbody tr',
            'business_name': '0',
            'entity_type': '1',
            'filing_date': '2',
            'address': '3',
            'status': '4',
        },
        'search_params': {'date_range_days': 14},
    },
]


class Command(BaseCommand):
    help = 'Seed StateBusinessFilingSource with top 10 US states by business formation volume'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Seeding State Business Filing Sources...'))

        created_count = 0
        for data in FILING_SOURCES:
            search_params = data.pop('search_params', {})
            _, created = StateBusinessFilingSource.objects.get_or_create(
                state=data['state'],
                defaults={**data, 'search_params': search_params},
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {data['state_name']} ({data['state']})")
            else:
                self.stdout.write(f"  Exists:  {data['state_name']} ({data['state']})")

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {created_count} state filing sources ({len(FILING_SOURCES)} total)'
        ))
