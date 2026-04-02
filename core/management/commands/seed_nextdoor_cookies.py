"""
Launch a visible Playwright browser so the user can log into Nextdoor manually.
Once login is detected, cookies are saved for headless monitor runs.

Usage:
    python manage.py seed_nextdoor_cookies
"""
import asyncio

from django.core.management.base import BaseCommand

from core.utils.monitors.nextdoor_playwright import _save_cookies, _random_viewport


class Command(BaseCommand):
    help = 'Open a visible browser to log into Nextdoor and save cookies for headless runs'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Launching visible browser for Nextdoor login...'))
        self.stdout.write('')
        self.stdout.write('  1. A Chrome window will open to nextdoor.com/login')
        self.stdout.write('  2. Log in manually — solve any CAPTCHAs as needed')
        self.stdout.write('  3. Once you reach the feed, cookies will be saved automatically')
        self.stdout.write('')

        try:
            asyncio.run(self._run_browser())
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nCancelled by user.'))

    async def _run_browser(self):
        from playwright.async_api import async_playwright

        width, height = _random_viewport()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                viewport={'width': width, 'height': height},
                locale='en-US',
                timezone_id='America/New_York',
            )
            page = await context.new_page()

            await page.goto('https://nextdoor.com/login/')
            self.stdout.write(self.style.WARNING('Waiting for you to log in...'))

            # Poll until URL indicates successful login
            while True:
                await page.wait_for_timeout(2000)
                url = page.url
                if any(segment in url for segment in [
                    '/feed', '/news_feed', '/neighborhood',
                    '/for_sale', '/events',
                ]):
                    break

            # Give the page a moment to fully load and set all cookies
            await page.wait_for_timeout(3000)

            cookies = await context.cookies()
            _save_cookies(cookies)

            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(
                f'Saved {len(cookies)} cookies to browser_data/nextdoor_cookies.json'
            ))
            self.stdout.write(self.style.SUCCESS(
                'Future monitor_nextdoor runs will use these cookies in headless mode.'
            ))

            await browser.close()
