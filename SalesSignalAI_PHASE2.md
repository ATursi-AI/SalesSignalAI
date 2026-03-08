# LeadPulse AI — Phase 2: Expanded Data Sources

## Context

Read this file alongside LEADPULSE_BRIEF.md. Sessions 1-3 from the original brief are complete or in progress. This document covers the expanded set of intent signal monitors and data sources to build in Sessions 4+.

**Current status:** Session 3 in progress — Craigslist monitor and Reddit local monitor being built.

**What's already built or in progress:**
- Django project, all models, design system, dashboard UI (Session 1)
- Lead feed, alert system, urgency badges (Session 2)
- Craigslist monitor + Reddit local monitor (Session 3 — in progress)

---

## Complete Intent Signal Source Map

### Tier 1 — High Volume, Easy to Scrape (already in original brief)

1. **Craigslist "Services Wanted"** — Session 3 (in progress)
2. **Reddit Local Subreddits** — Session 3 (in progress)
3. **Patch.com Community Boards** — original Session 4
4. **Houzz Community Forums** — original Session 4
5. **Alignable Forums** — original Session 4
6. **Google Business Q&A** — original Session 4
7. **Yelp Competitor Reviews** — original Session 4

### Tier 2 — High Value, Newly Identified (add in new sessions)

8. **City-Data.com Forums** — massive forum community with local sub-forums for every state and major metro. People constantly post "looking for a good [service] in [city]." Active since 2004, millions of posts. Standard HTML forums, easy to scrape with BeautifulSoup. No API needed.
   - Target forums: New York, New Jersey, Connecticut, Long Island, Westchester, NYC sub-forums
   - URL pattern: `www.city-data.com/forum/[city-or-state]/`
   - Scrape new threads and posts matching service keywords
   - Extract: post title, content, author, date, location references

9. **BiggerPockets Forums** — real estate investor community where landlords and property managers ask for contractors, plumbers, cleaners, and every service type. This is premium B2B signal — a landlord needing a plumber usually means recurring work across multiple properties. Publicly visible forums.
   - Target sections: Property Management, Maintenance/Rehab, Landlording
   - URL: `www.biggerpockets.com/forums/`
   - Focus on posts mentioning service needs + location in NY/NJ/CT
   - These leads are especially valuable for commercial cleaning, HVAC, plumbing, and handyman categories

10. **Angi/HomeAdvisor Public Reviews** — same concept as Yelp competitor review monitoring. Scrape public business profiles, reviews, and Q&A sections on Angi. Negative reviews = opportunity signals. Someone leaving a 1-star review saying "they never showed up" is actively looking for a replacement.
    - URL pattern: `www.angi.com/nearme/[service-type]/`
    - Scrape competitor reviews sorted by recent
    - Flag 1-2 star reviews as opportunities
    - Use Claude API to analyze if reviewer is looking for alternative

11. **Thumbtack Public Project Listings** — Thumbtack shows some project requests publicly before matching. These are literal "I need [service] in [location]" signals.
    - URL: `www.thumbtack.com/k/[service-type]/near-me`
    - Scrape visible project descriptions and locations
    - Lower volume but very high intent — these people are actively trying to hire

12. **Porch.com Reviews & Profiles** — similar to Angi, scrape competitor reviews for opportunity signals. Also useful as a business data source for outreach campaigns.
    - URL pattern: `porch.com/[city]-[state]/[service-type]`
    - Scrape reviews on competitor profiles

13. **Local Parent Community Sites** — parent networks where families ask for service recommendations. High-value because parents need recurring services (cleaning, lawn care, handyman) and refer extensively within their networks.
    - For NY/NJ/CT: Park Slope Parents (parkslopeparents.com), local Facebook parent groups (via browser automation if accessible)
    - Standard HTML community boards, scrape recommendation request threads
    - These leads tend to convert at higher rates because of community trust

14. **Local News Site Comment Sections / Community Boards** — hyperlocal blogs and news sites where residents discuss services.
    - NJ.com community sections
    - Gothamist comments
    - Long Island Press
    - CT Post / Stamford Advocate community sections
    - Westchester Magazine / 914INC
    - Town-specific WordPress blogs with active comment sections
    - Scrape articles and comments mentioning service needs
    - Lower volume per site but there are dozens of these in the tri-state area

