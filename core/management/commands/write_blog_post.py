"""Auto-generate a blog post using Gemini AI."""
import json
import re

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import BlogPost


class Command(BaseCommand):
    help = 'Generate a blog post using Gemini AI'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print without saving')

    def handle(self, *args, **options):
        api_key = getattr(settings, 'GEMINI_API_KEY', '')
        if not api_key:
            self.stderr.write('GEMINI_API_KEY not configured')
            return

        system_prompt = (
            'You are a blog writer for SalesSignal AI (salessignalai.com), a lead generation '
            'platform for local service businesses. Our brand voice is: confident, direct, '
            'knowledgeable, human. We believe the HUMANS behind the AI are what make us special. '
            'We are real salespeople — some of the best phone salespeople in America — who use AI '
            'as a tool, not a replacement. Our customers always have a live human a quick button '
            'press away. Never leave them in an AI voice tree. Write in first person plural '
            '(we/us/our). Be conversational but professional. No fluff, no corporate jargon. '
            'Sound like someone who has been in sales for 20 years and knows what they are talking about.'
        )

        prompt = (
            'Write a blog post for SalesSignal AI. Pick a topic from this list that would be '
            'valuable for local service business owners: [sales tips, lead generation, human + AI '
            'approach, industry spotlight, local business growth, cold calling, customer relationships, '
            'the SalesSignal difference].\n\n'
            'Return ONLY valid JSON with no markdown:\n'
            '{\n'
            '  "title": "...",\n'
            '  "content": "... (HTML formatted, use <h2>, <p>, <strong>, <ul><li> tags, 800-1200 words) ...",\n'
            '  "excerpt": "... (2-3 sentence preview, max 500 chars) ...",\n'
            '  "meta_title": "... (max 70 chars, include SalesSignal AI) ...",\n'
            '  "meta_description": "... (max 160 chars) ...",\n'
            '  "tags": "sales,lead generation,..."\n'
            '}'
        )

        model_name = 'gemini-3-flash-preview'
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent'

        self.stdout.write('Generating blog post...')

        try:
            resp = requests.post(
                url,
                params={'key': api_key},
                headers={'Content-Type': 'application/json'},
                json={
                    'systemInstruction': {'parts': [{'text': system_prompt}]},
                    'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {
                        'maxOutputTokens': 8192,
                        'temperature': 0.8,
                    },
                },
                timeout=60,
            )

            if resp.status_code != 200:
                self.stderr.write(f'Gemini API error {resp.status_code}: {resp.text[:300]}')
                return

            data = resp.json()
            parts = data['candidates'][0]['content']['parts']
            text = ''
            for part in parts:
                if 'text' in part and 'thought' not in part:
                    text += part['text']

            # Strip markdown code fences
            text = re.sub(r'^```(?:json)?\s*', '', text.strip())
            text = re.sub(r'\s*```$', '', text.strip())

            # Extract JSON
            match = re.search(r'\{[\s\S]*\}', text)
            if not match:
                self.stderr.write(f'No JSON found in response: {text[:300]}')
                return

            post_data = json.loads(match.group())

        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
            self.stderr.write(f'Error: {e}')
            return

        title = post_data.get('title', 'Untitled')
        self.stdout.write(f'\nTitle: {title}')
        self.stdout.write(f'Tags: {post_data.get("tags", "")}')
        self.stdout.write(f'Excerpt: {post_data.get("excerpt", "")[:200]}...')
        self.stdout.write(f'Meta title: {post_data.get("meta_title", "")}')
        self.stdout.write(f'Content length: ~{len(post_data.get("content", ""))} chars')

        if options['dry_run']:
            self.stdout.write('\n--- DRY RUN — not saved ---')
            self.stdout.write(post_data.get('content', '')[:500] + '...')
            return

        post = BlogPost.objects.create(
            title=title,
            content=post_data.get('content', ''),
            excerpt=post_data.get('excerpt', ''),
            meta_title=post_data.get('meta_title', '')[:70],
            meta_description=post_data.get('meta_description', '')[:160],
            tags=post_data.get('tags', ''),
            author='SalesSignal AI Team',
            is_published=True,
            published_at=timezone.now(),
        )

        self.stdout.write(self.style.SUCCESS(f'\nBlog post created: /blog/{post.slug}/ (ID: {post.pk})'))
