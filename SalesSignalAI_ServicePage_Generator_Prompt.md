# Claude Code Prompt: Trade/Location Landing Page Generator System

## Context

SalesSignalAI at /root/SalesSignalAI/ needs a system to generate and manage SEO-optimized landing pages for every trade + location combination. These pages rank on Google for searches like "emergency plumber Queens NY" and capture inbound calls and form submissions. Two modes: SalesSignal-owned pages (we get the calls and route them) and customer-branded pages (paying customer gets the calls directly).

This is a revenue-generating asset network — every page is a digital storefront that captures high-intent leads.

## Data Models

### TradeCategory Model
```python
class TradeCategory(models.Model):
    name = models.CharField(max_length=100)  # "Plumber"
    slug = models.SlugField(unique=True)  # "plumber"
    
    # For SEO content generation
    emergency_keywords = models.TextField(help_text="Comma-separated: emergency plumber, 24 hour plumber, plumber near me, burst pipe repair")
    service_keywords = models.TextField(help_text="Comma-separated: drain cleaning, water heater repair, toilet repair, pipe leak, sewer line")
    pain_points = models.TextField(help_text="Common customer problems: burst pipe flooding basement, no hot water, clogged drain backing up, toilet overflowing")
    
    # Grouping
    category_type = models.CharField(max_length=20, choices=[
        ('home_service', 'Home Service'),
        ('commercial_service', 'Commercial Service'),
        ('professional', 'Professional Service'),
        ('emergency', 'Emergency Service'),
    ])
    
    icon = models.CharField(max_length=50, blank=True, help_text="Icon class or emoji")
    
    class Meta:
        verbose_name_plural = "Trade Categories"
    
    def __str__(self):
        return self.name
```

### ServiceArea Model
```python
class ServiceArea(models.Model):
    name = models.CharField(max_length=100)  # "Queens"
    slug = models.SlugField()  # "queens"
    
    # Location hierarchy
    area_type = models.CharField(max_length=20, choices=[
        ('borough', 'Borough'),
        ('city', 'City'),
        ('county', 'County'),
        ('town', 'Town'),
        ('village', 'Village'),
        ('neighborhood', 'Neighborhood'),
        ('zip', 'ZIP Code'),
    ])
    parent_area = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, help_text="E.g. Queens -> New York City -> New York State")
    
    state = models.CharField(max_length=2, default='NY')
    state_full = models.CharField(max_length=50, default='New York')
    county = models.CharField(max_length=100, blank=True)
    
    # For geo-targeting
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    
    # For SEO
    population = models.IntegerField(null=True, blank=True)
    neighboring_areas = models.ManyToManyField('self', blank=True)
    
    class Meta:
        unique_together = ['slug', 'state']
    
    def __str__(self):
        return f"{self.name}, {self.state}"
```

### ServiceLandingPage Model
```python
class ServiceLandingPage(models.Model):
    # Core identity
    trade = models.ForeignKey(TradeCategory, on_delete=models.CASCADE)
    area = models.ForeignKey(ServiceArea, on_delete=models.CASCADE)
    
    # Page type
    page_type = models.CharField(max_length=20, choices=[
        ('salessignal', 'SalesSignal Owned'),
        ('customer', 'Customer Branded'),
    ])
    
    # URL
    slug = models.SlugField(max_length=200, unique=True, help_text="Auto-generated: emergency-plumber-queens-ny")
    custom_domain = models.CharField(max_length=200, blank=True, help_text="Optional: emergencyplumberqueens.com")
    
    # Customer branding (only for customer type)
    customer = models.ForeignKey('BusinessProfile', null=True, blank=True, on_delete=models.SET_NULL)
    branded_business_name = models.CharField(max_length=200, blank=True)
    branded_phone = models.CharField(max_length=20, blank=True)
    branded_email = models.EmailField(blank=True)
    branded_website = models.URLField(blank=True)
    branded_logo_url = models.URLField(blank=True)
    branded_tagline = models.CharField(max_length=200, blank=True)
    branded_years_in_business = models.IntegerField(null=True, blank=True)
    branded_license_number = models.CharField(max_length=100, blank=True)
    branded_google_reviews_url = models.URLField(blank=True)
    branded_star_rating = models.FloatField(null=True, blank=True)
    branded_review_count = models.IntegerField(null=True, blank=True)
    
    # Phone routing (for SalesSignal-owned pages)
    signalwire_phone = models.CharField(max_length=20, blank=True, help_text="SignalWire number assigned to this page")
    forward_to_phone = models.CharField(max_length=20, blank=True, help_text="Where calls get forwarded")
    
    # SEO Content — auto-generated but editable
    page_title = models.CharField(max_length=200, blank=True, help_text="Auto: 'Emergency Plumber in Queens, NY — 24/7 Service | Call Now'")
    meta_description = models.CharField(max_length=300, blank=True)
    h1_headline = models.CharField(max_length=200, blank=True, help_text="Auto: 'Emergency Plumber in Queens, NY'")
    hero_subheadline = models.CharField(max_length=300, blank=True, help_text="Auto: 'Fast, reliable plumbing service when you need it most. Licensed and insured.'")
    
    # Dynamic content from monitors
    show_live_stats = models.BooleanField(default=True, help_text="Show real-time stats from our monitors on the page")
    
    # Services list (specific to this trade+area combo)
    services_offered = models.TextField(blank=True, help_text="One per line: Emergency Pipe Repair\nDrain Cleaning\nWater Heater Installation")
    
    # Custom content sections
    about_section = models.TextField(blank=True, help_text="About paragraph — auto-generated if blank")
    faq_section = models.JSONField(default=list, blank=True, help_text="List of {question, answer} dicts")
    
    # Form submissions
    form_submissions = models.IntegerField(default=0)
    phone_calls = models.IntegerField(default=0)
    
    # Status
    status = models.CharField(max_length=20, choices=[
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
    ], default='draft')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['trade', 'area', 'page_type', 'customer']
    
    def __str__(self):
        if self.page_type == 'customer' and self.branded_business_name:
            return f"{self.branded_business_name} - {self.trade.name} in {self.area.name}"
        return f"{self.trade.name} in {self.area.name}"
```

