"""
Seed HealthInspectionSource records for top 5 US jurisdictions.
Usage: python manage.py seed_health_inspection_sources
"""
from django.core.management.base import BaseCommand

from core.models import HealthInspectionSource


SOURCES = [
    {
        'name': 'NYC Restaurant Inspections',
        'jurisdiction': 'New York City',
        'state': 'NY',
        'source_url': 'https://data.cityofnewyork.us/Health/DOHMH-New-York-City-Restaurant-Inspection-Results/43nn-pn8j',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://data.cityofnewyork.us/resource/43nn-pn8j.json',
            'params': {'$limit': 200, '$order': 'inspection_date DESC'},
        },
        'css_selectors': {
            'restaurant_name': 'dba',
            'address': 'building',
            'inspection_date': 'inspection_date',
            'score': 'score',
            'grade': 'grade',
            'violations': 'violation_description',
        },
        'failing_threshold': 28,  # NYC uses inverse scoring — higher = worse
    },
    {
        'name': 'Los Angeles County Health Inspections',
        'jurisdiction': 'Los Angeles County',
        'state': 'CA',
        'source_url': 'https://data.lacounty.gov/Health/Restaurant-and-Market-Health-Violations/jhe4-2nmw',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://data.lacounty.gov/resource/jhe4-2nmw.json',
            'params': {'$limit': 200, '$order': 'activity_date DESC'},
        },
        'css_selectors': {
            'restaurant_name': 'facility_name',
            'address': 'facility_address',
            'inspection_date': 'activity_date',
            'score': 'score',
            'grade': 'grade',
            'violations': 'violation_description',
        },
        'failing_threshold': 70,
    },
    {
        'name': 'Chicago Food Inspections',
        'jurisdiction': 'Chicago',
        'state': 'IL',
        'source_url': 'https://data.cityofchicago.org/Health-Human-Services/Food-Inspections/4ijn-s7e5',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://data.cityofchicago.org/resource/4ijn-s7e5.json',
            'params': {'$limit': 200, '$order': 'inspection_date DESC'},
        },
        'css_selectors': {
            'restaurant_name': 'dba_name',
            'address': 'address',
            'inspection_date': 'inspection_date',
            'score': '',
            'grade': 'results',
            'violations': 'violations',
        },
        'failing_threshold': 70,
    },
    {
        'name': 'Houston Health Inspections',
        'jurisdiction': 'Houston',
        'state': 'TX',
        'source_url': 'https://houston.data.socrata.com/Health/Restaurant-Inspections/bqw3-asgr',
        'scrape_method': 'api',
        'api_config': {
            'endpoint': 'https://houston.data.socrata.com/resource/bqw3-asgr.json',
            'params': {'$limit': 200, '$order': 'inspection_date DESC'},
        },
        'css_selectors': {
            'restaurant_name': 'restaurant_name',
            'address': 'address',
            'inspection_date': 'inspection_date',
            'score': 'score',
            'violations': 'violations',
        },
        'failing_threshold': 70,
    },
    {
        'name': 'Miami-Dade County Inspections',
        'jurisdiction': 'Miami-Dade County',
        'state': 'FL',
        'source_url': 'https://gis-mdc.opendata.arcgis.com/datasets/restaurant-inspections',
        'scrape_method': 'api',
        'css_selectors': {
            'restaurant_name': 'name',
            'address': 'address',
            'inspection_date': 'inspection_date',
            'score': 'total_demerits',
            'violations': 'violation_description',
        },
        'failing_threshold': 70,
    },
]


class Command(BaseCommand):
    help = 'Seed HealthInspectionSource records for top 5 US jurisdictions'

    def handle(self, *args, **options):
        created_count = 0
        for data in SOURCES:
            source, created = HealthInspectionSource.objects.get_or_create(
                jurisdiction=data['jurisdiction'],
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
