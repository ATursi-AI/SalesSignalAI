# Claude Code Prompt: Personalized Prospect Video Landing Page System

## Context

SalesSignalAI at /root/SalesSignalAI/ needs a system for creating personalized video landing pages for sales prospecting. We use HeyGen ($29/mo) to create short personalized videos about a prospect's business, then text them a link to watch it. Each prospect gets their own branded landing page on our site.

This system serves TWO purposes:
1. OUR prospecting — we create video pages for businesses we want to sell SalesSignalAI to
2. CUSTOMER prospecting — we create video pages on behalf of our paying customers to reach THEIR prospects (leads we found through our monitors)

## Data Model

Create a new model `ProspectVideo` in the core app:

```python
class ProspectVideo(models.Model):
    # Who this prospect page is for
    slug = models.SlugField(max_length=200, unique=True, help_text="URL slug: /demo/joes-plumbing/")
    
    # Prospect info
    prospect_business_name = models.CharField(max_length=200)
    prospect_owner_name = models.CharField(max_length=200, blank=True)
    prospect_phone = models.CharField(max_length=20, blank=True)
    prospect_email = models.EmailField(blank=True)
    prospect_trade = models.CharField(max_length=100, help_text="Plumbing, Cleaning, Electrical, etc.")
    prospect_city = models.CharField(max_length=100)
    prospect_state = models.CharField(max_length=2, default='NY')
    
    # Video
    video_url = models.URLField(help_text="YouTube unlisted URL or HeyGen direct link")
    video_thumbnail_url = models.URLField(blank=True, help_text="Optional thumbnail image URL")
    
    # Custom messaging on the page
    headline = models.CharField(max_length=300, blank=True, help_text="Custom headline. Default: '{business_name} — See What We Found'")
    custom_message = models.TextField(blank=True, help_text="Message shown below the video. E.g. 'We found 47 people in your area looking for a plumber this week.'")
    cta_text = models.CharField(max_length=100, default="Book a Call", help_text="Button text")
    cta_url = models.URLField(blank=True, help_text="Where the CTA button links. Leave blank for default contact form.")
    
    # If this is on behalf of a paying customer
    customer = models.ForeignKey('BusinessProfile', null=True, blank=True, on_delete=models.SET_NULL, help_text="If created on behalf of a paying customer, select them here")
    customer_business_name = models.CharField(max_length=200, blank=True, help_text="The paying customer's business name to feature in the page")
    customer_phone = models.CharField(max_length=20, blank=True, help_text="Customer's phone for the prospect to call")
    customer_website = models.URLField(blank=True)
    
    # Lead trigger (why we're reaching out)
    trigger_type = models.CharField(max_length=50, blank=True, choices=[
        ('health_violation', 'Health Inspection Violation'),
        ('building_violation', 'Building Violation'),
        ('new_business', 'New Business Filing'),
        ('property_sale', 'Property Sale'),
        ('permit_filed', 'Permit Filed'),
        ('social_request', 'Social Media Request'),
        ('no_website', 'No Website Detected'),
        ('bad_reviews', 'Low Google Reviews'),
        ('competitor_issue', 'Competitor Issue'),
        ('custom', 'Custom Outreach'),
    ], default='custom')
    trigger_detail = models.TextField(blank=True, help_text="Specific trigger info: 'Health inspection score 42, critical violation for pest issues'")
    
    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    sms_sent = models.BooleanField(default=False)
    sms_sent_at = models.DateTimeField(null=True, blank=True)
    email_sent = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    page_views = models.IntegerField(default=0)
    video_plays = models.IntegerField(default=0)
    cta_clicks = models.IntegerField(default=0)
    prospect_responded = models.BooleanField(default=False)
    response_date = models.DateTimeField(null=True, blank=True)
    response_notes = models.TextField(blank=True)
    
    # Status
    status = models.CharField(max_length=20, choices=[
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('responded', 'Responded'),
        ('converted', 'Converted'),
        ('closed', 'Closed'),
    ], default='draft')
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.prospect_business_name} ({self.slug})"
```

## URL Structure

- `/demo/<slug>/` — The public-facing prospect landing page
- `/admin/prospect-videos/` — Admin list of all prospect video pages (staff only)
- `/admin/prospect-videos/create/` — Admin form to create new prospect video page
- `/admin/prospect-videos/<id>/edit/` — Edit existing
- `/admin/prospect-videos/<id>/stats/` — View tracking stats for a specific page

Also register ProspectVideo in Django admin for quick access.

## Landing Page Template (`/demo/<slug>/`)

This is what the prospect sees when they click the link from our SMS. It must:

1. Look premium and professional — use the same light theme, Exo 2 font, and design language as the main site
2. Auto-track page views (increment page_views on each visit)
3. Be mobile-first — most people will open this from a text message on their phone

### Page Layout:

**Top:** Small SalesSignal AI logo in corner (subtle, not dominant)

**Hero Section:**
- Headline: Use `headline` field, or default to "{prospect_business_name}"
- Sub-headline: The custom_message field

**Video Section:**
- Embedded video player (YouTube embed or direct MP4)
- Large, centered, auto-play on desktop, tap-to-play on mobile
- Track video plays with JavaScript (increment video_plays via AJAX call)