### ServicePageSubmission Model
```python
class ServicePageSubmission(models.Model):
    landing_page = models.ForeignKey(ServiceLandingPage, on_delete=models.CASCADE)
    
    # Caller/submitter info
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    problem_description = models.TextField()
    urgency = models.CharField(max_length=20, choices=[
        ('emergency', 'Emergency — Need help now'),
        ('today', 'Today'),
        ('this_week', 'This week'),
        ('getting_quotes', 'Just getting quotes'),
    ])
    
    # Tracking
    submitted_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=20, choices=[
        ('form', 'Web Form'),
        ('phone', 'Phone Call'),
    ])
    
    # Routing
    routed_to = models.ForeignKey('BusinessProfile', null=True, blank=True, on_delete=models.SET_NULL)
    routed_at = models.DateTimeField(null=True, blank=True)
    
    # Outcome
    status = models.CharField(max_length=20, choices=[
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('converted', 'Converted'),
        ('lost', 'Lost'),
    ], default='new')
    
    def __str__(self):
        return f"{self.name} - {self.landing_page} - {self.submitted_at}"
```

## URL Structure

### SalesSignal-owned pages:
`salessignalai.com/find/{trade-slug}/{area-slug}/`
Examples:
- `/find/emergency-plumber/queens-ny/`
- `/find/commercial-cleaning/nassau-county-ny/`
- `/find/emergency-electrician/brooklyn-ny/`
- `/find/insurance-agent/manhattan-ny/`
- `/find/mortgage-broker/suffolk-county-ny/`
- `/find/dentist/long-beach-ny/`

### Customer-branded pages:
`salessignalai.com/pro/{customer-slug}/{area-slug}/`
Examples:
- `/pro/joes-plumbing/queens-ny/`
- `/pro/sunrise-cleaning/nassau-county-ny/`

### Future: custom domains pointing to these pages
When a customer buys `emergencyplumberqueens.com`, configure it to point to the same page template.

### Admin pages:
- `/admin/service-pages/` — list all landing pages
- `/admin/service-pages/create/` — create new landing page
- `/admin/service-pages/bulk-create/` — bulk create pages (select trades + areas, generates all combinations)
- `/admin/service-pages/{id}/edit/` — edit page
- `/admin/service-pages/{id}/stats/` — view submissions and call stats
- `/admin/service-pages/submissions/` — all form submissions across all pages

## Landing Page Template

### SalesSignal-owned version:

The page must be FAST (mobile-first, most searches are from phones), SEO-optimized, and conversion-focused.

**Top Bar:**
- Phone number in top right — large, tappable on mobile: "Call Now: (XXX) XXX-XXXX"
- Small SalesSignal AI logo in top left

**Hero Section:**
- H1: "{Trade} in {Area}" — e.g. "Emergency Plumber in Queens, NY"
- Subheadline: "Fast, reliable {trade} service when you need it most. Licensed and insured professionals available 24/7."
- Two CTAs side by side: 
  - "Call Now" button (tel: link to SignalWire number)
  - "Request Service" button (scrolls to form)
