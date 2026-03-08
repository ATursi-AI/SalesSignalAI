# SalesSignal AI — Phase 3: Apify Integration, Public Records, and Expanded Social Coverage

## Context

Read this alongside SALESSIGNAL_BRIEF.md and SALESSIGNAL_PHASE2.md. Sessions 1-12 are complete. The site is live at salessignalai.com. This document covers Phase 3: integrating Apify for reliable social media scraping, adding public records data sources for high-intent lead signals, and expanding social platform coverage nationwide.

**CRITICAL: This product is NATIONWIDE. Nothing is geographically limited. All monitors dynamically match leads to BusinessProfiles based on each customer's configured service area. When scraping platforms, monitors must be configurable for any US geography.**

---

## PART 1: APIFY INTEGRATION

### Why Apify

Apify is a cloud-based scraping platform with 4,000+ pre-built scrapers (called "Actors"). They handle proxy rotation, browser fingerprinting, and anti-bot bypasses automatically. Their Facebook Groups Scraper is rated 4.8 stars by thousands of users. Cost: $49/month Starter plan for initial usage, $199/month Scale plan as customer count grows.

### What Apify Replaces/Adds

| Platform | Current Approach | Apify Approach | Benefit |
|----------|-----------------|----------------|---------|
| Facebook Groups | Fragile Playwright automation | Apify Facebook Groups Scraper | Reliable, cloud-hosted, no account bans |
| Nextdoor | Not built (deferred) | Apify Nextdoor Scraper | Unlocks richest local signal source |
| Twitter/X | Requires $100/month API | Apify Tweet Scraper V2 | No API key needed, saves $100/month |
| Instagram | Instaloader (for MedSignal) | Apify Instagram Scraper | More reliable, 191K+ users |
| Google Maps | Google Places API | Apify Google Maps Scraper | Bypasses daily quota limits |
| Google Maps Reviews | Google Places API | Apify Google Maps Reviews Scraper | $0.35/1K reviews, more data |
| TikTok | Not built | Apify TikTok Scraper | New source — home disaster content |
| Quora | Not built | Apify Quora Scraper | New source — service recommendation questions |
| Facebook Marketplace | Not built | Apify Facebook Marketplace Scraper | New source — service requests on Marketplace |
| Threads | Not built | Apify Threads Scraper | New source — Meta's growing text platform |
| Trustpilot | Not built | Apify Trustpilot Scraper | Competitor review monitoring |

### Technical Implementation

**Install Apify Python SDK:**
```bash
pip install apify-client
```

**Add to .env:**
```
APIFY_API_TOKEN=your-apify-token-here
```

**Build a unified Apify integration layer:**

Create `core/utils/apify_client.py` — a wrapper class that handles all Apify interactions:

```python
class ApifyIntegration:
    """
    Unified Apify client for all platform scrapers.
    Handles running Actors, retrieving results, and converting to Lead records.
    """
    
    def __init__(self):
        self.client = ApifyClient(settings.APIFY_API_TOKEN)
    
    def run_actor(self, actor_id, run_input, timeout_secs=300):
        """Run any Apify Actor and return results"""
        run = self.client.actor(actor_id).call(run_input=run_input, timeout_secs=timeout_secs)
        return self.client.dataset(run["defaultDatasetId"]).list_items().items
    
    def scrape_facebook_groups(self, group_urls, max_posts=50):
        """Scrape Facebook Group posts"""
        # Actor ID for Facebook Groups Scraper
        pass
    
    def scrape_nextdoor(self, locations, keywords, max_results=50):
        """Scrape Nextdoor posts by location and keywords"""
        pass
    
    def scrape_twitter(self, keywords, locations=None, max_tweets=100):
        """Search Twitter/X for keyword matches"""
        pass
    
    def scrape_google_maps(self, search_terms, locations, max_results=100):
        """Scrape Google Maps business listings"""
        pass
    
    def scrape_google_reviews(self, place_ids, max_reviews=50):
        """Scrape Google Maps reviews for competitor monitoring"""
        pass
    
    def scrape_tiktok(self, keywords, max_videos=50):
        """Search TikTok for relevant content"""
        pass
    
    def scrape_quora(self, keywords, max_questions=50):
        """Search Quora for service recommendation questions"""
        pass
    
    def scrape_threads(self, keywords, max_posts=50):
        """Search Threads for local service discussions"""
        pass
    
    def scrape_facebook_marketplace(self, locations, categories, max_listings=50):
        """Scrape Facebook Marketplace service requests"""
        pass
    
    def scrape_trustpilot(self, company_urls, max_reviews=50):
        """Scrape Trustpilot competitor reviews"""
        pass
    
    def scrape_instagram(self, hashtags=None, profiles=None, max_posts=50):
        """Scrape Instagram posts by hashtag or profile"""
        pass
```

