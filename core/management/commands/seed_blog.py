"""Seed the first blog post."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import BlogPost


FIRST_POST_CONTENT = """
<p>Every AI company talks about automation. How their technology replaces humans. How their algorithms do the work so you don't have to. How you can "set it and forget it."</p>

<p>We think that's backwards.</p>

<h2>We're Salespeople First, Tech Company Second</h2>

<p>SalesSignal AI wasn't built in a Silicon Valley incubator by engineers who read about sales in a textbook. It was built by people who have been on the phones for over 20 years. People who have knocked on doors, cold-called strangers, handled objections, closed deals, and built relationships that last decades.</p>

<p>We know what it takes to win a customer because we've done it thousands of times. And we know something that pure-tech companies will never understand: <strong>technology doesn't close deals. People do.</strong></p>

<h2>What Our AI Actually Does</h2>

<p>Our AI is incredible at the things AI should be incredible at:</p>

<ul>
<li>Monitoring 37+ data sources 24/7 — building violations, health inspections, property sales, business filings, community forums, review sites</li>
<li>Spotting the signals that someone needs your service right now</li>
<li>Writing the first draft of a personalized outreach email</li>
<li>Scanning thousands of public records in minutes instead of weeks</li>
<li>Tracking your competitors' reviews and ratings</li>
</ul>

<p>That's what machines are good at — processing massive amounts of data, finding patterns, never sleeping. Let the AI do that all day long.</p>

<p>But here's where we're different from everyone else.</p>

<h2>You're Never Alone With a Chatbot</h2>

<p>When you become a SalesSignal customer, you are never stuck in an automated phone tree pressing 1, 2, 3, hoping to eventually reach a human who can actually help you. You are never talking to a chatbot that gives you canned responses. You are never submitting a support ticket into a black hole.</p>

<p><strong>You are always ONE button press away from a live person who knows your business, knows your market, and knows how to help.</strong></p>

<p>That's not a feature. That's a promise.</p>

<h2>AI Handles the Boring Stuff. We Handle the Important Stuff.</h2>

<p>We believe AI should handle the work that humans shouldn't waste time on — scanning thousands of records, monitoring social media around the clock, writing the first draft of an email at 3 AM. That frees up the humans to do what humans do best: listen, understand, persuade, and build trust.</p>

<p>When a building owner in Queens gets a $10,000 violation and needs a contractor yesterday, software doesn't pick up the phone. We do.</p>

<p>When a new homeowner in Nassau County closes on their first house and needs a plumber, an electrician, and a cleaning service, an algorithm doesn't call them and say "congratulations on the new home, how can we help?" We do.</p>

<p>When a restaurant fails a health inspection and needs immediate remediation, a dashboard doesn't empathize with their stress and connect them with the right person. We do.</p>

<h2>Our Competitors Sell Software. We Sell Results.</h2>

<p>There are dozens of lead generation platforms out there. Most of them are the same thing: scrape some data, throw it in a dashboard, charge you $300 a month, and wish you luck. If the leads don't convert, that's your problem.</p>

<p>That's not how we work.</p>

<p>We find the lead. We verify the contact information. We write the outreach. We follow up. And when someone is ready to talk, we get them on the phone with you. Real conversations with real prospects who actually need what you're selling.</p>

<p>If a lead doesn't have a phone number, we use AI to find it. If an email bounces, we find another way in. If someone doesn't respond to the first email, we follow up intelligently — not with spam, but with the kind of persistence that closes deals.</p>

<h2>The Person Behind the Platform</h2>

<p>I'm Andrew Tursi, and I help businesses get customers.</p>

<p>That's not a tagline. That's what I do every single day. It's what my team does. And it's backed by technology that makes us better, faster, and more effective than any traditional salesperson or any pure-AI platform could be on their own.</p>

<p><strong>Humans backed by AI. AI backed by humans.</strong></p>

<p>That's SalesSignal.</p>

<h2>Try It</h2>

<p>If you're tired of platforms that hide behind automation and make you feel like a number, give us a call. Not an email. Not a chatbot. A phone call. Talk to a real person who will listen to what you need and tell you honestly whether we can help.</p>

<p>That's the SalesSignal difference. And it starts with a conversation.</p>
"""


class Command(BaseCommand):
    help = 'Create the first blog post'

    def handle(self, *args, **options):
        if BlogPost.objects.filter(slug='why-humans-behind-salessignal-ai-make-us-different').exists():
            self.stdout.write('First blog post already exists.')
            return

        post = BlogPost.objects.create(
            title='Why the Humans Behind SalesSignal AI Are What Make Us Different',
            slug='why-humans-behind-salessignal-ai-make-us-different',
            content=FIRST_POST_CONTENT.strip(),
            excerpt=(
                'Every AI company talks about automation. We talk about people. '
                'SalesSignal AI is built by real salespeople who pick up the phone, '
                'close deals, and never leave you stuck in a voice tree.'
            ),
            author='Andrew Tursi',
            meta_title='Why Humans Behind the AI Make SalesSignal Different',
            meta_description=(
                'SalesSignal AI is built by real salespeople. '
                'No chatbots, no voice trees — just a live human one button press away.'
            ),
            tags='sales, human touch, AI, lead generation, customer service, SalesSignal difference',
            is_published=True,
            published_at=timezone.now(),
        )

        self.stdout.write(self.style.SUCCESS(
            f'Created: "{post.title}" at /blog/{post.slug}/'
        ))