15. **Contractor Talk / Trade Forums** — while primarily pros talking to pros, homeowners do post asking for help, and discussions reveal unmet demand in specific markets.
    - ContractorTalk.com — general contractor forum with trade-specific sections
    - PlumbingZone.com — plumbing-specific
    - HVAC-Talk.com — HVAC-specific
    - ElectricalForum / Mike Holt's Forum — electrical-specific
    - Scrape for homeowner posts asking for service recommendations with location info

16. **Google Maps Reviews (not just Q&A)** — expand beyond Q&A monitoring to scrape recent reviews on competitor Google listings. Same opportunity signal concept as Yelp — negative reviews indicate dissatisfied customers.
    - Already have Google Maps API access from the business scraper
    - Add review monitoring for TrackedCompetitor records
    - Flag negative reviews, run Claude analysis for opportunity scoring

### Tier 3 — Harder to Access but Worth Attempting

17. **Facebook Groups** — highest volume after Nextdoor but requires browser automation with logged-in session via Playwright. Every town has community groups where people post service requests.
    - For NY/NJ/CT: 200+ active community groups
    - Requires dedicated Facebook account
    - Fragile — Facebook changes DOM frequently
    - Build but expect maintenance overhead
    - Start with 10-20 highest-volume groups in the territory

18. **Nextdoor** — the richest source but hardest to access. No API, aggressive anti-scraping. Browser automation possible but risky.
    - Defer to last — only attempt after all other sources are running reliably
    - May need to use a hybrid approach: user joins Nextdoor manually, system helps them monitor and respond faster rather than scraping externally

19. **Twitter/X Local Searches** — people tweet "need a plumber in Brooklyn" regularly. If already paying $100/month for MedSignal's X API, reuse the same subscription for LeadPulse local service searches. Geo-filtered keyword monitoring.

---

## New Models Needed

Add to existing models in core/models.py:

```python
# No new models needed — all new monitors create Lead records using the existing
# Lead model. Just add new platform choices to the Lead.platform field:

PLATFORM_CHOICES = [
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
    ('facebook', 'Facebook'),
    ('nextdoor', 'Nextdoor'),
    ('twitter', 'Twitter/X'),
    ('manual', 'Manual Entry'),
]
```

---

## New Platform Pill Colors (add to leadpulse.css)

```css
/* Existing */
.platform-pill-craigslist { background: #7B2FBE; }
.platform-pill-reddit { background: #FF4500; }
.platform-pill-patch { background: #0EA5E9; }
.platform-pill-houzz { background: #4DBC5B; }
.platform-pill-alignable { background: #1B3A5C; }
.platform-pill-google_qna { background: #4285F4; }
.platform-pill-yelp_review { background: #D32323; }

/* New sources */
.platform-pill-citydata { background: #2D5F8A; }         /* steel blue */
.platform-pill-biggerpockets { background: #F57C00; }     /* orange */
.platform-pill-angi_review { background: #39B54A; }       /* angi green */
.platform-pill-thumbtack { background: #009FD9; }         /* thumbtack blue */
.platform-pill-porch { background: #00BFA5; }             /* teal */
.platform-pill-parent_community { background: #E91E63; }  /* pink */
.platform-pill-local_news { background: #607D8B; }        /* blue gray */
.platform-pill-trade_forum { background: #795548; }       /* brown */
.platform-pill-google_reviews { background: #FBBC04; color: #000; } /* google yellow */
.platform-pill-facebook { background: #1877F2; }
.platform-pill-nextdoor { background: #8ED500; color: #000; }
.platform-pill-twitter { background: #1DA1F2; }
```

---

## Revised Build Sessions (Session 4+)

### Session 4: Patch + Houzz + Alignable + Google Q&A + Yelp (original Session 4)
As originally specced in LEADPULSE_BRIEF.md:
1. Patch.com community board monitor
2. Houzz forum monitor
3. Alignable forum monitor
4. Google Business Q&A monitor
5. Yelp competitor review monitor
6. Management commands for all

### Session 5: City-Data + BiggerPockets + Angi + Thumbtack + Porch (NEW)
Build monitors for the newly identified high-value sources:

1. **City-Data Forum Monitor** (`core/utils/monitors/citydata.py`)
   - Scrape New York, New Jersey, Connecticut, Long Island sub-forums
   - URL pattern: `www.city-data.com/forum/new-york-city/`, `www.city-data.com/forum/new-jersey/`, `www.city-data.com/forum/connecticut/`, `www.city-data.com/forum/long-island/`
   - Look for threads with titles/content matching service keywords
   - Extract post title, body, author, date, sub-forum (for location context)
   - Rate limit: 2-3 second delay between page requests
   - Management command: `python manage.py monitor_citydata`