**Each Apify-powered monitor follows the same pattern:**
1. Build search parameters based on active BusinessProfile service areas and keywords
2. Call the appropriate ApifyIntegration method
3. Parse results into Lead records
4. Match leads to BusinessProfiles by service type + geography
5. Trigger alerts for matched leads

### New Monitors Using Apify

**Apify Facebook Groups Monitor** (`core/utils/monitors/apify_facebook.py`)
- Replaces the Playwright-based facebook_groups.py monitor
- Uses Apify's Facebook Groups Scraper Actor
- Configure which groups to monitor via MonitoredFacebookGroup model (already exists)
- Pull posts with author info, engagement metrics, comments
- Match against service keywords and customer geographies
- Management command: `python manage.py monitor_facebook_apify`

**Apify Nextdoor Monitor** (`core/utils/monitors/apify_nextdoor.py`)
- NEW — previously deferred because too hard to scrape
- Uses Apify's Nextdoor scraper
- Search by location keywords (city names, zip codes from active BusinessProfiles)
- Filter for service request posts
- This is the highest-value addition — Nextdoor is THE platform where people ask neighbors for service recommendations
- Management command: `python manage.py monitor_nextdoor`

**Apify Twitter/X Monitor** (`core/utils/monitors/apify_twitter.py`)
- Replaces need for $100/month X API subscription
- Uses Apify's Tweet Scraper V2 Actor
- Search for service keywords + location terms
- Geo-filter results against customer service areas
- Management command: `python manage.py monitor_twitter_apify`

**Apify TikTok Monitor** (`core/utils/monitors/apify_tiktok.py`)
- NEW source — people post home disaster videos, renovation content, and service requests
- Search for keywords like "need a plumber," "looking for contractor," "home repair help"
- Monitor comments on popular home improvement content for people asking for local help
- Management command: `python manage.py monitor_tiktok`

**Apify Quora Monitor** (`core/utils/monitors/apify_quora.py`)
- NEW source — people ask "best plumber in [city]," "how to find a good contractor in [area]"
- Very high intent — someone asking on Quora is actively researching
- Search by service keywords + location terms
- Management command: `python manage.py monitor_quora`

**Apify Threads Monitor** (`core/utils/monitors/apify_threads.py`)
- NEW source — Meta's growing text platform, 275M+ monthly users
- Local service discussions and recommendations happening here
- Search by service keywords
- Management command: `python manage.py monitor_threads`

**Apify Facebook Marketplace Monitor** (`core/utils/monitors/apify_fb_marketplace.py`)
- NEW source — Facebook Marketplace has a services section
- People post service requests and "looking for" listings
- Filter by category and location
- Management command: `python manage.py monitor_fb_marketplace`

**Apify Google Maps Enhanced** (`core/utils/monitors/apify_google_maps.py`)
- Replaces/supplements Google Places API for outreach campaign prospect scraping
- Bypasses Google's 10,000 unit daily quota
- Richer data extraction including popular times, review highlights
- Also powers enhanced competitor review monitoring at $0.35/1K reviews
- Management command: `python manage.py scrape_google_maps_apify`

**Apify Trustpilot Monitor** (`core/utils/monitors/apify_trustpilot.py`)
- NEW source — competitor review monitoring on Trustpilot
- Same pattern as Yelp/Angi — flag negative reviews as opportunity signals
- Use Claude AI to analyze if reviewer needs alternative provider
- Management command: `python manage.py monitor_trustpilot`

### New Platform Choices and Pill Colors

Add to Lead.platform choices:
```python
('nextdoor', 'Nextdoor'),
('tiktok', 'TikTok'),
('quora', 'Quora'),
('threads', 'Threads'),
('fb_marketplace', 'FB Marketplace'),
('trustpilot', 'Trustpilot'),
```

