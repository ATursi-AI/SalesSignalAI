# Claude Code Prompt: Lead Repository Redesign — Multi-State, Multi-Source Architecture

## Context

SalesSignalAI at /root/SalesSignalAI/ currently has a flat lead repository at /admin-leads/ that shows all leads on one page. We're adding many more data sources (property sales, permits, health inspections, liquor licenses, business filings) on top of existing ones (Reddit, Google Places, Nextdoor, weather, DOB violations). The volume will be 4,000+ violations alone for NYC, plus thousands more from other sources. When we expand to other states (California, New Jersey, Connecticut, etc.), leads from different states need to go to different salespeople and customers. The current single-page approach won't scale.

## New Architecture: Three-Level Lead Repository

### Level 1 — Command Center (main /admin-leads/ page)

This is the daily workhorse. A dashboard with:

**Top bar: State/Region selector**
- Dropdown or toggle buttons: "All States" | "New York" | "California" | "New Jersey" | etc.
- Selecting a state filters EVERYTHING on the page
- Default to "All States" for admin, auto-filter to assigned state for salespeople
- Store the user's last selected state in session so it persists

**Summary row: Urgency cards**
- Three cards showing counts: "🔴 47 HOT" | "🟡 183 WARM" | "🔵 612 COLD"
- Counts update based on state filter
- Clicking a card filters the feed below to that urgency level

**Source overview cards (below urgency)**
- One card per source group, showing count of unreviewed leads:
  - "Public Records: 4,287" (expand: Violations 978, Permits 1000, Property Sales 412, Health Inspections 89, Liquor Licenses 34, Business Filings 1774)
  - "Social Media: 142" (expand: Reddit 89, Nextdoor 41, Facebook 12)
  - "Review Sites: 67" (expand: Google Places Reviews 45, No-Website Prospects 22)
  - "Weather/Events: 8" (NOAA alerts)
- Clicking a source group card navigates to Level 2 for that group
- Clicking a specific sub-source navigates directly to that source's filtered view

**Unified feed (main content area)**
- Shows newest/hottest leads across ALL sources, sorted by urgency then recency
- Each lead row shows: urgency badge, source icon/label, title, location, contact name (if available), time ago
- Paginated, 50 leads per page
- Quick action buttons: Approve, Reject, Assign (to salesperson)
- Filter bar above feed: Source dropdown, Urgency, Borough/County, Date range, Status (Unreviewed/Approved/Rejected)

### Level 2 — Source Group Pages

Separate pages for each source group, accessible from sidebar and from source cards on Command Center:

**/admin-leads/public-records/** — All public record leads
- Sub-tabs across top: All | Violations | Permits | Property Sales | Health Inspections | Liquor Licenses | Business Filings
- Each tab has source-specific filters:
  - Violations: severity, violation_type, penalty range, borough
  - Permits: work_type, estimated_cost range, borough
  - Property Sales: sale amount range, borough
  - Health Inspections: critical_flag, score range, borough, cuisine_type
  - Liquor Licenses: license type, status (pending/active), county
  - Business Filings: entity_type, filing_type, county
- Bulk actions: Approve all, Reject all, Assign all to salesperson
- Export to CSV button

**/admin-leads/social-media/** — Reddit, Nextdoor, Facebook
- Sub-tabs: All | Reddit | Nextdoor | Facebook

**/admin-leads/reviews/** — Google Places
- Sub-tabs: All | Negative Reviews | No-Website | Q&A

**/admin-leads/weather/** — NOAA alerts

### Level 3 — Lead Detail (existing, enhance)

Click any lead from any view to see full detail. Enhance with:
- All raw data fields from the source
- Contact info prominently displayed
- Map showing location (if lat/long available)
- "Similar leads nearby" section
- Activity log (who viewed, approved, assigned, contacted)
- Quick assign to salesperson
- Quick create outbound email campaign for this lead

## Sidebar Navigation

Always-visible left sidebar:

```
📊 Command Center          (/admin-leads/)
📍 [State: New York ▼]     (state selector)

SOURCES
├── 📋 Public Records      (/admin-leads/public-records/)
│   ├── Violations (978)
│   ├── Permits (1,009)
│   ├── Property Sales (0)
│   ├── Health Inspections (0)
│   ├── Liquor Licenses (0)
│   └── Business Filings (0)
├── 💬 Social Media         (/admin-leads/social-media/)
│   ├── Reddit (13)
│   ├── Nextdoor (4)
│   └── Facebook (0)
├── ⭐ Reviews              (/admin-leads/reviews/)
│   ├── Negative Reviews (33)
│   └── No-Website (2)
└── 🌤️ Weather              (/admin-leads/weather/)
    └── NOAA Alerts (8)

TOOLS
├── 📧 Email Campaigns
├── 👥 Sales Team
└── ⚙️ Settings
```

Numbers in parentheses = unreviewed count, updated dynamically.

## Data Model Changes

### Add to Lead model:

```python
# State/region for multi-state support
state = models.CharField(max_length=2, blank=True, default='NY',
    help_text='Two-letter state code')
region = models.CharField(max_length=100, blank=True,
    help_text='Sub-region: borough, county, city, etc.')

# Source classification
source_group = models.CharField(max_length=50, choices=[
    ('public_records', 'Public Records'),
    ('social_media', 'Social Media'),
    ('reviews', 'Review Sites'),
    ('weather', 'Weather/Events'),
], default='public_records')

source_type = models.CharField(max_length=50, choices=[
    # Public Records
    ('violations', 'DOB Violations'),
    ('permits', 'DOB Permits'),
    ('property_sales', 'Property Sales'),
    ('health_inspections', 'Health Inspections'),
    ('liquor_licenses', 'Liquor Licenses'),
    ('business_filings', 'Business Filings'),
    # Social Media
    ('reddit', 'Reddit'),
    ('nextdoor', 'Nextdoor'),
    ('facebook', 'Facebook Groups'),
    # Reviews
    ('google_reviews', 'Google Reviews'),
    ('no_website', 'No Website Detected'),
    ('google_qa', 'Google Q&A'),
    # Weather
    ('noaa', 'NOAA Weather'),
], blank=True)

# Contact info (standardized across all sources)
contact_name = models.CharField(max_length=200, blank=True)
contact_phone = models.CharField(max_length=20, blank=True)
contact_email = models.EmailField(blank=True)
contact_business = models.CharField(max_length=200, blank=True)
contact_address = models.TextField(blank=True)
```

### Update existing monitors to populate these new fields:
- All monitors should set `state`, `region`, `source_group`, `source_type`
- NYC monitors: state='NY', region=borough name
- LI monitors: state='NY', region=county name
- Future CA monitors: state='CA', region=county/city

## Existing Violations Monitor — Field Verification

The current violations monitor uses dataset `6bgk-3dad` (DOB ECB Violations) on data.cityofnewyork.us.

Verified API fields (confirmed from actual API response):
- `issue_date` — YYYYMMDD text format, sorts correctly as strings
- `respondent_name` — lead contact name (e.g., "HAROLD,POMERANZ")
- `respondent_house_number`, `respondent_street`, `respondent_city`, `respondent_zip` — address
- `violation_description` — what's wrong
- `penality_imposed` — dollar amount (NOTE: misspelled in API as "penality" not "penalty")
- `balance_due` — outstanding amount
- `severity` — "Hazardous", "CLASS - 1", "CLASS - 2"
- `violation_type` — "Construction" etc.
- `ecb_violation_status` — "RESOLVE" vs active
- `ecb_violation_number` — unique ID for dedup
- `boro` — borough code (1=Manhattan, 2=Bronx, 3=Brooklyn, 4=Queens)
- `hearing_date`, `hearing_status`

IMPORTANT: Make sure the monitor is:
1. Filtering for RECENT issue_date only (last N days), not pulling resolved violations from 1990
2. Filtering for ACTIVE violations only (ecb_violation_status != 'RESOLVE' or balance_due > 0)
3. Mapping respondent fields to the new standardized contact fields
4. Setting source_group='public_records', source_type='violations', state='NY', region=borough name
5. Using penality_imposed (with the typo) and balance_due for urgency scoring

## Styling

Match the existing dark theme dashboard style. Use the same card components, colors, and layout patterns as the existing dashboard pages. Sidebar should be collapsible on mobile.

## Implementation Priority

1. Add new model fields and run migration
2. Build the sidebar navigation
3. Build the Command Center page
4. Build the Public Records source group page with sub-tabs
5. Update existing monitors to populate new fields
6. Build other source group pages
