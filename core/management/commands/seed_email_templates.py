"""Seed default email templates and call scripts."""
from django.core.management.base import BaseCommand
from core.models.sales import EmailTemplate, CallScript


class Command(BaseCommand):
    help = 'Seed default email templates and call scripts'

    def handle(self, *args, **options):
        # ── Email Templates ──
        templates = [
            {
                'name': 'Introduction',
                'category': 'introduction',
                'subject': 'Quick question about {{business_name}}',
                'body': (
                    'Hi {{contact_name}},\n\n'
                    'I noticed {{business_name}} serves the local area and wanted to reach out. '
                    'We help businesses like yours connect with customers who are actively looking '
                    'for your services right now — people posting on community forums, filing permits, '
                    'searching online.\n\n'
                    'Would it make sense to set up a quick 10-minute call this week? I can show you '
                    'exactly what kind of leads we\'re finding in your area.\n\n'
                    'Best,\n{{your_name}}\n{{your_company}}\n{{your_phone}}'
                ),
            },
            {
                'name': 'Follow-up',
                'category': 'followup',
                'subject': 'Following up — {{business_name}}',
                'body': (
                    'Hi {{contact_name}},\n\n'
                    'I reached out last week about helping {{business_name}} get more customers. '
                    'I wanted to follow up and see if you had a chance to think about it.\n\n'
                    'Since we last spoke, we\'ve detected several new leads in your area that match '
                    'your business. Happy to walk you through them on a quick call.\n\n'
                    'What does your schedule look like this week?\n\n'
                    'Best,\n{{your_name}}\n{{your_company}}\n{{your_phone}}'
                ),
            },
            {
                'name': 'Quote / Proposal',
                'category': 'quote',
                'subject': 'Your customized plan — {{business_name}}',
                'body': (
                    'Hi {{contact_name}},\n\n'
                    'Thanks for your time on the phone. As discussed, here\'s what we can do for '
                    '{{business_name}}:\n\n'
                    '- Real-time leads from 37+ data sources in your area\n'
                    '- AI-powered email campaigns to reach prospects\n'
                    '- CRM and pipeline management\n'
                    '- Human follow-up on your hottest leads\n\n'
                    'Plan: [PLAN NAME] — $[PRICE]/month\n'
                    'Setup fee: $299 (one-time)\n\n'
                    'Ready to get started? Just reply to this email or call me at {{your_phone}}.\n\n'
                    'Best,\n{{your_name}}\n{{your_company}}'
                ),
            },
            {
                'name': 'Appointment Confirmation',
                'category': 'appointment',
                'subject': 'Confirmed: Your call with {{your_company}}',
                'body': (
                    'Hi {{contact_name}},\n\n'
                    'This confirms our call scheduled for [DATE] at [TIME].\n\n'
                    'I\'ll walk you through how we find customers for businesses like '
                    '{{business_name}} using real-time data from public records, community forums, '
                    'and more.\n\n'
                    'Looking forward to it!\n\n'
                    '{{your_name}}\n{{your_company}}\n{{your_phone}}'
                ),
            },
            {
                'name': 'Thank You',
                'category': 'thankyou',
                'subject': 'Great talking with you, {{contact_name}}',
                'body': (
                    'Hi {{contact_name}},\n\n'
                    'Thanks for taking the time to speak with us today. We\'re excited about the '
                    'opportunity to help {{business_name}} grow.\n\n'
                    'As a next step, I\'ll get your account set up and you should see your first '
                    'leads within 24 hours.\n\n'
                    'If you have any questions in the meantime, don\'t hesitate to call me directly '
                    'at {{your_phone}}.\n\n'
                    'Best,\n{{your_name}}\n{{your_company}}'
                ),
            },
        ]

        created = 0
        for t in templates:
            _, was_created = EmailTemplate.objects.get_or_create(
                name=t['name'],
                defaults={**t, 'is_default': True},
            )
            if was_created:
                created += 1
        self.stdout.write(f'Email templates: {created} created')

        # ── Call Scripts ──
        scripts = [
            {
                'name': 'Violation Lead — Cold Call',
                'script_type': 'violation',
                'opening': (
                    'Hi, is this [CONTACT NAME]? This is [YOUR NAME] with SalesSignal AI. '
                    'I\'m calling because I noticed [BUSINESS/PROPERTY] at [ADDRESS] received '
                    'a building violation recently. I work with contractors and service providers '
                    'in the area who can help resolve these quickly. Do you have a minute?'
                ),
                'talking_points': [
                    'The violation was issued on [DATE] for [VIOLATION TYPE].',
                    'These typically need to be resolved within 30-60 days to avoid additional fines.',
                    'We work with licensed, insured contractors who specialize in exactly this type of work.',
                    'We can connect you with someone who can give you a free estimate this week.',
                ],
                'qualification_questions': [
                    'Are you the property owner or manager?',
                    'Have you already started working on resolving this?',
                    'Do you have a contractor you usually work with, or are you looking for one?',
                    'What\'s your timeline for getting this resolved?',
                ],
                'objection_handlers': {
                    'Not interested': 'I completely understand. Just so you know, the fine for this violation increases if not resolved by [DATE]. Would it help if I just sent you a free estimate via email?',
                    'Already handling it': 'Great to hear! If you ever need a second opinion or backup contractor, keep my number. We work with some of the best in the area.',
                    'How did you get my number?': 'Building violations are public record through the NYC Department of Buildings. We monitor these to connect property owners with qualified contractors before the deadline hits.',
                    'How much does it cost?': 'There\'s no cost to you for the introduction. The contractor gives you a free estimate. If you decide to work with them, you deal directly with them on pricing.',
                },
                'closing': 'Great — let me connect you with one of our contractors. What\'s the best email to send the details to? And what time works best for a call with them this week?',
            },
            {
                'name': 'No Website Prospect',
                'script_type': 'no_website',
                'opening': (
                    'Hi, is this [CONTACT NAME] at [BUSINESS NAME]? This is [YOUR NAME] with '
                    'SalesSignal AI. I was looking at businesses in [AREA] and noticed you don\'t '
                    'have a website yet. I help local businesses get found online and get more '
                    'customers. Got a quick minute?'
                ),
                'talking_points': [
                    'Right now, when someone in your area searches for your service, they\'re finding your competitors instead.',
                    'We monitor 37+ data sources to find people actively looking for services like yours.',
                    'We can set up lead monitoring for your area — you\'d get notified every time someone needs your service.',
                    'Our plans start at $149/month and typically pay for themselves with the first customer.',
                ],
                'qualification_questions': [
                    'How do you currently get most of your customers?',
                    'How many new customers would you like to add per month?',
                    'Do you serve the whole area or just specific neighborhoods?',
                    'What\'s your average job size in terms of revenue?',
                ],
                'objection_handlers': {
                    'I get enough work through referrals': 'That\'s great — referrals are the best. What we do is find the customers who AREN\'T asking friends yet. They\'re posting on Nextdoor, filing permits, searching Google. We catch those before your competitors do.',
                    'I can\'t afford it': 'I totally understand. Let me ask — what\'s your average job worth? $500? $1,000? Our Growth plan is $349/month. One new customer from our leads and you\'ve paid for 2-3 months.',
                    'I don\'t trust online stuff': 'I hear you. That\'s actually why we\'re different. We\'re real people, not a software company. I\'m on the phone with you right now. You always have a live human one button press away.',
                },
                'closing': 'Would it make sense to set up a quick demo? I can show you exactly what leads are available in your area right now. Takes about 10 minutes.',
            },
            {
                'name': 'General Outreach',
                'script_type': 'general',
                'opening': (
                    'Hi [CONTACT NAME], this is [YOUR NAME] with SalesSignal AI. We help local '
                    'service businesses find new customers using real-time data. I\'m calling because '
                    'I think we could help [BUSINESS NAME] get more leads in [AREA]. Do you have a minute?'
                ),
                'talking_points': [
                    'We monitor public records, community forums, review sites, and social media 24/7.',
                    'When someone in your area needs your service, we detect it and get you connected first.',
                    'Unlike Google Ads or Angi, these are people with real, immediate needs — not just browsing.',
                    'Humans backed by AI. You always have a real person you can call.',
                ],
                'qualification_questions': [
                    'What kind of services does your business provide?',
                    'How are you currently getting new customers?',
                    'How far do you typically travel for a job?',
                    'What would 5-10 extra customers per month mean for your business?',
                ],
                'objection_handlers': {
                    'Send me an email': 'Absolutely — what\'s your best email? I\'ll send over some examples of actual leads we\'ve found in your area. Fair warning, they\'re pretty impressive.',
                    'I\'m too busy': 'I totally respect that — that\'s actually a good sign. When you\'re ready to grow even more, we\'ll be here. Can I send you a quick email with our info?',
                    'What makes you different?': 'Great question. Most lead companies scrape Google Maps and blast emails. We monitor actual public records — building violations, permits, property sales — and community posts where people are asking for help. Real leads, not cold lists.',
                },
                'closing': 'Let me send you a personalized demo showing leads in your area. What\'s the best email? And if you like what you see, we can set up a 10-minute walkthrough.',
            },
            {
                'name': 'Follow-up Call',
                'script_type': 'followup',
                'opening': (
                    'Hi [CONTACT NAME], it\'s [YOUR NAME] from SalesSignal AI. We spoke [TIMEFRAME] '
                    'about helping [BUSINESS NAME] get more customers. Just following up to see if '
                    'you had any questions or if you\'re ready to get started.'
                ),
                'talking_points': [
                    'Since we last spoke, we\'ve detected [X] new leads in your area.',
                    'Your competitors are already getting these leads — I want to make sure you don\'t miss out.',
                    'We have a special this month: setup fee waived if you sign up by [DATE].',
                ],
                'qualification_questions': [
                    'Did you have a chance to look at the information I sent?',
                    'Any questions about how it works?',
                    'What would help you make a decision?',
                ],
                'objection_handlers': {
                    'Still thinking': 'Totally fair. Is there a specific concern I can address? Most of our customers say the first lead paid for the whole month.',
                    'Budget is tight': 'I understand. Our Outreach plan starts at $149/month — less than one job for most contractors. And there\'s a 100% money-back guarantee if you don\'t get leads in the first 30 days.',
                },
                'closing': 'Ready to get started? I can have your account live and scanning your area within the hour.',
            },
        ]

        script_created = 0
        for s in scripts:
            _, was_created = CallScript.objects.get_or_create(
                name=s['name'],
                defaults=s,
            )
            if was_created:
                script_created += 1
        self.stdout.write(f'Call scripts: {script_created} created')
        self.stdout.write(self.style.SUCCESS('Done.'))
