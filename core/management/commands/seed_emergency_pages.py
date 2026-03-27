"""
Create ServiceLandingPage records for Emergency Plumber + Electrician
across all NYC boroughs + Long Island counties.

Usage: python manage.py seed_emergency_pages
"""
from django.core.management.base import BaseCommand
from core.models.service_pages import TradeCategory, ServiceArea, ServiceLandingPage

DEFAULT_PHONE = '(959) 247-2537'

# Map trade slugs to area slugs we want pages for
PAGES = {
    'plumber': [
        'queens-ny', 'brooklyn-ny', 'manhattan-ny', 'bronx-ny',
        'staten-island-ny', 'nassau-county-ny', 'suffolk-county-ny',
    ],
    'electrician': [
        'queens-ny', 'brooklyn-ny', 'manhattan-ny', 'bronx-ny',
        'staten-island-ny', 'nassau-county-ny', 'suffolk-county-ny',
    ],
}


class Command(BaseCommand):
    help = 'Create emergency plumber + electrician service landing pages'

    def handle(self, *args, **options):
        created = 0
        skipped = 0

        for trade_slug, area_slugs in PAGES.items():
            trade = TradeCategory.objects.filter(slug=trade_slug).first()
            if not trade:
                self.stdout.write(self.style.ERROR(f"Trade not found: {trade_slug}"))
                continue

            for area_slug in area_slugs:
                area = ServiceArea.objects.filter(slug=area_slug, state='NY').first()
                if not area:
                    self.stdout.write(self.style.ERROR(f"  Area not found: {area_slug}"))
                    continue

                # Page slug for the URL: /find/plumber/queens-ny/
                page_slug = f"{trade_slug}-{area_slug}"

                if ServiceLandingPage.objects.filter(slug=page_slug).exists():
                    self.stdout.write(f"  SKIP  {page_slug} (exists)")
                    skipped += 1
                    continue

                page = ServiceLandingPage(
                    trade=trade,
                    area=area,
                    page_type='salessignal',
                    slug=page_slug,
                    signalwire_phone=DEFAULT_PHONE,
                    status='active',
                )
                page.save()  # triggers _auto_generate_content
                created += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  OK    /find/{trade_slug}/{area_slug}/"
                ))

        self.stdout.write(f"\nDone: {created} created, {skipped} skipped")
