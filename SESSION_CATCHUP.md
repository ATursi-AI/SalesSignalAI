# SalesSignalAI Session Catch-Up Document
**Last Updated: April 1, 2026**
**Give this to Claude at the start of every new session.**

---

## CRITICAL RULES — READ FIRST
- **NEVER show or read .env file contents**
- **"Source first protocol"** — always read files before editing, never guess filenames
- **PowerShell uses `;` not `&&`** for command chaining (local machine is Windows)
- **Deployment workflow**: local edits → `git push` → VPS `git pull` → `sudo systemctl restart salessignal`
- **User does web searching, Claude writes code** — teamwork approach. Andrew said: "no guessing. you and i work as a team, ill help search you will guide us"
- **Conserve tokens** — browser tools freeze Andrew's computer. Don't use Chrome browser tools unless asked.
- Andrew is the founder/developer. His sales team calls leads on behalf of customers — customers don't call leads themselves.

---

## PROJECT OVERVIEW

**SalesSignalAI** is a lead intelligence platform. The core business model: public data monitors find businesses with problems (health violations, code violations, no website, bad reviews, new permits, etc.) → Andrew's sales team calls those businesses within days → offers service providers to fix their issues before re-inspection.

**Customer base**: Blue collar service businesses (plumbers, pest control, electricians, HVAC) AND professional service businesses. They pay $599-$1,999/month for leads delivered to them.

---

## TECH STACK
- **Framework**: Django 5.x / Python 3.12 / SQLite
- **Server**: Gunicorn / Nginx on Hostinger Ubuntu 24 VPS
- **VPS path**: `/root/SalesSignalAI/`, port 8003, service name: `salessignal`
- **Local path**: `C:\Users\aturs\Documents\AI_Engineering_Course\SalesSignalAI`
- **Integrations**: SignalWire (calls/SMS), Stripe (billing), SendGrid (email — just set up, DNS records added to Hostinger, not yet integrated into codebase), HeyGen (AI videos)

---

## WHAT'S WORKING — MONITORS

### Health Inspection Monitors (Western US Expansion)
| Monitor | Source | Status | Leads (7d) | Notes |
|---------|--------|--------|------------|-------|
| Sacramento | ArcGIS FeatureServer | ✅ WORKING | 90 | Layer 0, daily updates |
| LA County | ArcGIS FeatureServer | ✅ WORKING | 1,790 facilities | Quarterly data, 120-day window. Violations fetch returns 0 (not debugged) |
| Denver | myhealthdepartment.com POST API | ✅ WORKING | 111 | Day-by-day pagination workaround |
| Colorado Springs | myhealthdepartment.com POST API | ✅ WORKING | 73 | Uses `result` not `scoreDisplay` |
| Honolulu | myhealthdepartment.com POST API | ✅ WORKING | 82 | |
| Portland | myhealthdepartment.com POST API | ✅ WORKING | 25 | 14-day window (low volume) |
| Las Vegas | SNHD nightly ZIP/CSV | ✅ WORKING | 1,922 | |
| Santa Clara | Socrata SODA API | ✅ WORKING | — | 3 joined datasets |
| San Diego | Unverified | ❌ UNTESTED | — | Endpoints not confirmed |

### Key Technical Details
- **ArcGIS**: Uses `timestamp 'YYYY-MM-DD HH:MM:SS'` date syntax, epoch-ms in responses, `maxRecordCount` pagination (1000/page), check `exceededTransferLimit` flag
- **myhealthdepartment.com**: POST API `{task: "searchInspections", data: {path, programName, filters: {date}}}` — caps at 25 records per request. **Workaround**: day-by-day requests (one per day in the date range) to bypass the cap
- **Shared `_arcgis_fetch()` helper** in `ca_health_inspections.py` handles pagination properly

### Key Files for Monitors
- `core/utils/monitors/ca_health_inspections.py` — Sacramento, LA County, Santa Clara, San Diego
- `core/utils/monitors/myhealthdept.py` — Denver, Portland, Colorado Springs, Honolulu
- `core/utils/monitors/vegas_health.py` — Las Vegas
- `core/utils/monitors/schedule.py` — Single source of truth for all monitor schedules
- `core/utils/lead_processor.py` — Shared pipeline (dedup via content_hash, keyword matching, business assignment)

---

## FEATURES BUILT THIS SESSION

### 1. Voicemail Drops (`/dashboard/voicemail-drops/`)
- Pre-recorded voicemail templates with audio URLs
- SignalWire integration with machine detection — plays audio when VM picks up
- Bulk send to phone number lists or sales prospect IDs
- Status webhook tracks delivery/failed/busy
- Dashboard with delivery rate stats and activity log
- **Files**: `core/models/engagement.py`, `core/views/engagement.py`, `core/templates/engagement/voicemail_drops.html`

### 2. Self-Service Booking Pages (`/dashboard/booking-pages/` + `/book/<slug>/`)
- Each business gets a public booking page at `/book/your-slug/`
- 3-step flow: Pick Date → Pick Time → Enter Info → Confirmation
- Configurable: available days, hours, slot duration, max per day
- Auto-creates CRM Contact + Appointment on booking
- SMS confirmation to booker + SMS alert to business owner
- Standalone dark theme page, no login needed
- **Files**: Same as above, plus `core/templates/engagement/booking_public.html`, `booking_pages.html`