Add to CSS:
```css
.platform-pill-nextdoor { background: #8ED500; color: #000; }
.platform-pill-tiktok { background: #010101; border: 1px solid #333; }
.platform-pill-quora { background: #B92B27; }
.platform-pill-threads { background: #000000; border: 1px solid #333; }
.platform-pill-fb_marketplace { background: #1877F2; }
.platform-pill-trustpilot { background: #00B67A; }
```

---

## PART 2: PUBLIC RECORDS DATA SOURCES

These are the highest-value data sources that capture demand BEFORE it hits social media. Someone who filed a building permit isn't posting on Reddit asking for a contractor — they're already committed to the project and actively hiring. This data layer catches demand that all 19+ social monitors miss.

### Source 1: Building Permits (HIGHEST PRIORITY)

**Why:** When someone pulls a building permit, they're about to need contractors. A renovation permit means plumbers, electricians, painters, flooring. A new construction permit means every trade. This isn't speculation — they've committed money and filed legal paperwork.

**Data available:** Permit type (renovation, new construction, demolition, plumbing, electrical, roofing), property address, owner name, contractor name (if listed), filing date, estimated project value, permit status.

**Where to get it:** County clerk websites and municipal building department portals. Most publish online searchable databases or weekly/monthly PDF reports. Every county in America has this data.

**Nationwide approach:**
- Build a configurable PermitSource model:
```python
class PermitSource(models.Model):
    county = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    source_url = models.URLField()
    scrape_method = models.CharField(max_length=50, choices=[
        ('html_table', 'HTML Table'),
        ('pdf_report', 'PDF Report'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ])
    css_selectors = models.JSONField(default=dict)  # custom per source
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
```
- Start by building scrapers for the top 20 most populated counties
- Each new county is a configuration entry, not new code
- Run weekly

**Lead alert:** "New renovation permit filed at 123 Main St, [City]. Permit type: Kitchen Remodel. Filed 3 days ago. Estimated value: $45,000."

**Management command:** `python manage.py monitor_permits`

