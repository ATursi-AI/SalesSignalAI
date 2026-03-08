"""
Seed MonitoredLocalSite with initial NY/NJ/CT local news and community sites.
Usage: python manage.py seed_local_sites
"""
from django.core.management.base import BaseCommand

from core.models import MonitoredLocalSite


SITES = [
    {
        'name': 'NJ.com',
        'base_url': 'https://www.nj.com',
        'community_section_url': 'https://www.nj.com/forums/',
        'scrape_pattern': 'custom_html',
        'css_selectors': {
            'article_list': 'article, .river-item, .story-card',
            'article_link': 'a[href*="/community/"], a[href*="/forums/"]',
            'article_title': 'h2, h3, .headline',
            'article_date': 'time, .timestamp',
            'article_author': '.byline, .author',
            'article_body': '.entry-content, .story-body, article p',
            'comment_list': '.comment, [data-comment-id]',
            'comment_body': '.comment-body, .comment-text, p',
        },
    },
    {
        'name': 'Gothamist',
        'base_url': 'https://gothamist.com',
        'community_section_url': 'https://gothamist.com/neighborhood',
        'scrape_pattern': 'custom_html',
        'css_selectors': {
            'article_list': 'article, .story-card, .c-block',
            'article_link': 'a[href*="/news/"], a[href*="/neighborhood/"]',
            'article_title': 'h2, h3, .c-block__title',
            'article_date': 'time, .c-block__date',
            'article_author': '.c-block__byline, .byline',
            'article_body': '.c-article__body, article p, .entry-content',
            'comment_list': '.comment',
            'comment_body': '.comment-body, p',
        },
    },
    {
        'name': 'Long Island Press',
        'base_url': 'https://www.longislandpress.com',
        'community_section_url': 'https://www.longislandpress.com/category/community/',
        'scrape_pattern': 'wordpress_comments',
        'css_selectors': {
            'article_list': 'article, .post',
            'article_link': 'a[href*="/20"]',
            'article_title': 'h2 a, .entry-title a',
            'article_date': 'time, .entry-date',
            'article_author': '.author, .byline',
            'article_body': '.entry-content, .post-content',
            'comment_list': '.comment, #comments li',
            'comment_body': '.comment-content, .comment-body',
        },
    },
    {
        'name': 'CT Post',
        'base_url': 'https://www.ctpost.com',
        'community_section_url': 'https://www.ctpost.com/local/',
        'scrape_pattern': 'custom_html',
        'css_selectors': {
            'article_list': 'article, .story-card, .river-item',
            'article_link': 'a[href*="/local/"], a[href*="/news/"]',
            'article_title': 'h2, h3, .headline',
            'article_date': 'time, .timestamp',
            'article_author': '.byline, .author',
            'article_body': '.body-copy, article p, .entry-content',
            'comment_list': '.comment',
            'comment_body': '.comment-body, p',
        },
    },
    {
        'name': 'Stamford Advocate',
        'base_url': 'https://www.stamfordadvocate.com',
        'community_section_url': 'https://www.stamfordadvocate.com/local/',
        'scrape_pattern': 'custom_html',
        'css_selectors': {
            'article_list': 'article, .story-card, .river-item',
            'article_link': 'a[href*="/local/"], a[href*="/news/"]',
            'article_title': 'h2, h3, .headline',
            'article_date': 'time, .timestamp',
            'article_author': '.byline, .author',
            'article_body': '.body-copy, article p, .entry-content',
            'comment_list': '.comment',
            'comment_body': '.comment-body, p',
        },
    },
    {
        'name': 'Westchester Magazine',
        'base_url': 'https://westchestermagazine.com',
        'community_section_url': 'https://westchestermagazine.com/life-style/',
        'scrape_pattern': 'wordpress_comments',
        'css_selectors': {
            'article_list': 'article, .post',
            'article_link': 'a[href*="/life-style/"], a[href*="/home-real-estate/"]',
            'article_title': 'h2 a, .entry-title a',
            'article_date': 'time, .entry-date',
            'article_author': '.author, .byline',
            'article_body': '.entry-content, .post-content',
            'comment_list': '.comment, #comments li',
            'comment_body': '.comment-content, .comment-body',
        },
    },
]


class Command(BaseCommand):
    help = 'Seed MonitoredLocalSite with initial NY/NJ/CT local news sites'

    def handle(self, *args, **options):
        created_count = 0
        for site_data in SITES:
            _, created = MonitoredLocalSite.objects.get_or_create(
                name=site_data['name'],
                defaults={
                    'base_url': site_data['base_url'],
                    'community_section_url': site_data['community_section_url'],
                    'scrape_pattern': site_data['scrape_pattern'],
                    'css_selectors': site_data['css_selectors'],
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {site_data['name']}")
            else:
                self.stdout.write(f"  Exists:  {site_data['name']}")

        self.stdout.write(self.style.SUCCESS(f'Seeded {created_count} new sites ({len(SITES)} total)'))
