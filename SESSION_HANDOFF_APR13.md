# SalesSignalAI Session Handoff — April 13, 2026

## CRITICAL RULES
- Source first protocol — read files before editing, NEVER guess filenames
- Never show .env files
- "Starting at" before all prices, no money-back guarantees
- PowerShell uses `;` not `&&`
- Warn at 96% context, provide handoff
- Protected files: `landing.html`, `base.html` (unless explicitly instructed)
- Claude Code does coding locally, push to git, pull from VPS

---

## WHAT WAS COMPLETED THIS SESSION

### 1. Sequence Data Rendered on Sales Dashboard
- **File:** `core/templates/sales/dashboard.html`
- Template now renders `active_sequences`, `sequence_call_tasks`, `due_enrollment_count`, `recent_replies`
- Two cards between the two-column layout and Pipeline section
- Template-only, no Python changes

### 2. Pricing Page ROI Section Fixed
- **File:** `core/templates/pricing.html`
- Dominate: $899/mo → $1,999/mo (line 333)
- Closer: $2,499/mo → $3,999/mo (line 339)
- Rest of pricing page verified correct

### 3. Engagement Tool Access for Salespeople
- **File:** `core/templates/base.html` — Engagement section extracted to own conditional including `salesperson_profile`
- **File:** `core/views/engagement.py` — Added `_is_sales_user()` helper, updated voicemail_drops, booking_page_list (also fixed duplicate @login_required), booking_submission_action, review_campaigns, review_campaign_detail, review_campaign_toggle, review_campaign_send
- Pattern: sales users see ALL records across all customers

### 4. Enhanced Scheduling on Prospect Detail
- **File:** `core/templates/sales/prospect_detail.html` — Modal now has type (Follow-up/Demo/Meeting/Callback), date+time, notes
- **File:** `core/views/sales.py` — `schedule_followup` handler creates SalesActivity with `is_task=True`, formats time as 12-hour, auto-advances pipeline to `demo_scheduled` for demos

### 5. Onboard Customer Page Rebuilt
- **File:** `core/models/business.py` — TIER_CHOICES expanded to 20 options (10 package bundles, trial, custom, 5 legacy)
- **File:** `core/views/signup.py` — Added `PACKAGE_BUNDLES` dict (10 plans with Stripe payment links), `ALACARTE_SERVICES` dict (22 services with links), `SETUP_FEE_LINK`. Rewrote `sales_create_customer` view
- **File:** `core/templates/sales/create_customer.html` — Complete redesign with 3 tabs (Packages/A La Carte/Trial), AI vs Human+AI selection, payment options
- **File:** `core/templates/sales/create_customer_success.html` — Updated with plan display, copy-to-clipboard payment links, a la carte service links
- **Migration:** `0048_update_tier_choices`

### NOT YET PUSHED TO VPS
All 5 items above are built locally. Push/pull commands:

**Local (PowerShell):**
```
cd C:\Users\aturs\Documents\AI_Engineering_Course\SalesSignalAI
git add .
git commit -m "Sales dashboard: sequences, pricing fix, engagement access, scheduling, onboard customer rebuild"
git push origin main
```

**VPS:**
```
cd /root/SalesSignalAI
git pull origin main
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart salessignal
```

---

## NEXT TASK: Give Salespeople Full CRM + Outreach + Call Center Access

This is the immediate next thing to build. Everything below is verified from reading the actual files.

### The Problem
Salespeople have `SalesPerson` profile but NOT `BusinessProfile`. The sidebar hides CRM, Outreach, and Call Center sections behind `{% if user.business_profile %}`. The CRM views in `crm.py` all call `_get_business(request)` and redirect to onboarding if None.

### What a Salesperson Currently Sees in Sidebar
(Verified from `base.html` — read the file)
- **Engagement** (line 95-109): Voicemail Drops, Booking Pages, Reviews ✅ DONE
- **Sales** (line 131-173): Dashboard, High-Value Leads, My Pipeline, My Prospects, Today's Calls, Calendar, My Stats, Phone, SMS Inbox, My Calls
- **Leads** (line 195+, because is_staff): Command Center, Sources, Customer Accounts, Onboard Customer, Mission Control
- **Sales Tools** (line 267-275): GEO Audit, Agent REP