**Platform pill:** Orange/construction (#E67E22) — `permit` platform choice

### Source 2: New Home Sales / Property Transfers

**Why:** New homeowners need everything within 90 days — locksmith, deep cleaning, painter, handyman, landscaper, HVAC maintenance, pest control, gutter cleaning. A single property transfer generates potential demand for 10+ service categories.

**Data available:** Property address, sale date, sale price, buyer name (in most states), property type, square footage.

**Where to get it:** County recorder/assessor websites publish property transfer records. Zillow/Redfin show recently sold homes with addresses (scrapable). Apify has Zillow and Realtor.com scrapers.

**Nationwide approach:**
- Build a PropertyTransferSource model similar to PermitSource
- Scrape county recorder websites or use Apify's Zillow scraper
- Focus on residential sales (filter out commercial, land-only)
- Run weekly

**Lead alert:** "New home sold at 456 Oak Ave, [City]. Closed 5 days ago. Sale price: $425,000. New homeowner likely needs services."

**Management command:** `python manage.py monitor_property_sales`

**Platform pill:** Blue/house (#2980B9) — `property_sale` platform choice

### Source 3: New Business Filings

**Why:** New businesses need commercial cleaning, IT setup, insurance, accounting, legal services, signage, interior buildout, HVAC, security systems. Every new LLC or corporation filing represents a business that hasn't chosen its service providers yet.

**Data available:** Business name, filing date, entity type (LLC, Corp, etc.), registered agent, business address, state of formation.

**Where to get it:** State corporation databases — every state has one:
- New York: Department of State (appext20.dos.ny.gov)
- New Jersey: Division of Revenue (njportal.com/DOR/BusinessNameSearch)
- Connecticut: Secretary of State (service.ct.gov/business)
- All 50 states have similar online portals

**Nationwide approach:**
- Build a StateBusinessFilingSource model
- Each state's portal has a different structure — build per-state scraper classes
- Start with top 10 states by business formation volume
- Run weekly
- Match new filings to customer service areas by registered business address

**Lead alert:** "New LLC filed: 'Garden City Dental Associates LLC' registered in [City] 3 days ago. New business may need commercial cleaning, IT, insurance."

**Management command:** `python manage.py monitor_business_filings`

**Platform pill:** Green/business (#27AE60) — `business_filing` platform choice

### Source 4: Weather and Disaster Alerts

**Why:** After storms, floods, and fires, demand for tree removal, roofing, water damage restoration, mold remediation, and general contractors spikes massively in the affected area. This is time-critical — the first companies to reach affected homeowners get the work.

**Data available:** Weather alert type, severity, affected area (counties/zip codes), start time, expected duration, damage reports.

**Where to get it:** NOAA National Weather Service API — completely free, no key needed.
- API endpoint: `https://api.weather.gov/alerts/active`
- Returns active weather alerts by area
- Can filter by state, county, severity, event type

**Also monitor:**
- FEMA disaster declarations (fema.gov API — free)
- Local news for storm damage reports (existing local news monitor)

**Nationwide approach:**
- Check NOAA alerts API every 15 minutes
- When a severe weather event affects a county where customers operate, immediately alert relevant service providers
- Tree service, roofing, water damage restoration, mold remediation, general contractors, window replacement all get alerts
- Match weather event type to relevant service categories automatically

**Lead alert:** "⚠️ SEVERE STORM: High wind damage reported in [County]. NOAA issued severe thunderstorm warning. Tree service, roofing, and restoration leads likely incoming. Be ready to respond fast."

**Management command:** `python manage.py monitor_weather`

**Platform pill:** Red/alert (#E74C3C) — `weather_alert` platform choice

### Source 5: Code Violations

**Why:** When a property gets a code violation, the owner is legally REQUIRED to fix it. Overgrown lawn = mandatory landscaping hire. Damaged roof = mandatory roofing hire. Peeling paint = mandatory painting hire. This is forced demand — the property owner cannot say no.

**Data available:** Property address, violation type, violation date, compliance deadline, property owner.

**Where to get it:** Municipal code enforcement databases. Most cities and towns publish online portals. Some charge nominal fees for bulk access.

**Nationwide approach:**
- Build a CodeViolationSource model
- Each municipality has a different portal — start with the largest cities
- Focus on violation types that map to service categories (overgrown vegetation → landscaping, structural damage → general contractor, plumbing violations → plumber, etc.)
- Run weekly

**Lead alert:** "Code violation at 789 Elm St, [City]. Violation: Overgrown vegetation/lawn maintenance. Compliance deadline: 30 days. Property owner must hire landscaping."

**Management command:** `python manage.py monitor_code_violations`

**Platform pill:** Yellow/warning (#F1C40F; color: #000) — `code_violation` platform choice

### Source 6: Eviction Filings (Commercial)

**Why:** When a commercial tenant is evicted, the property owner needs the space cleaned, repaired, and prepared for the next tenant. Commercial cleaning, painting, general repair, junk removal, and locksmith services are all needed.

**Data available:** Property address, filing date, case number, plaintiff (landlord/property owner), property type.

**Where to get it:** County court records. Many counties have online court record search portals. Focus on commercial evictions (not residential — residential evictions have ethical concerns around targeting vulnerable populations).

**Nationwide approach:**
- Build a CourtRecordSource model
- Filter for commercial eviction filings only
- Start with counties in customer service areas
- Run weekly

**Lead alert:** "Commercial eviction filed at 100 Commerce Dr, [City]. Filed 5 days ago. Property owner likely needs cleaning, repair, and locksmith services."

**Management command:** `python manage.py monitor_evictions`

**Platform pill:** Gray/legal (#95A5A6) — `eviction_filing` platform choice

### Source 7: Restaurant Health Inspections

**Why:** Restaurants that fail health inspections often need deep cleaning, pest control, kitchen equipment repair, HVAC work, and plumbing. A failed inspection is both forced demand and a time-sensitive lead.

**Data available:** Restaurant name, address, inspection date, score/grade, violations found, follow-up deadline.

**Where to get it:** County and city health department databases. Most publish online. Many states have statewide portals.

**Nationwide approach:**
- Build a HealthInspectionSource model
- Focus on inspections with critical violations or failing grades
- Map violation types to service categories (pest activity → pest control, plumbing issues → plumber, ventilation → HVAC, cleanliness → commercial cleaning)
- Run weekly

**Lead alert:** "Restaurant inspection failure: '[Restaurant Name]' at [address] scored 65/100. Critical violations: pest activity, plumbing leak. Pest control and plumber needed."

**Management command:** `python manage.py monitor_health_inspections`

**Platform pill:** Pink/health (#E91E63) — `health_inspection` platform choice

### Source 8: Expired Contractor Licenses

**Why:** When a competing service provider's license expires or gets suspended, their customers need a new provider. This is direct competitive intelligence.

**Data available:** Contractor name, license number, license type, expiration date, status (expired/suspended/revoked), business address.

**Where to get it:** State licensing board databases. Every state publishes these online.

**Nationwide approach:**
- Build a LicensingBoardSource model
- Monitor for license expirations and suspensions in service categories matching customer types
- Alert customers when a competitor's license expires in their area

**Lead alert:** "Competitor alert: 'ABC Plumbing' (License #12345) license expired 30 days ago in [County]. Their customers may need a new plumber."

**Management command:** `python manage.py monitor_license_expirations`

**Platform pill:** Dark orange/warning (#D35400) — `license_expiry` platform choice

---

## PART 3: ADDITIONAL SOCIAL PLATFORMS

### Quora Monitor
- People ask "best [service] in [city]" and "how to find a good [service provider] in [area]"
- Very high intent — actively researching
- Use Apify's Quora scraper or direct scraping (Quora is publicly visible)
- Search by service keywords + location terms
- Platform: `quora`

### Threads Monitor (Meta)
- 275M+ monthly users on Meta's text platform
- Local service discussions and recommendations growing
- Use Apify's Threads scraper
- Search by service keywords
- Platform: `threads`

### TikTok Monitor
- Home disaster videos, renovation content, "before and after" content
- Comments sections where people ask "who did this work" and "I need someone for this"
- Use Apify's TikTok scraper
- Search for service-related keywords and location terms
- Platform: `tiktok`

### Trustpilot Monitor
- Competitor review monitoring — same pattern as Yelp/Angi
- Negative reviews = opportunity signals
- Use Apify's Trustpilot scraper
- Platform: `trustpilot`

### BBB (Better Business Bureau) Monitor
- BBB.org has business profiles with complaint data
- When a competitor gets a BBB complaint, that customer is looking for an alternative
- Scrapable with BeautifulSoup
- Platform: `bbb`

---

## NEW DATABASE MODELS

```python
# Add to Lead.platform choices - all new sources
PLATFORM_CHOICES = [
    # Existing
    ('craigslist', 'Craigslist'),
    ('reddit', 'Reddit'),
    ('patch', 'Patch.com'),
    ('houzz', 'Houzz'),
    ('alignable', 'Alignable'),
    ('google_qna', 'Google Q&A'),
    ('google_reviews', 'Google Reviews'),
    ('yelp_review', 'Yelp Review'),
    ('angi_review', 'Angi Review'),
    ('thumbtack', 'Thumbtack'),
    ('porch', 'Porch'),
    ('citydata', 'City-Data Forum'),
    ('biggerpockets', 'BiggerPockets'),
    ('parent_community', 'Parent Community'),
    ('local_news', 'Local News/Blog'),
    ('trade_forum', 'Trade Forum'),
    ('facebook', 'Facebook Groups'),
    ('twitter', 'Twitter/X'),
    ('manual', 'Manual Entry'),
    # New Apify-powered social
    ('nextdoor', 'Nextdoor'),
    ('tiktok', 'TikTok'),
    ('quora', 'Quora'),
    ('threads', 'Threads'),
    ('fb_marketplace', 'FB Marketplace'),
    ('trustpilot', 'Trustpilot'),
    ('instagram', 'Instagram'),
    ('bbb', 'BBB'),
    # New public records
    ('permit', 'Building Permit'),
    ('property_sale', 'Property Sale'),
    ('business_filing', 'New Business Filing'),
    ('weather_alert', 'Weather Alert'),
    ('code_violation', 'Code Violation'),
    ('eviction_filing', 'Eviction Filing'),
    ('health_inspection', 'Health Inspection'),
    ('license_expiry', 'License Expiry'),
]

# Public records source models
class PermitSource(models.Model):
    name = models.CharField(max_length=200)
    county = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    source_url = models.URLField()
    scrape_method = models.CharField(max_length=50, choices=[
        ('html_table', 'HTML Table'),
        ('pdf_report', 'PDF Report'),
        ('api', 'API'),
        ('csv_download', 'CSV Download'),
    ])
    css_selectors = models.JSONField(default=dict)
    schedule = models.CharField(max_length=20, default='weekly')
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class PropertyTransferSource(models.Model):
    name = models.CharField(max_length=200)
    county = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    source_url = models.URLField()
    scrape_method = models.CharField(max_length=50)
    css_selectors = models.JSONField(default=dict)
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class StateBusinessFilingSource(models.Model):
    state = models.CharField(max_length=2, unique=True)
    state_name = models.CharField(max_length=50)
    source_url = models.URLField()
    scrape_method = models.CharField(max_length=50)
    css_selectors = models.JSONField(default=dict)
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class CodeViolationSource(models.Model):
    municipality = models.CharField(max_length=200)
    county = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    source_url = models.URLField()
    scrape_method = models.CharField(max_length=50)
    css_selectors = models.JSONField(default=dict)
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class HealthInspectionSource(models.Model):
    jurisdiction = models.CharField(max_length=200)  # county or city
    state = models.CharField(max_length=2)
    source_url = models.URLField()
    scrape_method = models.CharField(max_length=50)
    css_selectors = models.JSONField(default=dict)
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class LicensingBoardSource(models.Model):
    state = models.CharField(max_length=2)
    license_type = models.CharField(max_length=100)  # plumbing, electrical, general contractor, etc.
    source_url = models.URLField()
    scrape_method = models.CharField(max_length=50)
    css_selectors = models.JSONField(default=dict)
    last_scraped = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## BUILD SESSIONS

### Session 13: Apify Core Integration + Facebook + Nextdoor (4-5 hours)
1. Install apify-client
2. Build core/utils/apify_client.py — the unified ApifyIntegration class
3. Add APIFY_API_TOKEN to .env
4. Build Apify Facebook Groups monitor (replaces Playwright version)
5. Build Apify Nextdoor monitor (NEW — highest value addition)
6. Update Lead.platform choices + migration
7. Add new platform pill CSS classes
8. Update lead feed filter dropdown
9. Management commands for both
10. Test with real Apify account

### Session 14: Apify Twitter + TikTok + Quora + Threads (3-4 hours)
1. Build Apify Twitter/X monitor (replaces need for $100/month API)
2. Build Apify TikTok monitor (NEW)
3. Build Apify Quora monitor (NEW)
4. Build Apify Threads monitor (NEW)
5. Build Apify Trustpilot monitor (NEW)
6. Management commands for all

### Session 15: Building Permits + Property Sales (4-5 hours)
1. Build PermitSource and PropertyTransferSource models + migration + admin
2. Build base permit scraper class with configurable CSS selectors per source
3. Build scrapers for top 5 most populated counties as starting templates
4. Build Zillow/property transfer monitor (Apify Zillow scraper or county recorder scraping)
5. Permit-to-service-category mapping logic (renovation permit → plumber, electrician, painter, etc.)
6. Property-sale-to-service-category mapping (new homeowner → cleaner, locksmith, landscaper, etc.)
7. Management commands: monitor_permits, monitor_property_sales
8. Seed initial PermitSource and PropertyTransferSource records for top counties

### Session 16: Business Filings + Weather Alerts (3-4 hours)
1. Build StateBusinessFilingSource model + migration + admin
2. Build state corporation database scrapers starting with NY, NJ, CT, CA, TX, FL
3. Build NOAA Weather API monitor (free API, no key needed)
4. Weather-event-to-service-category mapping (wind → tree/roofing, flood → restoration/plumber, etc.)
5. Business-filing-to-service-category mapping (new dental office → commercial cleaning, IT, insurance, etc.)
6. Management commands: monitor_business_filings, monitor_weather
7. Seed StateBusinessFilingSource records for initial states

### Session 17: Code Violations + Health Inspections + License Expirations (3-4 hours)
1. Build CodeViolationSource, HealthInspectionSource, LicensingBoardSource models + migration + admin
2. Build code violation scrapers for top 10 cities
3. Build health inspection scrapers for top 5 jurisdictions
4. Build license board scrapers for top 5 states
5. Violation-to-service-category mapping
6. Management commands: monitor_code_violations, monitor_health_inspections, monitor_license_expirations
7. Seed initial source records

### Session 18: Eviction Filings + BBB + Enhanced Google Maps via Apify (2-3 hours)
1. Build commercial eviction filing monitor
2. Build BBB complaint/review monitor
3. Build enhanced Google Maps scraper using Apify (bypasses quota limits)
4. Build enhanced Google Maps Reviews using Apify ($0.35/1K)
5. Management commands for all

### Session 19: Expanded Service Categories + Polish (2-3 hours)
1. Expand seed_categories to include ALL industries:
   - Professional Services (insurance, legal, financial, accounting)
   - Healthcare (dentist, chiropractor, PT, therapist, vet, optometrist)
   - Auto (repair, body, detailing, towing)
   - Events (photographer, DJ, caterer, florist, planner)
   - Education (tutors, music teachers, driving instructors)
   - Pet Services (groomer, walker, sitter, trainer, boarding)
   - Senior Care (home health, companion care, assisted living)
   - Technology (IT support, computer repair, managed services)
2. Default keywords for each new category
3. Update onboarding wizard with new industry groups
4. Dashboard home widgets for public records leads (permits, sales, filings)
5. Final testing of all new monitors

---

## COMPLETE PLATFORM COUNT AFTER PHASE 3

**Social Media Monitors (Apify-powered):** Facebook Groups, Nextdoor, Twitter/X, TikTok, Instagram, Quora, Threads, Facebook Marketplace = 8

**Social/Community Monitors (self-hosted scrapers):** Craigslist, Reddit, Patch, Houzz, Alignable, City-Data, BiggerPockets, Parent Communities, Local News, Trade Forums = 10

**Review/Competitor Monitors:** Yelp, Angi, Google Reviews, Porch, Thumbtack, Trustpilot, BBB, Google Q&A = 8

**Public Records Monitors:** Building Permits, Property Sales, New Business Filings, Weather Alerts, Code Violations, Health Inspections, License Expirations, Eviction Filings = 8

**TOTAL: 34 data sources across 4 categories.**

No competitor has anything close to this. Cohesive has 1. Thumbtack captures demand only on their platform. You capture demand across the entire internet AND public records systems.

---

## Messages for Claude Code

### Session 13 — Apify Core + Facebook + Nextdoor

```
Read SALESSIGNAL_BRIEF.md, SALESSIGNAL_PHASE2.md, and SALESSIGNAL_PHASE3.md in this folder. We are building Session 13: Apify integration.

IMPORTANT: This product is NATIONWIDE. All monitors must work for any US geography based on customer-configured service areas.

Build the following:
1. Install apify-client (pip install apify-client, add to requirements.txt)
2. Build core/utils/apify_client.py — a unified ApifyIntegration class that wraps the Apify Python SDK. Methods for each platform we'll scrape via Apify (see SALESSIGNAL_PHASE3.md for full list). Handle running Actors, retrieving results, and error handling.
3. Add APIFY_API_TOKEN to .env and .env.example
4. Build Apify Facebook Groups monitor (core/utils/monitors/apify_facebook.py) — replaces the Playwright-based monitor. Uses ApifyIntegration to scrape Facebook Group posts. Reads MonitoredFacebookGroup model for group URLs. Creates Lead records from matching posts.
5. Build Apify Nextdoor monitor (core/utils/monitors/apify_nextdoor.py) — NEW source. Uses ApifyIntegration to search Nextdoor by location and service keywords. Dynamically determines locations to search based on active BusinessProfile service areas.
6. Update Lead.platform choices to add: 'nextdoor', 'tiktok', 'quora', 'threads', 'fb_marketplace', 'trustpilot', 'instagram', 'bbb', 'permit', 'property_sale', 'business_filing', 'weather_alert', 'code_violation', 'eviction_filing', 'health_inspection', 'license_expiry'. Run migration.
7. Add all new platform pill CSS classes (see SALESSIGNAL_PHASE3.md for colors).
8. Update lead feed filter dropdown with all new platforms.
9. Management commands: monitor_facebook_apify, monitor_nextdoor — both with --dry-run flag.
10. Add APIFY_API_TOKEN to settings.

Follow established monitor patterns. Start building now.
```