- Background: subtle, professional, light theme

**Trust Signals Bar:**
- "Licensed & Insured" | "Available 24/7" | "Free Estimates" | "Satisfaction Guaranteed"
- These are generic defaults — customer-branded pages show their actual credentials

**Live Stats Section (dynamic from monitors):**
- "This Week in {Area}:"
- "{X} {trade} service requests detected" (from Nextdoor/Reddit monitors)
- "{X} building permits filed for {trade} work" (from DOB permits)
- "{X} properties sold — new owners needing services" (from ACRIS)
- These numbers pull from actual Lead model counts filtered by trade + area
- Update daily via a simple template tag query
- This is the innovation — REAL DATA on a service landing page that no competitor has

**Services Section:**
- Grid of service cards specific to the trade
- E.g. for plumber: "Emergency Pipe Repair", "Drain Cleaning", "Water Heater Installation", "Sewer Line Repair", "Toilet Repair", "Gas Line Service"
- Auto-populated from TradeCategory.service_keywords, editable per page

**Request Service Form:**
- Name (required)
- Phone (required)
- Email
- Address / ZIP code
- "What do you need help with?" (text area)
- Urgency dropdown: "Emergency — need help now" | "Today" | "This week" | "Just getting quotes"
- Submit button: "Get Help Now"
- On submit: create ServicePageSubmission, send notification to admin (SMS + email), show confirmation

**FAQ Section:**
- Auto-generated FAQs specific to trade + area
- E.g. "How much does an emergency plumber cost in Queens?" / "What should I do if I have a burst pipe?" / "Are your plumbers licensed in New York State?"
- Structured data markup (FAQ schema) for Google rich results

**Footer:**
- "Powered by SalesSignal AI" (subtle)
- Links to privacy, terms
- Service area list (nearby areas linked to their own landing pages — internal linking for SEO)

### Customer-branded version:

Same layout but with branding changes:
- Customer's logo instead of SalesSignal logo
- Customer's phone number prominently displayed
- Customer's business name in the headline: "Joe's Plumbing — Emergency Plumber in Queens, NY"
- Customer's tagline
- "Serving {Area} for {X} years" if years_in_business is set
- Google review stars + count if provided
- License number displayed in trust bar
- "Powered by SalesSignal AI" in small footer text
- Form submissions go to the customer AND to SalesSignal admin

## Admin: Bulk Page Generator

The power feature. At `/admin/service-pages/bulk-create/`:

**Step 1: Select Trades**
Checklist of all trades. "Select All" button. Or select specific ones.

**Step 2: Select Areas**
Checklist of all service areas. Grouped by state → county/city → neighborhoods.
"Select All in New York" | "Select All in Nassau County" etc.

**Step 3: Select Page Type**
- SalesSignal Owned (default)
- Customer Branded → select customer from dropdown

**Step 4: Review & Generate**
Shows grid of what will be created:
"This will generate {X} landing pages:"
- Emergency Plumber in Queens, NY
- Emergency Plumber in Brooklyn, NY
- Emergency Plumber in Manhattan, NY
- Emergency Plumber in Nassau County, NY
- Commercial Cleaning in Queens, NY
- ... etc.

"Generate All" button.

Each page gets auto-generated:
- slug from trade + area
- page_title, meta_description, h1 from templates
- services_offered from TradeCategory.service_keywords
- FAQ auto-generated from trade + area
- about_section auto-generated
- Status: draft (review before activating)

## Seed Data: Trades

Pre-populate these TradeCategory records:

**Home Services:**
- Plumber (emergency plumber, 24 hour plumber, plumber near me, drain cleaning, water heater repair, pipe leak, sewer line, toilet repair, faucet replacement)
- Electrician (emergency electrician, 24 hour electrician, electrical repair, panel upgrade, outlet installation, wiring, lighting, ceiling fan installation)
- HVAC Technician (AC repair, heating repair, furnace installation, air conditioning, ductwork, HVAC maintenance, heat pump, boiler repair)
- General Contractor (home renovation, kitchen remodel, bathroom remodel, basement finishing, addition, deck building)
- Roofer (roof repair, roof replacement, emergency roof repair, shingle repair, flat roof, roof leak)
- Painter (house painter, interior painting, exterior painting, commercial painting, cabinet painting)
- Landscaper (landscaping, lawn care, tree trimming, yard cleanup, garden design, sprinkler installation)
- Pest Control (exterminator, pest removal, termite treatment, rodent control, bed bug treatment, ant removal)
- Locksmith (emergency locksmith, 24 hour locksmith, lock change, lockout service, key duplication, smart lock installation)
- Handyman (handyman services, home repair, furniture assembly, drywall repair, minor plumbing, minor electrical)
- Mover (moving company, local movers, long distance movers, packing services, storage)
- Cleaning Service (house cleaning, deep cleaning, move-in cleaning, move-out cleaning, regular maid service)
- Fencing (fence installation, fence repair, wood fence, vinyl fence, chain link fence)
- Flooring (floor installation, hardwood flooring, tile installation, carpet installation, laminate flooring)
- Garage Door (garage door repair, garage door installation, garage door opener, spring replacement)
- Tree Service (tree removal, tree trimming, stump grinding, emergency tree removal, tree pruning)
- Power Washing (pressure washing, deck cleaning, driveway cleaning, house washing, concrete cleaning)
- Paving (driveway paving, asphalt paving, concrete paving, patio installation, walkway)
- Mason (masonry, brick repair, stone work, retaining wall, chimney repair, stucco)
- Pool Service (pool cleaning, pool repair, pool installation, pool opening, pool closing)
- Gutter (gutter installation, gutter cleaning, gutter repair, gutter guards, downspout)
- Window (window installation, window replacement, window repair, glass repair, storm windows)
- Siding (siding installation, siding repair, vinyl siding, fiber cement siding)

**Commercial Services:**
- Commercial Cleaning (office cleaning, janitorial services, commercial cleaning, floor waxing, carpet cleaning, post-construction cleaning)
- Commercial HVAC (commercial AC, commercial heating, rooftop units, commercial refrigeration)
- Fire Protection (fire alarm, fire sprinkler, fire suppression, fire extinguisher, fire safety inspection)
- Security System (security camera, alarm system, access control, video surveillance, business security)

**Professional Services:**
- Insurance Agent (business insurance, home insurance, auto insurance, liability insurance, workers comp, commercial insurance)
- Mortgage Broker (mortgage, home loan, refinance, FHA loan, VA loan, first time homebuyer)
- Real Estate Agent (real estate, buy home, sell home, realtor, listing agent, buyer agent)
- Lawyer (attorney, legal services, business lawyer, real estate lawyer, personal injury, contract attorney)
- Accountant (CPA, tax preparation, bookkeeping, business accounting, tax planning, payroll)
- Dentist (dental, teeth cleaning, dental implants, emergency dentist, cosmetic dentistry, orthodontist)
- Chiropractor (chiropractic, back pain, spinal adjustment, neck pain, sports injury)
- Veterinarian (vet, animal hospital, pet care, emergency vet, dog vet, cat vet)
- Auto Mechanic (car repair, auto repair, oil change, brake repair, transmission, check engine light)
- Tow Truck (towing service, 24 hour towing, roadside assistance, flatbed tow, emergency towing)

## Seed Data: Service Areas

Pre-populate for New York launch:

**NYC Boroughs:**
- Manhattan (New York County, state=NY)
- Brooklyn (Kings County, state=NY)
- Queens (Queens County, state=NY)
- Bronx (Bronx County, state=NY)
- Staten Island (Richmond County, state=NY)

**Nassau County Towns/Cities:**
- Hempstead, North Hempstead, Oyster Bay
- Glen Cove, Long Beach
- Major villages: Freeport, Rockville Centre, Valley Stream, Lynbrook, Garden City, Massapequa Park, Farmingdale, Mineola, Great Neck, Port Washington
- Nassau County (as an aggregate area)

**Suffolk County Towns:**
- Babylon, Huntington, Islip, Smithtown, Brookhaven, Riverhead, Southampton, East Hampton
- Major villages: Patchogue, Port Jefferson, Northport, Bay Shore, Sayville, Lindenhurst, Amityville
- Suffolk County (as an aggregate area)

**Westchester:**
- Westchester County
- White Plains, Yonkers, New Rochelle, Mount Vernon, Scarsdale, Rye, Mamaroneck, Tarrytown

**Long Island aggregate:**
- Long Island (parent area covering Nassau + Suffolk)

**NYC neighborhoods (high-value, high-search-volume):**
- Astoria, Flushing, Jamaica, Bayside, Forest Hills (Queens)
- Williamsburg, Park Slope, Bushwick, Bay Ridge, Flatbush (Brooklyn)
- Harlem, Upper East Side, Upper West Side, East Village, Chelsea, Midtown (Manhattan)
- Fordham, Riverdale, Pelham Bay, Throggs Neck (Bronx)

Total areas: ~80-100

## Auto-Content Generation