2. **BiggerPockets Forum Monitor** (`core/utils/monitors/biggerpockets.py`)
   - Scrape Property Management, Landlording, and Maintenance/Rehab forums
   - URL: `www.biggerpockets.com/forums/52` (property management), etc.
   - Focus on posts mentioning service needs with NY/NJ/CT location references
   - These are high-value B2B leads — landlords and property managers = recurring work
   - Management command: `python manage.py monitor_biggerpockets`

3. **Angi Review Monitor** (`core/utils/monitors/angi_reviews.py`)
   - Scrape public reviews on competitor listings on Angi
   - URL pattern: `www.angi.com/companylist/us/[state]/[city]/[service].htm`
   - Flag 1-2 star reviews as potential opportunities
   - Use Claude API to analyze review text: "Is this person looking for an alternative?"
   - Create Lead records for opportunity reviews
   - Management command: `python manage.py monitor_angi_reviews`

4. **Thumbtack Monitor** (`core/utils/monitors/thumbtack.py`)
   - Scrape publicly visible project listings and service request pages
   - URL pattern: `www.thumbtack.com/k/[service]/near-me`
   - Extract project descriptions, service types, and locations
   - These are ultra-high-intent leads — people actively trying to hire
   - Management command: `python manage.py monitor_thumbtack`

5. **Porch Review Monitor** (`core/utils/monitors/porch_reviews.py`)
   - Same pattern as Angi/Yelp review monitoring on competitor profiles
   - Scrape recent reviews, flag negatives as opportunities
   - Management command: `python manage.py monitor_porch`

6. **Google Maps Review Monitor** (extend existing `google_qna.py` or create `google_reviews.py`)
   - Expand Google competitor monitoring to include review scraping, not just Q&A
   - Uses Google Places API — reviews endpoint
   - Flag negative reviews, run Claude opportunity analysis
   - Management command: `python manage.py monitor_google_reviews`

7. Update Lead.platform field choices to include all new sources
8. Add all new platform pill CSS classes
9. Register all new platform options in the lead feed filter dropdown

### Session 6: Local News + Parent Communities + Trade Forums (NEW)
Build monitors for the long-tail community sources:

1. **Local News Monitor** (`core/utils/monitors/local_news.py`)
   - Build a flexible scraper that works across multiple local news/blog sites
   - Configure target sites via a new model or settings:
     ```python
     class MonitoredLocalSite(models.Model):
         name = models.CharField(max_length=200)
         base_url = models.URLField()
         community_section_url = models.URLField(blank=True)
         scrape_pattern = models.CharField(max_length=50, choices=[
             ('wordpress_comments', 'WordPress Comments'),
             ('discourse', 'Discourse Forum'),
             ('custom_html', 'Custom HTML'),
         ])
         css_selectors = models.JSONField(default=dict)  # custom selectors per site
         is_active = models.BooleanField(default=True)
         last_scraped = models.DateTimeField(null=True, blank=True)
     ```
   - Pre-populate with NY/NJ/CT local sites:
     - NJ.com community sections
     - Gothamist
     - Long Island Press
     - CT Post / Stamford Advocate
     - Westchester Magazine
     - Patch.com sub-sites not covered by the Patch monitor
     - Town-specific blogs (discover via Google: `"looking for" "recommend" plumber site:[town-blog-url]`)
   - Management command: `python manage.py monitor_local_news`

2. **Parent Community Monitor** (`core/utils/monitors/parent_communities.py`)
   - Scrape parent network recommendation boards
   - Start with Park Slope Parents (parkslopeparents.com) and similar
   - Look for threads in "recommendations" or "services" sections
   - These leads are gold — parents need recurring services and refer aggressively
   - Management command: `python manage.py monitor_parent_communities`

3. **Trade Forum Monitor** (`core/utils/monitors/trade_forums.py`)
   - Scrape homeowner posts on trade-specific forums
   - Target sites:
     - ContractorTalk.com (general — has sections for every trade)
     - PlumbingZone.com
     - HVAC-Talk.com
     - Mike Holt's Electrical Forum (mikeholt.com/forum)
     - GardenWeb / Houzz Discussions (home improvement)
   - Filter for posts from homeowners (not pros) asking for service help with location info
   - Management command: `python manage.py monitor_trade_forums`

4. Add MonitoredLocalSite model + migration + admin registration
5. Seed MonitoredLocalSite with initial NY/NJ/CT sites

