# LeadPulse AI — Complete Build Brief

## What This Is

LeadPulse is a real-time lead intelligence and outreach platform for local service businesses. It monitors the internet for people actively requesting services ("I need a plumber in Nassau County ASAP"), delivers instant alerts to the service provider, provides a full dashboard for managing leads and outreach campaigns, and runs automated cold email campaigns to businesses that might need the provider's services. It works for ANY local service category — plumbing, cleaning, HVAC, roofing, landscaping, electrical, and 50+ more.

**This is NOT just another cold email tool.** The core differentiator is real-time intent signal detection — capturing demand the moment it's expressed online, across multiple platforms simultaneously. The service provider who responds first wins the job. This product makes them first.

## Who This Is For

Any local service business in the United States. Initial beta: NY/NJ/CT area. The product is vertical-agnostic — a commercial cleaner, a plumber, a roofer, and an electrician all use the same platform with different service category configurations.

## Tech Stack

- **Framework:** Django 4+ / Python 3.10+
- **Database:** SQLite for local dev, Postgres for production
- **Frontend:** Django templates with a VISUALLY STUNNING custom design (see Design Direction below)
- **Task scheduling:** Django management commands + cron
- **Email sending:** Django email backend + SendGrid or Amazon SES (configurable)
- **Deployment (later):** Ubuntu VPS, Nginx, Gunicorn

## CRITICAL: Design Direction

**This product must look premium and modern — NOT like a generic Bootstrap dashboard.** The target customer is a local business owner who's used to apps like Square, Jobber, and Housecall Pro. The UI must feel that polished.

**Aesthetic direction: Dark luxury meets neon urgency.**