When a new ServiceLandingPage is created, auto-generate content if fields are blank:

**page_title template:**
SalesSignal owned: "{Trade} in {Area}, {State} — 24/7 Service | Call Now"
Customer branded: "{Business Name} — {Trade} in {Area}, {State} | Call Now"

**meta_description template:**
SalesSignal owned: "Need a {trade} in {Area}? Fast, licensed, insured professionals available 24/7. Free estimates. Call now or request service online."
Customer branded: "{Business Name} provides expert {trade} services in {Area}, {State}. {Years} years experience. Licensed and insured. Call now for a free estimate."

**h1 template:**
"{Trade} in {Area}, {State_Full}"

**about_section template:**
SalesSignal owned: "Finding a reliable {trade} in {Area} shouldn't be stressful. Our network of licensed, insured {trade} professionals serves {Area} and surrounding communities. Whether it's an emergency at 2 AM or a scheduled service call, we connect you with the right professional fast. Every {trade} in our network is vetted, licensed, and reviewed by local customers."

Customer branded: "{Business Name} has been providing expert {trade} services to {Area} and surrounding areas for {years} years. Our team of licensed professionals is available when you need us most. We take pride in quality workmanship, fair pricing, and customer satisfaction. Call us today for a free estimate."

**FAQ auto-generation (3-5 per page):**
Generate based on trade + area:
- "How much does a {trade} cost in {Area}?"
- "How do I find a licensed {trade} in {Area}?"
- "What should I do in a {trade-related emergency}?"
- "Are {trade} services available on weekends in {Area}?"
- "How quickly can a {trade} arrive in {Area}?"

Fill in answers with area-specific information where possible.

## Live Stats Integration

Create a template tag or context processor that queries the Lead model:

```python
def get_area_trade_stats(trade_slug, area_name, days=7):
    """Returns live stats for a trade+area combination from our monitors"""
    # Count leads matching this trade's keywords in this area
    # from the last N days
    return {
        'service_requests': count_from_social_monitors,
        'permits_filed': count_from_permit_monitors,
        'violations_issued': count_from_violation_monitors,
        'properties_sold': count_from_property_sales,
        'new_businesses': count_from_business_filings,
    }
```

Display on the landing page:
"This Week in Queens: 23 plumbing service requests • 15 plumbing permits filed • 47 properties sold"

If counts are zero (no data for that specific combo), hide the section gracefully — don't show "0 requests."

## Phone Routing Logic

For SalesSignal-owned pages:
1. Each page gets a SignalWire phone number (or shares one with location-based routing)
2. When someone calls, SignalWire webhook identifies which page the number is associated with
3. If a paying customer covers that trade + area — route to them
4. If no customer — route to SIGNALWIRE_FALLBACK_PHONE (Andrew)
5. Log the call on the ServicePageSubmission model

For customer-branded pages:
1. Display the customer's phone number directly
2. Optionally route through SignalWire for call tracking (so we can prove ROI)

## Form Submission Handling

When someone submits the "Request Service" form:
1. Create ServicePageSubmission record
2. If SalesSignal-owned page with an assigned customer → route to customer (email + SMS notification)
3. If SalesSignal-owned page with NO assigned customer → notify admin only
4. If customer-branded page → notify both customer and admin
5. Also create a Lead in the main Lead model with source_type='service_page'
6. Send email confirmation to the submitter: "We received your request. A {trade} professional will contact you shortly."

## Sitemap Integration

Add all active ServiceLandingPages to the existing sitemap.xml dynamically. This could mean hundreds of URLs — Google wants to index all of them.

## Internal Linking

Each landing page footer should link to:
- Same trade in neighboring areas: "Also serving: Brooklyn, Manhattan, Nassau County..."
- Same area but different trades: "Other services in Queens: Electrician, HVAC, Cleaning..."
- This creates a strong internal link network for SEO

## Design

- Light theme matching the main SalesSignal site
- Exo 2 for headings
- Mobile-first — big tap targets, phone number always visible
- Fast loading — no heavy images, minimal JS
- The phone number should be sticky on mobile (always visible at top of screen as they scroll)
- Professional but not generic — the live stats section is the visual differentiator
- Customer-branded pages use the customer's brand colors if provided, otherwise default to SalesSignal palette

## Implementation Priority

1. Create models and migrations
2. Seed trade categories and service areas
3. Build the landing page template (SalesSignal-owned version)
4. Build the customer-branded variation
5. Build admin create/edit forms
6. Build bulk generator
7. Build form submission handling + notifications
8. Integrate live stats from monitors
9. Add to sitemap
10. Build internal linking