### Session 7: Competitor Intelligence + Territory Map (original Session 5)
As originally specced — competitor tracking, review monitoring dashboard, territory map with lead pins.

### Session 8: Outreach Campaign Engine (original Session 6)
As originally specced — Google Maps scraping, email generation, sending, follow-ups, reply detection.

### Session 9: Facebook Groups Monitor (Tier 3 — optional)
Only build this after all Tier 1 and 2 sources are running:

1. **Facebook Group Monitor** (`core/utils/monitors/facebook_groups.py`)
   - Uses Playwright for browser automation with a logged-in Facebook session
   - Requires a dedicated Facebook account (NOT personal)
   - `.env` variables: `FACEBOOK_EMAIL`, `FACEBOOK_PASSWORD`
   - Monitor specific groups the account has joined
   - New model:
     ```python
     class MonitoredFacebookGroup(models.Model):
         group_name = models.CharField(max_length=200)
         group_url = models.URLField()
         is_active = models.BooleanField(default=True)
         last_checked = models.DateTimeField(null=True, blank=True)
     ```
   - Scrape recent posts matching service keywords
   - CRITICAL: Very aggressive rate limiting — Facebook bans automation quickly
   - Sleep 10-15 seconds between actions, max 50 posts per session
   - Save browser session/cookies to avoid repeated logins
   - Management command: `python manage.py monitor_facebook_groups`
   - Expect this to be fragile and require maintenance when Facebook changes DOM

### Session 10: Analytics + Polish + Weekly Summary (original Session 7)
As originally specced — analytics charts, conversion funnels, platform performance, weekly summary emails.

---

## Updated File Structure (new files only)

```
core/utils/monitors/
├── craigslist.py          # Session 3 (done/in progress)
├── reddit_local.py        # Session 3 (done/in progress)
├── patch.py               # Session 4
├── houzz.py               # Session 4
├── alignable.py           # Session 4
├── google_qna.py          # Session 4
├── yelp_reviews.py        # Session 4
├── citydata.py            # Session 5 (NEW)
├── biggerpockets.py       # Session 5 (NEW)
├── angi_reviews.py        # Session 5 (NEW)
├── thumbtack.py           # Session 5 (NEW)
├── porch_reviews.py       # Session 5 (NEW)
├── google_reviews.py      # Session 5 (NEW)
├── local_news.py          # Session 6 (NEW)
├── parent_communities.py  # Session 6 (NEW)
├── trade_forums.py        # Session 6 (NEW)
└── facebook_groups.py     # Session 9 (NEW, optional)

core/management/commands/
├── monitor_craigslist.py         # Session 3
├── monitor_reddit_local.py       # Session 3
├── monitor_patch.py              # Session 4
├── monitor_houzz.py              # Session 4
├── monitor_alignable.py          # Session 4
├── monitor_google_qna.py         # Session 4
├── monitor_yelp_reviews.py       # Session 4
├── monitor_citydata.py           # Session 5 (NEW)
├── monitor_biggerpockets.py      # Session 5 (NEW)
├── monitor_angi_reviews.py       # Session 5 (NEW)
├── monitor_thumbtack.py          # Session 5 (NEW)
├── monitor_porch.py              # Session 5 (NEW)
├── monitor_google_reviews.py     # Session 5 (NEW)
├── monitor_local_news.py         # Session 6 (NEW)
├── monitor_parent_communities.py # Session 6 (NEW)
├── monitor_trade_forums.py       # Session 6 (NEW)
└── monitor_facebook_groups.py    # Session 9 (NEW)
```

---

## Messages to Paste into Claude Code

### Session 4 — Patch + Houzz + Alignable + Google Q&A + Yelp

```
Read LEADPULSE_BRIEF.md and LEADPULSE_PHASE2.md in this folder. We are building Session 4: the next batch of intent signal monitors.

Build the following monitors, each following the same patterns as the Craigslist and Reddit monitors already built:

1. Patch.com community board monitor (core/utils/monitors/patch.py) — scrape community boards for towns in our service areas. Patch URLs follow pattern patch.com/[state]/[town]. Look for posts in "Neighbors" and "Classifieds" sections matching service keywords. Use requests + BeautifulSoup.

2. Houzz forum monitor (core/utils/monitors/houzz.py) — scrape Houzz community forum (Discourse-based). Focus on "Find a Pro" and "Advice" categories. Extract posts asking for contractor/service recommendations with location info.

3. Alignable forum monitor (core/utils/monitors/alignable.py) — scrape Alignable local community forums. Focus on B2B signals: property managers and business owners asking for service recommendations.

4. Google Business Q&A monitor (core/utils/monitors/google_qna.py) — use Google Places API to check Q&A on competitor listings (TrackedCompetitor records with google_place_id). When someone asks a question on a competitor's listing, create a Lead.

5. Yelp competitor review monitor (core/utils/monitors/yelp_reviews.py) — scrape recent reviews on tracked competitor Yelp pages. Flag 1-2 star reviews as opportunities. Use Claude API to analyze if reviewer is looking for alternative. Create Lead records for opportunity reviews.

Each monitor should: create Lead records with proper platform value, match leads to BusinessProfiles by service type + geography, include rate limiting, and have a management command with --dry-run flag. Start building now.
```