- **Color system:** Deep charcoal/near-black backgrounds (#0A0A0F, #12121A) with electric accent colors. Primary accent: electric blue (#3B82F6). Alert/hot lead accent: vivid coral/red (#FF4757). Success: emerald (#10B981). Warning: amber (#F59E0B). Platform pills get their own vibrant colors.
- **Typography:** Use Google Fonts. Display/headings: "Plus Jakarta Sans" (800 weight). Body: "Plus Jakarta Sans" (400/500). Monospace for stats: "JetBrains Mono". Large bold numbers for KPIs. Generous letter-spacing on section headers.
- **Cards and surfaces:** Glassmorphism-inspired cards with subtle backdrop-blur, very faint borders (rgba white at 5-8%), and soft box-shadows. Cards should feel like they float above the background. Rounded corners (12-16px).
- **Animations:** Fade-in-up on page load with staggered delays for card grids. Smooth transitions on hover states. Pulse animation on "HOT" lead badges. Count-up animation on KPI numbers. Subtle glow effects on primary action buttons.
- **Lead urgency visual system:** Leads less than 1 hour old get a pulsing red "HOT" badge with a subtle glow. Leads 1-4 hours old get an amber "WARM" badge. Leads 4-24 hours old get a blue "NEW" badge. Older leads get no badge. This urgency system is the most important visual element in the product — it drives immediate action.
- **Sidebar:** Fixed dark sidebar (same near-black as MedSignal but with the LeadPulse brand color accents). Clean icon + label navigation. Active state: bright accent background pill. User avatar and business name at bottom.
- **Platform pills:** Vibrant colored pills for each data source — Craigslist purple (#7B2FBE), Reddit orange (#FF4500), Patch teal (#0EA5E9), Google blue (#4285F4), Yelp red (#D32323), Houzz green (#4DBC5B), Alignable navy (#1B3A5C), Facebook blue (#1877F2), Nextdoor green (#8ED500).
- **Empty states:** Illustrated empty states with call-to-action, not just "no data" text.
- **Mobile responsive:** Sidebar collapses to bottom nav on mobile. Cards stack single column. Lead alerts must look great on phone screens since many users will access primarily via mobile browser.

**DO NOT use default Bootstrap styling.** Use Bootstrap's grid and utilities but completely override the visual design with custom CSS. Every element should feel intentionally designed.

---

## External Services

Store all keys in `.env` file loaded via `python-decouple`. Create `.env.example` with all placeholders.

```
# Reddit (for local subreddit monitoring)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=LeadPulseAI/1.0
REDDIT_USERNAME=
REDDIT_PASSWORD=

# Google Maps / Places API (for business scraping)
GOOGLE_MAPS_API_KEY=

# Anthropic (for AI email personalization and HTML parsing)
ANTHROPIC_API_KEY=

# Email sending
SENDGRID_API_KEY=
# OR
AWS_SES_ACCESS_KEY=
AWS_SES_SECRET_KEY=

# Email validation
ZEROBOUNCE_API_KEY=

# SMS alerts (Twilio)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# SerpAPI (for Google enrichment and document discovery)
SERPAPI_KEY=

# Alert email settings
ALERT_FROM_EMAIL=alerts@leadpulse.ai
```

---

## Database Models

### User & Business Models

**BusinessProfile** (extends Django User)
```python
class BusinessProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business_name = models.CharField(max_length=200)
    owner_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    website = models.URLField(blank=True)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    zip_code = models.CharField(max_length=10)
    service_category = models.ForeignKey('ServiceCategory', on_delete=models.PROTECT)
    service_subcategories = models.ManyToManyField('ServiceSubcategory', blank=True)
    service_radius_miles = models.IntegerField(default=15)
    service_zip_codes = models.JSONField(default=list, blank=True)  # explicit zip code list
    logo = models.ImageField(upload_to='logos/', blank=True)
    alert_via_email = models.BooleanField(default=True)
    alert_via_sms = models.BooleanField(default=False)
    alert_phone = models.CharField(max_length=20, blank=True)  # SMS number
    subscription_tier = models.CharField(max_length=20, choices=[
        ('starter', 'Starter'),
        ('growth', 'Growth'),
        ('pro', 'Pro'),
    ], default='starter')
    stripe_customer_id = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    onboarding_complete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### Service Category Models

**ServiceCategory** (master list — top level)
```python
class ServiceCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    icon = models.CharField(max_length=50, blank=True)  # Bootstrap icon class
    description = models.TextField(blank=True)
    # Default keywords for intent monitoring when customer picks this category
    default_keywords = models.JSONField(default=list)
    # Default Craigslist section to monitor
    craigslist_section = models.CharField(max_length=50, blank=True)
    # Default Google Maps search terms for lead list building
    google_maps_terms = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
```

**ServiceSubcategory**
```python
class ServiceSubcategory(models.Model):
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    slug = models.SlugField()
    additional_keywords = models.JSONField(default=list)
```

**Pre-populate with comprehensive categories (build a data migration):**

Home Services: Plumbing, Electrical, HVAC/Heating & Cooling, Handyman, Appliance Repair, Locksmith, Garage Door, Home Security

Cleaning: House Cleaning (Residential), Commercial Cleaning/Janitorial, Carpet Cleaning, Window Cleaning, Pressure Washing, Pool Cleaning, Gutter Cleaning

Outdoor/Property: Landscaping, Lawn Care/Mowing, Tree Service, Fencing, Concrete/Masonry, Paving/Asphalt, Snow Removal, Irrigation, Deck/Patio

Construction/Renovation: General Contractor, Roofing, Painting (Interior/Exterior), Flooring, Drywall, Tile, Kitchen Remodeling, Bathroom Remodeling, Basement Finishing, Siding, Windows/Doors

Specialty: Pest Control, Junk Removal/Hauling, Moving, Water Damage Restoration, Mold Remediation, Insulation, Solar Installation, EV Charger Installation, Chimney Sweep, Septic Service

Auto: Auto Repair, Auto Body, Towing, Auto Detailing, Windshield Repair, Mobile Mechanic

Commercial: Commercial Cleaning, Office Cleaning, Restaurant Cleaning, Construction Cleanup, Floor Waxing/Stripping

---

### Lead & Signal Models

**Lead** (the core model — every intent signal becomes a lead)
```python
class Lead(models.Model):
    # Source info
    platform = models.CharField(max_length=50, choices=[
        ('craigslist', 'Craigslist'),
        ('reddit', 'Reddit'),
        ('patch', 'Patch.com'),
        ('houzz', 'Houzz'),
        ('alignable', 'Alignable'),
        ('google_qna', 'Google Q&A'),
        ('yelp_review', 'Yelp Review'),
        ('facebook', 'Facebook'),
        ('nextdoor', 'Nextdoor'),
        ('twitter', 'Twitter/X'),
        ('manual', 'Manual Entry'),
    ])
    source_url = models.URLField(max_length=500)
    source_content = models.TextField()  # the original post text
    source_author = models.CharField(max_length=200, blank=True)
    source_posted_at = models.DateTimeField(null=True, blank=True)
    
    # Location extraction
    detected_location = models.CharField(max_length=200, blank=True)  # extracted city/town/zip
    detected_zip = models.CharField(max_length=10, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    
    # Categorization
    detected_service_type = models.ForeignKey('ServiceCategory', null=True, blank=True, on_delete=models.SET_NULL)
    matched_keywords = models.JSONField(default=list)
    
    # Urgency scoring
    urgency_score = models.IntegerField(default=50)  # 0-100
    urgency_level = models.CharField(max_length=10, choices=[
        ('hot', 'HOT'),       # < 1 hour old, high urgency language
        ('warm', 'WARM'),     # 1-4 hours old
        ('new', 'NEW'),       # 4-24 hours old
        ('stale', 'Stale'),   # > 24 hours old
    ], default='new')
    
    # AI-extracted insights
    ai_summary = models.TextField(blank=True)  # Claude-generated 1-line summary
    ai_suggested_response = models.TextField(blank=True)  # Claude-generated response template
    
    # Timestamps
    discovered_at = models.DateTimeField(auto_now_add=True)
    raw_data = models.JSONField(default=dict)
    
    # Dedup
    content_hash = models.CharField(max_length=64, unique=True)  # SHA256 of source_url or content
```

**LeadAssignment** (which business profiles receive which leads)
```python
class LeadAssignment(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE)
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE)
    
    # Status tracking
    status = models.CharField(max_length=20, choices=[
        ('new', 'New'),
        ('alerted', 'Alert Sent'),
        ('viewed', 'Viewed'),
        ('contacted', 'Contacted'),
        ('quoted', 'Quote Sent'),
        ('won', 'Won'),
        ('lost', 'Lost'),
        ('expired', 'Expired'),
    ], default='new')
    
    # Alert tracking
    alert_sent_at = models.DateTimeField(null=True, blank=True)
    alert_method = models.CharField(max_length=10, blank=True)  # email, sms, both
    viewed_at = models.DateTimeField(null=True, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    
    # Outcome
    revenue = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['lead', 'business']
```

### Outreach Campaign Models

**ProspectBusiness** (scraped from Google Maps and other directories)
```python
class ProspectBusiness(models.Model):
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)  # what kind of business (for targeting)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    zip_code = models.CharField(max_length=10, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    google_rating = models.FloatField(null=True, blank=True)
    google_review_count = models.IntegerField(null=True, blank=True)
    google_place_id = models.CharField(max_length=200, blank=True)
    owner_name = models.CharField(max_length=200, blank=True)
    owner_email = models.EmailField(blank=True)
    email_validated = models.BooleanField(default=False)
    email_validation_status = models.CharField(max_length=20, blank=True)
    source = models.CharField(max_length=50, blank=True)  # google_maps, alignable, bbb, etc.
    raw_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
```

**OutreachCampaign**
```python
class OutreachCampaign(models.Model):
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=[
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
    ], default='draft')
    
    # Targeting
    target_business_types = models.JSONField(default=list)  # e.g., ["property management", "office building"]
    target_zip_codes = models.JSONField(default=list)
    target_radius_miles = models.IntegerField(null=True, blank=True)
    
    # Email config
    email_subject_template = models.CharField(max_length=200, blank=True)
    email_body_template = models.TextField(blank=True)
    use_ai_personalization = models.BooleanField(default=True)
    
    # Sequence config
    max_emails_per_day = models.IntegerField(default=25)
    followup_delay_days = models.IntegerField(default=3)
    max_followups = models.IntegerField(default=2)
    
    # Stats
    total_prospects = models.IntegerField(default=0)
    emails_sent = models.IntegerField(default=0)
    emails_opened = models.IntegerField(default=0)
    emails_replied = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**OutreachEmail**
```python
class OutreachEmail(models.Model):
    campaign = models.ForeignKey(OutreachCampaign, on_delete=models.CASCADE)
    prospect = models.ForeignKey(ProspectBusiness, on_delete=models.CASCADE)
    
    sequence_number = models.IntegerField(default=1)  # 1=initial, 2=followup1, 3=followup2
    subject = models.CharField(max_length=200)
    body = models.TextField()
    
    status = models.CharField(max_length=20, choices=[
        ('queued', 'Queued'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('opened', 'Opened'),
        ('replied', 'Replied'),
        ('bounced', 'Bounced'),
        ('failed', 'Failed'),
    ], default='queued')
    
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
```

### Competitor Intelligence Models

**TrackedCompetitor**
```python
class TrackedCompetitor(models.Model):
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE)  # who's tracking this competitor
    name = models.CharField(max_length=200)
    google_place_id = models.CharField(max_length=200, blank=True)
    yelp_url = models.URLField(blank=True)
    website = models.URLField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    current_google_rating = models.FloatField(null=True, blank=True)
    current_review_count = models.IntegerField(null=True, blank=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

**CompetitorReview**
```python
class CompetitorReview(models.Model):
    competitor = models.ForeignKey(TrackedCompetitor, on_delete=models.CASCADE)
    platform = models.CharField(max_length=20)  # google, yelp
    reviewer_name = models.CharField(max_length=200, blank=True)
    rating = models.IntegerField()  # 1-5 stars
    review_text = models.TextField()
    review_date = models.DateField(null=True, blank=True)
    is_negative = models.BooleanField(default=False)  # 1-2 stars
    is_opportunity = models.BooleanField(default=False)  # AI flagged as potential lead signal
    ai_analysis = models.TextField(blank=True)  # Claude analysis of why this is an opportunity
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## Dashboard Pages

### 1. Onboarding Flow (first-time users only)
- Step 1: "What service do you provide?" — visual grid of service category cards with icons. Click to select. Shows subcategories after selection.
- Step 2: "Where do you work?" — address input + radius slider (5-50 miles). Or manual zip code entry for non-contiguous service areas. Map preview showing coverage area.
- Step 3: "How should we alert you?" — toggle for email alerts, SMS alerts. Phone number input for SMS. Test alert button.
- Step 4: "You're all set!" — summary of configuration, link to dashboard. System immediately starts monitoring for their area and service type.
- Clean, single-page wizard with progress indicator. Animated transitions between steps.

### 2. Dashboard Home
- **Lead KPI cards (top row):** Hot Leads (pulsing red number), Leads This Week, Response Rate, Leads Won This Month — each card with count-up animation, trend arrow (vs. last period), and colored icon
- **Live Lead Stream (main content):** Last 10 leads in reverse chronological order. Each lead card shows: platform pill, urgency badge (HOT/WARM/NEW), truncated content preview, detected location, time ago, and action buttons (View, Mark Contacted, Dismiss). HOT leads have a subtle glow/pulse effect.
- **Quick Stats sidebar:** Response time average, Best performing platform, Most active neighborhood, Conversion rate
- **Active Campaigns widget:** If Growth/Pro tier, show active outreach campaigns with sent/opened/replied counts
- **Competitor Watch widget:** Latest negative competitor review with "Opportunity" flag if applicable

### 3. Lead Feed (full view)
- Reverse chronological feed of ALL leads assigned to this business
- Filter bar: platform, urgency level, status (new/contacted/won/lost), date range, location/zip
- Each lead card (expanded version): platform pill with source icon, urgency badge, full post content (or truncated with expand), detected location with small map pin, keywords that matched (as pills), AI-generated summary (1 line), "View Original Post" button (opens source URL in new tab), status dropdown (New → Contacted → Quoted → Won/Lost), notes field, revenue field (for Won leads)
- Bulk actions: mark multiple as contacted, dismiss stale leads
- Lead count by urgency at top: "3 HOT | 7 WARM | 14 NEW"

### 4. Lead Detail
- Full post content from the source
- Platform and source URL with "Open Original" button
- Detected location with embedded map (small Google Maps embed or static map)
- AI-suggested response: a pre-written reply the business owner can copy and paste onto the original platform. Generated by Claude based on the lead content and the business's profile.
- Timeline: when discovered, when alerted, when viewed, when contacted, outcome
- Similar recent leads in the same area

### 5. Territory Map
- Full-width map (Google Maps or Leaflet/OpenStreetMap) showing the business's service area
- Colored pins for leads: red=HOT, amber=WARM, blue=NEW, green=Won, gray=Lost/Expired
- Heat map overlay option showing lead density by neighborhood
- Filter by date range and platform
- Click a pin to see lead summary popup
- Over time this shows the business owner where demand is concentrated

### 6. Outreach Campaigns (Growth/Pro tier)
- Campaign list with status, stats (sent/opened/replied), and action buttons
- Create campaign wizard:
  - Step 1: Name + target business type (e.g., "Property management companies")
  - Step 2: Geography (zip codes or radius)
  - Step 3: Review AI-generated email template or write custom. Preview with sample prospect data.
  - Step 4: Set sending pace (emails/day) and follow-up schedule
  - Step 5: Review prospect list (auto-populated from Google Maps scraping) and launch
- Campaign detail view: list of all prospects with email status (queued/sent/opened/replied), timeline, reply content
- Reply inbox: all campaign replies in one view for quick response

### 7. Competitor Intelligence
- List of tracked competitors with current rating, review count, trend (up/down arrows)
- Add competitor: search by business name or paste Google Maps URL
- Competitor detail: rating history chart (if tracked over time), recent reviews sorted by date, negative reviews highlighted, "Opportunity" flagged reviews (AI determined the reviewer needs an alternative provider)
- Alert settings: notify when competitor gets a 1-star or 2-star review

### 8. Analytics & Reports
- Weekly/monthly lead volume chart (line chart over time, broken down by platform)
- Lead conversion funnel: Detected → Alerted → Viewed → Contacted → Won
- Revenue tracking (for leads marked as Won with revenue entered)
- Platform performance comparison: which sources generate the most leads, best conversion rates
- Territory heat map: which areas generate the most demand
- Response time analysis: how fast the business responds to leads and correlation with win rate
- Competitor comparison: side-by-side rating trends

### 9. Settings
- Business profile: name, address, service area, service type, logo
- Alert preferences: email on/off, SMS on/off, phone number, alert frequency (instant, hourly digest, daily digest)
- Monitored keywords: auto-populated from service category but customizable. Add/remove keywords.
- Notification schedule: quiet hours (don't send SMS alerts between 10pm-7am)
- Billing: current plan, usage, upgrade/downgrade
- Team members (Pro tier): invite additional users

### 10. Public Landing Page
- Marketing homepage at the root URL (before login)
- Hero section with headline, subheadline, CTA button
- "How it works" section: 3 steps with animations
- Feature showcase with screenshots
- Pricing cards for the 3 tiers
- Testimonials section
- Footer with links
- This page should be VISUALLY STUNNING — it's the first thing prospects see. Dark theme matching the dashboard, dramatic animations, compelling copy.

---

## Intent Signal Monitors (Management Commands)

### Craigslist Monitor
- `python manage.py monitor_craigslist`
- Scrapes the "services wanted" and "gigs" sections for each metro area in the system
- Uses requests + BeautifulSoup (Craigslist has minimal anti-scraping)
- Organize by Craigslist metro: newyork.craigslist.org, newjersey.craigslist.org, etc.
- For each post: extract title, body, location, date, URL
- Match against service keywords for each active business profile
- Create Lead records and LeadAssignment records
- Extract location from post content using regex and known city/town names
- Run every 15-30 minutes via cron

### Reddit Local Monitor
- `python manage.py monitor_reddit_local`
- Uses PRAW (same as MedSignal's Reddit monitor but different subreddits)
- Target subreddits: r/longisland, r/nyc, r/newjersey, r/connecticut, r/westchester, r/hudsonvalley, r/stamford, r/hoboken, r/jerseyCity, r/nassaucounty, r/suffolkcounty, r/rocklandcounty, r/bergen, and more
- Scan posts AND comments for service request keywords
- Match against active business profiles by service type and geography
- Create Lead and LeadAssignment records

### Patch.com Monitor
- `python manage.py monitor_patch`
- Scrapes community boards for towns in the service areas of active businesses
- Patch URLs follow pattern: patch.com/[state]/[town]
- Uses requests + BeautifulSoup
- Look for posts in "Neighbors" and "Classifieds" sections
- Match against service keywords

### Houzz Forum Monitor
- `python manage.py monitor_houzz`
- Scrapes the Houzz community forum (Discourse-based = easy to scrape)
- Focus on "Find a Pro" and "Advice" categories
- Extract posts asking for contractor/service recommendations with location info

### Alignable Forum Monitor
- `python manage.py monitor_alignable`
- Scrapes Alignable local community forums
- Focus on posts requesting service recommendations
- B2B signals: property managers, facility managers, business owners asking for services

### Google Business Q&A Monitor
- `python manage.py monitor_google_qna`
- Uses Google Places API to check Q&A on competitor listings
- When someone asks a question on a competitor's listing, that's a potential lead
- Requires TrackedCompetitor records with google_place_id

### Yelp Competitor Review Monitor
- `python manage.py monitor_yelp_reviews`
- Scrapes recent reviews on tracked competitor Yelp pages
- Flags negative reviews (1-2 stars) as potential opportunities
- Uses Claude API to analyze review text and determine if the reviewer is looking for an alternative
- Creates Lead records for "opportunity" reviews

### Google Maps Business Scraper (for outreach campaigns)
- `python manage.py scrape_google_maps --category "property management" --location "Nassau County, NY"`
- Uses Google Places API or Outscraper to find businesses matching campaign targeting
- Extracts: business name, address, phone, website, rating, review count
- Crawls business website to find email addresses (using requests + Claude API for extraction)
- Creates ProspectBusiness records for use in outreach campaigns

---

## Outreach Email System

### Email Generation
- `python manage.py generate_campaign_emails --campaign-id 1`
- For each prospect in a campaign, generates a personalized email using Claude API
- Prompt includes: the business owner's service description, the prospect's business info (name, type, location, Google rating, website content summary), and the campaign template
- Generates unique subject line and body for each prospect
- Stores in OutreachEmail records with status=queued

### Email Sending
- `python manage.py send_campaign_emails`
- Picks up queued emails, respects daily sending limits per campaign
- Sends via SendGrid or SES
- Tracks delivery status via webhook callbacks
- Manages follow-up scheduling: if no reply after X days, queues next sequence email
- Domain warming: starts slow (5/day) and ramps up over 2 weeks

### Reply Detection
- `python manage.py check_campaign_replies`
- Checks inbox (via IMAP or SendGrid inbound parse) for replies to campaign emails
- Matches replies to OutreachEmail records
- Updates status to 'replied'
- Sends alert to business owner: "Someone replied to your outreach!"

---

## Alert System

### Alert Dispatcher
- `python manage.py dispatch_alerts`
- Runs every 5 minutes via cron
- Checks for LeadAssignment records with status='new' (not yet alerted)
- For each, sends alert via the business's preferred method:
  - **Email alert:** HTML email with lead summary, urgency badge, "View Lead" button linking to dashboard
  - **SMS alert:** Concise text message: "🔥 HOT LEAD: Someone in [location] needs [service type]. Posted [X min ago] on [platform]. View: [link]"
- Updates LeadAssignment status to 'alerted' and records alert_sent_at
- Respects quiet hours setting (no SMS between configured quiet hours)

---

## AI Features

### Lead Summary Generation
- When a new Lead is created, call Claude API to generate:
  - A 1-line summary of what the person needs (stored in ai_summary)
  - A suggested response the business owner can copy/paste (stored in ai_suggested_response)
  - The suggested response should be friendly, professional, reference the specific need, and mention the business by name

### Location Extraction
- Use Claude API to extract location information from free-text posts
- "Looking for a plumber, I'm in the Garden City area" → detected_location="Garden City, NY", detected_zip="11530"
- Fall back to regex matching against known city/town name list for the service area

### Competitor Review Analysis
- When a negative competitor review is captured, use Claude API to analyze:
  - Is this reviewer likely looking for an alternative provider?
  - What specifically went wrong? (no-show, quality, price, communication)
  - How could the business owner approach this person?
- Store analysis in CompetitorReview.ai_analysis

### Email Personalization
- For outreach campaigns, Claude generates unique emails per prospect
- Incorporates: prospect business type, location, Google rating, any website content scraped
- Varies each email to avoid spam filter patterns

---

## File Structure

```
leadpulse/
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── leadpulse/                      # Django project settings
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── local.py
│   ├── urls.py
│   └── wsgi.py
├── core/                           # Main app
│   ├── models/
│   │   ├── __init__.py
│   │   ├── business.py             # BusinessProfile, ServiceCategory, ServiceSubcategory
│   │   ├── leads.py                # Lead, LeadAssignment
│   │   ├── outreach.py             # ProspectBusiness, OutreachCampaign, OutreachEmail
│   │   ├── competitors.py          # TrackedCompetitor, CompetitorReview
│   │   └── alerts.py               # alert preferences if needed beyond BusinessProfile
│   ├── admin/
│   │   ├── __init__.py
│   │   └── admin.py
│   ├── views/
│   │   ├── __init__.py
│   │   ├── landing.py              # Public landing page
│   │   ├── onboarding.py           # Onboarding wizard
│   │   ├── dashboard.py            # Dashboard home
│   │   ├── leads.py                # Lead feed, lead detail
│   │   ├── territory.py            # Territory map
│   │   ├── campaigns.py            # Outreach campaigns
│   │   ├── competitors.py          # Competitor intelligence
│   │   ├── analytics.py            # Analytics & reports
│   │   └── settings_views.py       # Settings
│   ├── management/
│   │   └── commands/
│   │       ├── monitor_craigslist.py
│   │       ├── monitor_reddit_local.py
│   │       ├── monitor_patch.py
│   │       ├── monitor_houzz.py
│   │       ├── monitor_alignable.py
│   │       ├── monitor_google_qna.py
│   │       ├── monitor_yelp_reviews.py
│   │       ├── scrape_google_maps.py
│   │       ├── generate_campaign_emails.py
│   │       ├── send_campaign_emails.py
│   │       ├── check_campaign_replies.py
│   │       ├── dispatch_alerts.py
│   │       └── seed_categories.py      # populates ServiceCategory/Subcategory
│   ├── templates/
│   │   ├── base.html                   # Dashboard base with sidebar
│   │   ├── landing.html                # Public marketing page (separate from dashboard base)
│   │   ├── onboarding/
│   │   │   └── wizard.html
│   │   ├── dashboard/
│   │   │   └── home.html
│   │   ├── leads/
│   │   │   ├── feed.html
│   │   │   └── detail.html
│   │   ├── territory/
│   │   │   └── map.html
│   │   ├── campaigns/
│   │   │   ├── list.html
│   │   │   ├── create.html
│   │   │   └── detail.html
│   │   ├── competitors/
│   │   │   ├── list.html
│   │   │   └── detail.html
│   │   ├── analytics/
│   │   │   └── reports.html
│   │   ├── settings/
│   │   │   └── config.html
│   │   └── emails/
│   │       ├── lead_alert.html         # HTML email template for lead alerts
│   │       └── campaign_reply.html     # HTML email template for reply notifications
│   ├── static/
│   │   ├── css/
│   │   │   └── leadpulse.css           # THE MAIN STYLESHEET — must be stunning
│   │   ├── js/
│   │   │   └── leadpulse.js
│   │   └── images/
│   │       └── logo.svg
│   ├── templatetags/
│   │   └── lead_tags.py
│   ├── utils/
│   │   ├── monitors/
│   │   │   ├── craigslist.py
│   │   │   ├── reddit_local.py
│   │   │   ├── patch.py
│   │   │   ├── houzz.py
│   │   │   ├── alignable.py
│   │   │   ├── google_qna.py
│   │   │   └── yelp_reviews.py
│   │   ├── scrapers/
│   │   │   └── google_maps.py
│   │   ├── email_engine/
│   │   │   ├── generator.py            # Claude-powered email generation
│   │   │   ├── sender.py               # SendGrid/SES sending logic
│   │   │   └── reply_checker.py        # Reply detection
│   │   ├── ai/
│   │   │   ├── lead_analyzer.py        # Summary generation, location extraction
│   │   │   └── email_writer.py         # Campaign email personalization
│   │   ├── alerts/
│   │   │   ├── dispatcher.py           # Email + SMS alert sending
│   │   │   └── sms.py                  # Twilio SMS
│   │   └── location.py                 # Geo utilities, zip code lookups, distance calc
│   └── urls.py
```

---

## Build Sessions

### Session 1: Foundation + Data Models + Design System (4-5 hours)
Build:
1. Django project with settings structure
2. ALL database models with migrations
3. ServiceCategory seed data migration (full category list)
4. Django admin configuration for all models
5. The CSS design system (leadpulse.css) — this is critical, spend time making it stunning
6. Base template with dark sidebar, topbar, responsive layout
7. Public landing page (landing.html) — visually impressive marketing page
8. User registration + login + logout
9. Onboarding wizard (3-step flow)
10. Dashboard home with placeholder data
11. .env.example with all variables
12. requirements.txt

### Session 2: Lead Feed + Alert System (3-4 hours)
Build:
1. Lead feed page with filter bar, urgency badges, platform pills
2. Lead detail page with AI summary display area, suggested response, timeline, map
3. Lead status management (mark contacted, won, lost)
4. Alert dispatcher (email alerts — HTML template, send via Django email backend)
5. SMS alert integration (Twilio) — stub if no Twilio credentials yet
6. Lead urgency auto-update (cron command that moves HOT→WARM→NEW→Stale based on age)

### Session 3: Intent Signal Monitors — Craigslist + Reddit (3-4 hours)
Build:
1. Craigslist monitor (scraping services wanted sections)
2. Reddit local subreddit monitor (using PRAW, same pattern as MedSignal)
3. Location extraction utility (city/town/zip detection from free text)
4. Lead matching logic (match leads to business profiles by service type + geography)
5. Keyword matching based on ServiceCategory.default_keywords
6. Management commands for both monitors

### Session 4: More Monitors — Patch, Houzz, Alignable, Google Q&A, Yelp (4-5 hours)
Build:
1. Patch.com community board monitor
2. Houzz forum monitor
3. Alignable forum monitor
4. Google Business Q&A monitor
5. Yelp competitor review monitor
6. Management commands for all

### Session 5: Competitor Intelligence + Territory Map (3-4 hours)
Build:
1. Competitor tracking — add competitor, auto-populate from Google
2. Competitor detail page with review list, rating trend
3. Competitor review AI analysis (Claude integration)
4. Opportunity flagging for negative reviews
5. Territory map page with Leaflet.js or Google Maps
6. Lead pins with urgency colors
7. Heat map overlay option

### Session 6: Outreach Campaign Engine (4-5 hours)
Build:
1. Google Maps business scraper
2. Website crawler + email extraction (Claude API)
3. Email validation integration
4. Campaign creation wizard
5. AI email generation (Claude API)
6. Email sending via SendGrid/SES
7. Follow-up sequence logic
8. Reply detection
9. Campaign detail view with prospect list and statuses

### Session 7: Analytics + Polish (3-4 hours)
Build:
1. Analytics page with charts (Chart.js)
2. Weekly summary data aggregation
3. Lead conversion funnel visualization
4. Platform performance comparison
5. Response time analysis
6. Weekly summary email command
7. Final UI polish, animations, mobile responsiveness testing

---

## First Message for Claude Code — Session 1

```
Read the file LEADPULSE_BRIEF.md in this folder. Build Session 1 as described.

CRITICAL DESIGN REQUIREMENT: This product must be visually stunning. Do NOT use default Bootstrap styling. The aesthetic direction is "dark luxury meets neon urgency" — deep charcoal/near-black backgrounds (#0A0A0F, #12121A), glassmorphism cards with backdrop-blur and faint borders, electric blue primary accent (#3B82F6), vivid coral for hot alerts (#FF4757), emerald for success (#10B981). Typography: "Plus Jakarta Sans" from Google Fonts for all text, "JetBrains Mono" for stats/numbers. Animations: fade-in-up on page load with staggered delays, count-up on KPI numbers, pulse glow on HOT badges. Every element should feel intentionally designed and premium — this is competing against apps like Square and Jobber.

Build the Django project, all models, seed the 50+ service categories, configure admin, create the full CSS design system, build the base template with dark sidebar, build the public landing page (make it impressive), user auth, onboarding wizard, and dashboard home. The app should run with `python manage.py runserver` showing a complete working UI by end of session. Start building now.
```