### 3. Review Campaigns (`/dashboard/review-campaigns/`)
- Create campaigns with Google review URL + SMS template
- Send review requests to all "won" contacts in one click
- Click tracking via `/r/<id>/` redirect URL
- Auto-send option when contact stage becomes "won"
- Campaign detail page with sent/clicked/reviewed metrics
- **Files**: Same engagement files, plus `core/templates/engagement/review_campaigns.html`, `review_campaign_detail.html`

### 4. Sidebar Updated
- New "Engagement" section in `base.html` between CRM and Outreach
- Links to Voicemail Drops, Booking Pages, Reviews

### 5. SignalWire Service Updated
- Added `drop_voicemail()` and `drop_voicemail_bulk()` to `core/services/signalwire_service.py`

### 6. New Models (need migration on VPS)
- `VoicemailDrop`, `VoicemailDropLog`
- `BookingPage`, `BookingSubmission`
- `ReviewCampaign`, `ReviewRequest`
- All in `core/models/engagement.py`, imported in `core/models/__init__.py`

---

## EXISTING FEATURES (Built in Previous Sessions)

### Prospect Video Pages
- Personalized video landing pages for prospects at `/demo/<slug>/`
- Two modes: SalesSignal prospecting (intake form) or White-label (customer branded)
- HeyGen script generators built in
- Tracking: page views, video plays, CTA clicks
- **Files**: `core/models/prospect_videos.py`, `core/views/prospect_videos.py`, `core/templates/prospect_videos/`

### Full CRM
- Contact pipeline (Kanban), activity timeline, appointments
- Outreach campaigns with AI email generation (1-3 email sequences)
- Workflow automation (9 trigger types, 9 action types)
- Competitor intelligence with review tracking
- Service landing pages (SEO)
- Sales team tools (pipeline, call scripts, email templates, power dialer)
- Call center (SignalWire softphone, SMS inbox, call logging)

### Subscription Tiers
- Trial, Outreach ($599), Growth ($1,199), Dominate ($1,999), Concierge, Custom Outbound
- Stripe integration for payments

---

## KNOWN BUGS — FIX NEXT SESSION

### 1. Customer Accounts & Mission Control pages return 404
- Sidebar links exist but pages are not loading
- URLs in `core/urls.py`: `/admin-leads/customers/` → `admin_leads.customer_accounts` and `/admin-leads/mission-control/` → `admin_leads.mission_control`
- Need to check if views exist in `core/views/admin_leads.py`

### 2. LA County violations fetch returns 0
- Inspections work (1,790 facilities) but violation details via SERIAL_NUMBER batch lookup returns nothing
- Lower priority since core data with owner names works

### 3. San Diego monitor untested
- Endpoints in `ca_health_inspections.py` not verified

### 4. Engagement features need migration on VPS
- After pushing, run: `python manage.py makemigrations && python manage.py migrate`

---

## PENDING TASKS FOR NEXT SESSION

1. **Fix Customer Accounts & Mission Control 404s** (Andrew flagged these)
2. **Integrate SendGrid into codebase** — account created, DNS records added to Hostinger (CNAME for link, em, DKIM + TXT for DMARC), needs API key wired into Django settings and email sending code
3. **Reddit scraping optimization** — Andrew mentioned this for tomorrow
4. **Apify setup** — Andrew has an account, needs to connect it for Facebook and Twitter scraping ("does anyone know a good xyz?" posts)
5. **Nextdoor scraping** — mentioned as a target platform
6. **Previous session pending**: Stripe STRIPE_PUBLISHABLE_KEY in .env, Stripe webhook setup, business model migration

---

## DEPLOYMENT COMMANDS

**Local git push:**
```
git add [files]
git commit -m "message"
git push
```

**VPS deploy:**
```
cd /root/SalesSignalAI && git pull && sudo systemctl restart salessignal
```

**VPS deploy with migrations:**
```
cd /root/SalesSignalAI && git pull && python manage.py makemigrations && python manage.py migrate && sudo systemctl restart salessignal
```

**Test a monitor:**
```
python manage.py monitor_myhealthdept --jurisdiction denver --days 7 --dry-run
python manage.py monitor_ca_health --county sacramento --days 7 --dry-run
```

---

## FILE STRUCTURE (Key Directories)
```
core/
  models/
    __init__.py          — All model imports
    business.py          — BusinessProfile, ServiceCategory
    leads.py             — Lead, LeadAssignment
    crm.py               — Contact, Activity, Appointment
    outreach.py          — OutreachCampaign, OutreachProspect, GeneratedEmail
    engagement.py        — NEW: VoicemailDrop, BookingPage, ReviewCampaign
    call_center.py       — CallLog, SMSMessage
    prospect_videos.py   — ProspectVideo
    competitors.py       — TrackedCompetitor, CompetitorReview
    workflows.py         — WorkflowRule, WorkflowExecution
    sales.py             — SalesPerson, SalesProspect, SalesActivity
  views/
    engagement.py        — NEW: All 3 engagement features
    admin_leads.py       — Lead repository, mission control, customer accounts
    call_center.py       — SignalWire webhooks, softphone, SMS inbox
    crm.py               — Pipeline, contacts, appointments
    campaigns.py         — Outreach campaigns
    prospect_videos.py   — Video landing pages
  services/
    signalwire_service.py — SMS, calls, voicemail drops
  utils/
    monitors/
      ca_health_inspections.py  — CA county monitors
      myhealthdept.py           — Denver, Portland, CO Springs, Honolulu
      vegas_health.py           — Las Vegas
      schedule.py               — Monitor schedule config
      lead_processor.py         — Shared lead processing pipeline
  templates/
    engagement/          — NEW: All engagement templates
    prospect_videos/     — Video page templates
    base.html            — Main layout with sidebar
```