### What They're Missing
- **CRM** (line 67-93, gated by `business_profile`): Conversations, Pipeline, Contacts, Inbox, Appointments, Competitors
- **Outreach** (line 111-129, gated by `business_profile`): Compose Email, Campaigns, Workflows
- **Call Center Dashboard** (line 189, gated by `is_superuser`)

### Files to Edit

**`core/templates/base.html`** — THREE sidebar changes:
1. CRM section (line 67): Extract from `business_profile` block, give it own gate: `{% if user.business_profile or user.salesperson_profile or user.is_superuser %}`
2. Outreach section (line 111): Same change
3. Add Call Center Dashboard link inside the Sales section (after My Calls, before `{% endif %}`)
4. CRITICAL: The `{% endif %}` on line 93 currently closes both Main AND CRM — must split them into separate if/endif pairs

**`core/views/crm.py`** — Add `_is_sales_user()` helper and update ALL views:
- Same pattern as `engagement.py`: if sales user and no business_profile, show data across all customers
- Views to update: `pipeline`, `pipeline_move`, `contact_list`, `contact_detail`, `contact_add_note`, `contact_create`, `inbox`, `appointment_list`, `appointment_create`, `appointment_update_status`, `competitor_dashboard`, `revenue_data`
- Each currently does: `bp = _get_business(request); if not bp: return redirect('onboarding')`
- Change to: if `_is_sales_user()` and no bp, set bp=None and query all records; otherwise redirect

**`core/views/conversations.py`** — READ THIS FILE FIRST. It hasn't been reviewed yet. Apply same `_is_sales_user()` pattern if it's gated behind `business_profile`.

**`core/views/campaigns.py`** — DO NOT EDIT. Already uses `is_staff` checks throughout. Salespeople are `is_staff=True` so campaigns already work. Just needs the sidebar link to be visible (handled in base.html change).

### CRM View Pattern (from reading `crm.py`)

Every view follows this pattern:
```python
bp = _get_business(request)
if not bp:
    return redirect('onboarding')
# then uses bp to filter: Contact.objects.filter(business=bp)
```

Change to:
```python
bp = _get_business(request)
if not bp:
    if _is_sales_user(request):
        bp = None
    else:
        return redirect('onboarding')

# Then for queries:
if bp:
    contacts = Contact.objects.filter(business=bp)
else:
    contacts = Contact.objects.all()  # Sales users see all
```

### CRM View Details (from reading `crm.py`)

- `pipeline()` (line 34): Filters contacts by business, shows kanban with stages: new, contacted, follow_up, quoted, won, lost
- `pipeline_move()` (line 63): AJAX POST, moves contact to new stage, syncs to LeadAssignment
- `contact_list()` (line 116): Filterable table with search, stage, source filters
- `contact_detail()` (line 157): Left panel (info, pipeline buttons, deal info, appointments) + right panel (activity timeline). Has Edit Contact modal and Add Activity modal
- `contact_add_note()` (line 177): POST, creates Activity. Auto-changes pipeline stage for won/lost/quoted
- `contact_update()` (line ~234, truncated): POST, updates contact fields
- `contact_create()` (line ~310, truncated): POST, creates new Contact with Activity log
- `inbox()` (line 349): Shows replied emails from OutreachEmail and OutreachProspect
- `appointment_list()` (line 379): Upcoming + past appointments, with create modal
- `appointment_create()` (line 405): POST, creates Appointment + Activity log
- `appointment_update_status()` (line 447): POST, changes appointment status
- `competitor_dashboard()` (line 467): Shows tracked competitors with negative reviews
- `revenue_data()` (line 504): JSON endpoint for dashboard widget

### CRM Templates (verified from uploaded files)

- `crm/pipeline.html` — Kanban board with drag-and-drop, stats bar (active deals, pipeline value, won revenue)
- `crm/contacts.html` — Searchable table with filters, Add Contact modal
- `crm/contact_detail.html` — Two-panel layout: info/pipeline/deal/appointments on left, activity timeline on right. Edit Contact modal, Add Activity modal with type selector
- `crm/appointments.html` — Upcoming + past lists, Book Appointment modal with contact picker, date/time, duration, service, notes

### Other Templates Read This Session

