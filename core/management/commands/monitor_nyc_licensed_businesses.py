"""
Management command to monitor NYC DCWP licensed businesses.

Usage:
    python manage.py monitor_nyc_licensed_businesses --mode expired --days 30
    python manage.py monitor_nyc_licensed_businesses --mode new --days 14
    python manage.py monitor_nyc_licensed_businesses --mode expired --category "Home Improvement Contractor" --dry-run
"""
from django.core.management.base import BaseCommand

from core.utils.monitors.nyc_licensed_businesses import monitor_nyc_licensed_businesses


class Command(BaseCommand):
    help = (
        'Monitor NYC DCWP licensed businesses for expired licenses '
        '(orphaned customers) or newly issued licenses (new businesses needing services). '
        '50K+ businesses with phone numbers.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode', type=str, default='expired', choices=['expired', 'new'],
            help='expired = recently expired licenses, new = recently issued (default: expired)',
        )
        parser.add_argument(
            '--days', type=int, default=30,
            help='Look back this many days (default: 30)',
        )
        parser.add_argument(
            '--category', type=str, default=None,
            help='Filter by business_category (e.g. "Home Improvement Contractor")',
        )
        parser.add_argument(
            '--borough', type=str, default=None,
            help='Filter by borough (e.g. Brooklyn, Queens)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Log matches without creating Lead records',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO(
            f'Starting NYC Licensed Businesses monitor ({options["mode"]})...'
        ))
        self.stdout.write(f'  Mode:     {options["mode"]}')
        self.stdout.write(f'  Days:     {options["days"]}')
        if options['category']:
            self.stdout.write(f'  Category: {options["category"]}')
        if options['borough']:
            self.stdout.write(f'  Borough:  {options["borough"]}')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  DRY RUN MODE'))

        stats = monitor_nyc_licensed_businesses(
            mode=options['mode'],
            days=options['days'],
            category=options.get('category'),
            borough=options.get('borough'),
            dry_run=options['dry_run'],
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('NYC Licensed Businesses Results:'))
        self.stdout.write(f'  Sources checked:    {stats["sources_checked"]}')
        self.stdout.write(f'  Items scraped:      {stats["items_scraped"]}')
        self.stdout.write(f'  Leads created:      {stats["created"]}')
        self.stdout.write(f'  Duplicates:         {stats["duplicates"]}')
        self.stdout.write(f'  Assignments:        {stats["assigned"]}')
        if stats.get('errors'):
            self.stdout.write(self.style.WARNING(f'  Errors:             {stats["errors"]}'))
        self.stdout.write(self.style.SUCCESS('Done.'))
