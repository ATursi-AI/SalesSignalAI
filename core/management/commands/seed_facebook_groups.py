"""
Seed MonitoredFacebookGroup with initial NY/NJ/CT community groups.
Usage: python manage.py seed_facebook_groups

NOTE: You must manually join these groups with your dedicated FB account
before the monitor can scrape them. The group_id values below are
placeholders — replace with actual numeric group IDs after joining.
"""
from django.core.management.base import BaseCommand

from core.models.monitoring import MonitoredFacebookGroup


GROUPS = [
    {
        'name': 'Brooklyn Recommendations',
        'group_id': 'brooklyn_recs',
        'url': 'https://www.facebook.com/groups/brooklynrecs/',
        'keywords': [
            'plumber', 'electrician', 'contractor', 'handyman',
            'painter', 'cleaner', 'mover', 'landscaper',
        ],
    },
    {
        'name': 'Queens Home Improvement',
        'group_id': 'queens_home_improvement',
        'url': 'https://www.facebook.com/groups/queenshomeimprovement/',
        'keywords': [
            'contractor', 'renovation', 'remodel', 'plumber',
            'electrician', 'roofer', 'hvac',
        ],
    },
    {
        'name': 'Jersey City Community',
        'group_id': 'jerseycity_community',
        'url': 'https://www.facebook.com/groups/jerseycitycommunity/',
        'keywords': [
            'plumber', 'electrician', 'handyman', 'cleaning',
            'landscaping', 'moving', 'painter',
        ],
    },
    {
        'name': 'Westchester NY Moms',
        'group_id': 'westchester_moms',
        'url': 'https://www.facebook.com/groups/westchestermoms/',
        'keywords': [
            'recommend', 'contractor', 'plumber', 'electrician',
            'cleaner', 'handyman', 'painter', 'landscaper',
        ],
    },
    {
        'name': 'Long Island Home Services',
        'group_id': 'li_home_services',
        'url': 'https://www.facebook.com/groups/longislandhomeservices/',
        'keywords': [
            'plumber', 'electrician', 'hvac', 'roofer',
            'contractor', 'pest control', 'tree service',
        ],
    },
    {
        'name': 'Hoboken Recommendations',
        'group_id': 'hoboken_recs',
        'url': 'https://www.facebook.com/groups/hobokenrecs/',
        'keywords': [
            'plumber', 'electrician', 'handyman', 'cleaning',
            'mover', 'locksmith', 'painter',
        ],
    },
    {
        'name': 'CT Home Owners Group',
        'group_id': 'ct_homeowners',
        'url': 'https://www.facebook.com/groups/cthomeowners/',
        'keywords': [
            'contractor', 'plumber', 'electrician', 'roofer',
            'hvac', 'landscaping', 'painter', 'tree removal',
        ],
    },
    {
        'name': 'Stamford CT Community',
        'group_id': 'stamford_community',
        'url': 'https://www.facebook.com/groups/stamfordcommunity/',
        'keywords': [
            'plumber', 'electrician', 'handyman', 'contractor',
            'cleaning', 'landscaping', 'painter',
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed MonitoredFacebookGroup with initial NY/NJ/CT community groups'

    def handle(self, *args, **options):
        created_count = 0
        for grp in GROUPS:
            _, created = MonitoredFacebookGroup.objects.get_or_create(
                group_id=grp['group_id'],
                defaults={
                    'name': grp['name'],
                    'url': grp['url'],
                    'keywords': grp['keywords'],
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {grp['name']}")
            else:
                self.stdout.write(f"  Exists:  {grp['name']}")

        self.stdout.write(self.style.SUCCESS(
            f'Seeded {created_count} new groups ({len(GROUPS)} total)'
        ))
        self.stdout.write(self.style.WARNING(
            '\nIMPORTANT: Update group_id and url fields with actual Facebook '
            'group IDs after joining them with your dedicated account.'
        ))