- `sales/dashboard.html` — Metrics, Your Day, Recent Activity, Sequences, Pipeline summary
- `sales/pipeline.html` — Kanban with board/list toggle, drag-and-drop
- `sales/prospects.html` — Table with filters, Add Prospect modal
- `sales/prospect_detail.html` — Business info form, Quick Actions (Log Activity, Schedule, Mark Won/Lost), Activity Timeline, Email Compose with templates + AI draft, Call Script with objection handlers
- `sales/today.html` — Call goal progress, overdue/today follow-ups, new prospects
- `sales/stats.html` — KPIs, monthly breakdown, team leaderboard
- `sales/calendar.html` — Day/week/month views
- `sales/high_value_leads.html` — $5K+ leads with filters
- `sales/create_customer.html` — Rebuilt with 3-tab pricing
- `sales/create_customer_success.html` — Updated with payment links
- `call_center/softphone.html` — SignalWire softphone (not read in detail)
- `call_center/sms_inbox.html` — Two-panel SMS layout, conversation list + thread view + reply
- `call_center/my_calls.html` — Call + SMS history tables
- `call_center/dashboard.html` — Team-wide stats, recent calls, recent SMS (this is the Call Center Dashboard)

---

## PRICING (Finalized — Verified from pricing.html + stripe_payment_links.md)

### A La Carte Services
Email drip ($199/$399), Video email ($349/$599), Lead access ($299/mo or $125/lead), Social listings ($349/$699), Appointment setting ($99/$175 per appt), Inbound call center ($399/$699), Outbound call center ($599/$1,199), Landing page ($99/$149/mo + setup), Outbound sales team ($3,999/$7,499), SEO+AEO ($399/$799), BYO leads ($199/$299 + per appt).

### Package Bundles
| Tier | AI Automated | Human + AI |
|------|-------------|------------|
| Starter | Starting at $599/mo | Starting at $999/mo |
| Growth | Starting at $1,199/mo | Starting at $1,999/mo |
| Dominate (Most Popular) | Starting at $1,999/mo | Starting at $3,499/mo |
| Closer | Starting at $3,999/mo | Starting at $6,499/mo |
| Full Service | Starting at $7,999/mo | Starting at $12,999/mo |

Setup fee: $299 one-time. All Stripe payment links are in `stripe_payment_links.md` in project files.

### BusinessProfile.TIER_CHOICES (Updated)
`none`, `trial`, `starter_ai`, `starter_human`, `growth_ai`, `growth_human`, `dominate_ai`, `dominate_human`, `closer_ai`, `closer_human`, `full_service_ai`, `full_service_human`, `custom`, plus legacy: `outreach`, `growth`, `dominate`, `concierge`, `custom_outbound`

---

## REMAINING LAUNCH BLOCKERS

1. **CRM + Outreach access for salespeople** — described above, ready to build
2. **Salesperson login issue** — Andrew's salesperson couldn't log in from their house. Andrew could log in with same credentials from incognito at his own house. Not debugged yet.
3. **Stripe end-to-end test** — configured but never tested full checkout flow
4. **500 error sweep** — full pass needed before launch

## REMAINING NON-BLOCKERS

- Sequence builder view (salespeople can't create sequences, only view)
- Conversion bridge (mark_won → create_customer → populate converted_business)
- Create booking pages / review campaigns for specific customers (salespeople can view but not create)
- conversations.py — not yet read, needs review

---

## KEY ARCHITECTURE NOTES

- CRM Contact/Activity/Appointment gated behind `BusinessProfile` — for paying customers
- Sales SalesProspect/SalesActivity gated behind `SalesPerson` — for internal sales team
- `SalesProspect.converted_business` FK exists but never populated
- Engagement tools accessible to both profiles (done this session)
- Campaigns already work for salespeople (is_staff checks) — just hidden in sidebar
- All CRM views use `_get_business()` → need `_is_sales_user()` fallback for cross-customer access

## DEPLOYMENT

Local PowerShell → git push → VPS git pull → migrate → collectstatic → restart

**VPS:** `/root/SalesSignalAI/`, port 8003, service: `salessignal`, superuser: `artursi`
**Local:** `C:\Users\aturs\Documents\AI_Engineering_Course\SalesSignalAI`