### Session 5 — City-Data + BiggerPockets + Angi + Thumbtack + Porch + Google Reviews

```
Read LEADPULSE_BRIEF.md and LEADPULSE_PHASE2.md in this folder. We are building Session 5: expanded intent signal monitors from newly identified high-value sources.

First, update the Lead model's platform field to add these new choices: 'citydata', 'biggerpockets', 'angi_review', 'thumbtack', 'porch', 'google_reviews'. Run makemigrations and migrate.

Add new platform pill CSS classes to leadpulse.css (see LEADPULSE_PHASE2.md for exact colors).

Update the lead feed filter dropdown to include all new platform options.

Then build these monitors:

1. City-Data Forum Monitor (core/utils/monitors/citydata.py) — scrape New York, New Jersey, Connecticut, and Long Island sub-forums at city-data.com/forum/. Look for threads matching service keywords. Standard HTML, use BeautifulSoup. Rate limit 2-3 seconds between requests.

2. BiggerPockets Forum Monitor (core/utils/monitors/biggerpockets.py) — scrape Property Management, Landlording, and Maintenance/Rehab forums. Focus on posts mentioning service needs with NY/NJ/CT location references. These are high-value B2B leads from landlords and property managers.

3. Angi Review Monitor (core/utils/monitors/angi_reviews.py) — scrape public reviews on competitor listings on angi.com. Flag 1-2 star reviews as opportunities. Use Claude API to analyze review text for opportunity scoring.

4. Thumbtack Monitor (core/utils/monitors/thumbtack.py) — scrape publicly visible project listings at thumbtack.com/k/[service]/near-me. Extract project descriptions, service types, locations. Ultra-high-intent leads.

5. Porch Review Monitor (core/utils/monitors/porch_reviews.py) — scrape competitor reviews on porch.com profiles. Same opportunity flagging pattern as Yelp/Angi.

6. Google Maps Review Monitor (core/utils/monitors/google_reviews.py) — expand Google competitor monitoring to include review scraping via Places API. Flag negative reviews, run Claude opportunity analysis.

Each monitor needs a management command with --dry-run flag. Follow established patterns. Start building now.
```

### Session 6 — Local News + Parent Communities + Trade Forums

```
Read LEADPULSE_BRIEF.md and LEADPULSE_PHASE2.md in this folder. We are building Session 6: long-tail community signal sources.

First, create the MonitoredLocalSite model (see LEADPULSE_PHASE2.md for field definitions) + migration. Register in admin. This model allows configuring different local news/blog sites with custom CSS selectors per site.

Seed MonitoredLocalSite with initial NY/NJ/CT sites: NJ.com, Gothamist, Long Island Press, CT Post, Stamford Advocate, Westchester Magazine.

Update Lead model platform choices to add: 'local_news', 'parent_community', 'trade_forum'. Run migration.

Add platform pill CSS for new sources (see LEADPULSE_PHASE2.md for colors).

Then build:

1. Local News Monitor (core/utils/monitors/local_news.py) — flexible scraper that reads MonitoredLocalSite configs. Supports WordPress comment scraping, Discourse forums, and custom HTML patterns via configurable CSS selectors. Scrapes community sections and comment threads for service recommendation requests.

2. Parent Community Monitor (core/utils/monitors/parent_communities.py) — scrape parent network recommendation boards. Start with Park Slope Parents and similar NY-area parent communities. Look for recommendation request threads mentioning services.

3. Trade Forum Monitor (core/utils/monitors/trade_forums.py) — scrape homeowner posts on ContractorTalk.com, PlumbingZone.com, HVAC-Talk.com, and similar trade forums. Filter for posts from homeowners (not pros) asking for service help with location info.

Each needs a management command with --dry-run flag. Follow established patterns. Start building now.
```