**Below Video — The Pitch:**
If this is OUR prospecting (no customer assigned):
- "We found [X] people in {prospect_city} looking for a {prospect_trade} this week."
- "SalesSignal AI monitors 37 data sources 24/7 to find people who need your service — before they start Googling."
- CTA button: "{cta_text}" linking to cta_url or a built-in contact form

If this is on behalf of a CUSTOMER (customer field is set):
- Show the customer's business info: "{customer_business_name} — Serving {prospect_city}"
- customer_phone prominently displayed
- "Call now" or "Book an appointment" CTA
- Do NOT show SalesSignal branding prominently — this is white-labeled for the customer

**Trigger Context (optional, shown if trigger_detail is filled):**
- A subtle info card: "Why we reached out: {trigger_detail}"
- E.g. "Your building at 41-15 Kissena Blvd received a DOB violation on March 12. We can help."

**Footer:** Minimal. "Powered by SalesSignal AI" in small text (for our own prospecting pages). Nothing for customer white-label pages.

### Tracking JavaScript:
```javascript
// On page load — track view
fetch('/api/prospect-video-track/', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({slug: '{{slug}}', event: 'view'})
});

// On video play — track play
video.addEventListener('play', function() {
    fetch('/api/prospect-video-track/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({slug: '{{slug}}', event: 'play'})
    });
});

// On CTA click — track click
ctaButton.addEventListener('click', function() {
    fetch('/api/prospect-video-track/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({slug: '{{slug}}', event: 'cta_click'})
    });
});
```

Create a simple API endpoint `/api/prospect-video-track/` that accepts POST with slug and event type, increments the appropriate counter. No auth required (this is a public page).

## Admin Interface for Creating Video Pages

Build a clean form at `/admin/prospect-videos/create/` (staff-only) with:

**Section 1: Prospect Information**
- Business name (required)
- Owner name
- Phone
- Email
- Trade (dropdown: Plumbing, Electrical, HVAC, Commercial Cleaning, Roofing, General Contracting, Pest Control, Landscaping, Moving, Insurance, Legal, Other)
- City
- State

**Section 2: Video**
- Video URL (required) — paste YouTube or HeyGen link
- Thumbnail URL (optional)

**Section 3: Page Content**
- Headline (auto-generates from business name if blank)
- Custom message (text area)
- CTA button text (default: "Book a Call")
- CTA URL (optional)

**Section 4: On Behalf Of (optional)**
- Customer dropdown (pulls from BusinessProfile model)
- Customer business name (auto-fills from selection)
- Customer phone
- Customer website

**Section 5: Trigger**
- Trigger type (dropdown)
- Trigger detail (text area)

**Section 6: URL**
- Slug (auto-generates from business name, editable)
- Preview link showing the full URL

On save: status defaults to "draft". Admin clicks "Activate" to make it live. Show the live URL prominently after saving so it can be copied and texted.

## Admin List View (`/admin/prospect-videos/`)

Table showing all prospect video pages with columns:
- Business name
- Trade
- City
- Status (draft/active/responded/converted)
- Views / Plays / CTA Clicks (as small stat badges)
- SMS Sent (yes/no)
- Created date
- Actions: Edit, View Page, Copy URL

Filter by: status, trade, customer (our own vs on behalf of customer), date range

This should match the light theme design of the rest of the admin dashboard.

## SMS Integration (Stub for now)

Add a button on the admin edit page: "Send SMS" that:
- For now: just copies a pre-formatted text message to clipboard:
  "Hey {owner_name} — we made something for {business_name}: salessignalai.com/demo/{slug} - Andrew, SalesSignal AI"
- Marks sms_sent = True and records sms_sent_at
- In the future: this will actually send via SignalWire API (don't build the SignalWire integration yet, just the button and the clipboard copy)

Also add an "Send Email" button that:
- Copies a pre-formatted email to clipboard with the video link
- Marks email_sent = True and records email_sent_at

## HeyGen Video Script Template

Add a section in the admin create form that auto-generates a HeyGen script template based on the fields. Show it in a read-only text area that can be copied:

For OUR prospecting:
```
"{prospect_business_name} has been serving {prospect_city} for years.
Your customers clearly value quality — and so do we.
But right now, {X} people in your area are actively looking for a {prospect_trade}.
They're posting on community forums, they're searching online — and most {prospect_trade}s don't even know these leads exist.
What if you were the first call they got?
I'm Andrew with SalesSignal AI. Check the link — I'd love to show you what we found in your area."
```

For CUSTOMER prospecting:
```
"{prospect_business_name} — did you know {trigger_detail}?
{customer_business_name} specializes in exactly what you need right now.
With {X} years serving {prospect_city}, they've helped dozens of businesses just like yours.
Give them a call at {customer_phone} — or click below to book an appointment."
```

These are just templates — the admin can edit before pasting into HeyGen.

## Design Notes

- Match the existing light theme with Exo 2 headings
- The prospect landing page should feel premium — like someone spent time on it
- Mobile-first — big video player, big CTA button, easy to tap
- Fast loading — no heavy animations on the landing page
- The admin form should be clean and fast to fill out — the goal is 2 minutes per prospect
