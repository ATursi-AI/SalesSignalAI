# SalesSignalAI — Project Status
**Last Updated:** April 12, 2026 by Claude
**Latest Migration:** 0047_merge_20260411_0019.py

---

## 🚨 CRITICAL RULES — NEVER VIOLATE

1. **Source first protocol** — Read files before editing, NEVER guess filenames
2. **Complete replacement files only** — No partial edits (exception: Andrew can do targeted block edits himself)
3. **NEVER touch `landing.html` or `base.html`** unless explicitly instructed
4. **"Starting at" required** before all prices in customer-facing copy
5. **No money-back guarantee** — Remove all mentions wherever found
6. **PowerShell uses `;` not `&&`** for command chaining
7. **Always run `collectstatic`** after static file changes
8. **Always run `sudo systemctl restart salessignal`** after Python changes
9. **Warn at 96% context** — Provide handoff markdown file
10. **Never show `.env` file contents**

---

## 💰 PRICING (Final)

| Tier | Price | Model |
|------|-------|-------|
| Starter | Starting at $149/mo | AI Only |
| Growth | Starting at $599/mo | AI + Data |
| Dominate | Starting at $899/mo | AI + Outreach |
| Closer | Starting at $2,499/mo | AI + Humans |
| Full Service | Starting at $4,999/mo | Your Sales Army |

**Setup:** $499 one-time (waived for annual commitment)

---

## 🏗️ ARCHITECTURE

### Tech Stack
- **Backend:** Django 5.x / Python 3.12 / SQLite
- **Server:** Gunicorn + Nginx on Hostinger Ubuntu 24 VPS
- **AI:** Gemini 2.5 Flash-Lite (intent classification), Claude Sonnet (agents)
- **Email:** SendGrid (DNS configured, integration pending)
- **SMS/Voice:** SignalWire (959-AISALES number, 10DLC pending for transactional)
- **Video:** HeyGen for personalized prospect videos
- **Payments:** Stripe (configured, end-to-end untested)

### Paths
- **VPS:** `/root/SalesSignalAI/`, port 8003, service: `salessignal`, superuser: `artursi`
- **Local:** `C:\Users\aturs\Documents\AI_Engineering_Course\SalesSignalAI`
- **GitHub:** `https://github.com/ATursi-AI/SalesSignalAI`

### Three Customer Types
1. **"Do it for me"** — Blue collar businesses, we run everything
2. **"Help me do it better"** — Agencies with white-label dashboard
3. **"Let me build my own"** — Power users configuring monitors/sequences

### Two Lead Flows
1. **Hot Social Leads** (Reddit, Nextdoor, Facebook) → `real_lead` classification → **Phone calls** → Sold at $150/each to up to 3 customers
2. **Google Places Prospects** → **Drip sequences** → Selling SalesSignalAI subscriptions

---

## 👁️ THREE DASHBOARDS

### 1. Customer Dashboard (`/dashboard/`)
**File:** `core/views/dashboard.py`
- Lead stats (hot, this week, total, won)
- Response rate, conversion rate, ROI metrics
- Public records breakdown by platform
- Competitor tracking summary
- Outreach activity (managed campaigns)
- Recent replies

### 2. Admin/Staff Dashboard (`/admin-leads/`)
**File:** `core/views/admin_leads.py`
| Page | URL | Status |
|------|-----|--------|
| Lead Repository (Command Center) | `/admin-leads/` | ✅ Working |
| Public Records | `/admin-leads/public-records/` | ✅ Working |
| Social Media | `/admin-leads/social-media/` | ✅ Working |
| Reviews | `/admin-leads/reviews/` | ✅ Working |
| Customer Accounts | `/admin-leads/customers/` | ✅ Working |
| Mission Control | `/admin-leads/mission-control/` | ✅ Working |

### 3. Sales Dashboard (`/sales/`)
**File:** `core/views/sales.py`
| Page | URL | Status |
|------|-----|--------|
| Pipeline (Kanban) | `/sales/pipeline/` | ✅ Working |
| Prospects | `/sales/prospects/` | ✅ Working |
| Today's Calls | `/sales/today/` | ✅ Working |
| Stats | `/sales/stats/` | ✅ Working |
| High-Value Leads | `/sales/high-value-leads/` | ✅ Working |

---

## 🔐 SIDEBAR PERMISSION STRUCTURE (Updated Apr 12)

| Section | Permission Gate |
|---------|-----------------|
| Main, CRM, Engagement, Outreach, Insights | `user.business_profile` |
| Sales | `user.salesperson_profile or is_superuser` |
| Leads + Sources + Customer Accounts + **Onboard Customer** + Mission Control + **Sales Tools** (GEO Audit, Agent REP) | `user.is_staff` |
| Sales Admin | `user.is_superuser` |
| Admin Tools (Monitor Health, Video Pages, Agent SCOUT) | `user.is_superuser` |

### To Create a Salesperson:
1. Django Admin → Users → Add User → check ☑️ **Staff status** only
2. Sales Admin → Manage Team → Add Salesperson → link to that user

---

## 🤖 AGENTS & BOTS — CORRECT CLASSIFICATION

| Name | Type | File | Why |
|------|------|------|-----|
| **Orchestrator** | ✅ AGENT | `core/agents/orchestrator.py` | Inherits `BaseAgent`, calls Claude, Think→Act→Observe loop, max 30 steps |
| **Discovery** | ✅ AGENT | `core/agents/discovery.py` | Inherits `BaseAgent`, calls Claude, uses search/fetch tools, max 25 steps |
| **Agent SCOUT** | ✅ AGENT | `core/services/scout_adapters/` | Gemini reasoning in `--smart` mode, discovers datasets |
| **GEO Audit** | ⚠️ HYBRID | `.claude/skills/geo-audit/` | Python script (bot) + Claude Code qualitative analysis (agent) |
| **Agent REP** | ❌ BOT | `core/views/agent_rep.py` | Zero LLM calls, regex scraping, hardcoded if/else |

### Agent Framework
- **Base:** `core/agents/base.py` — `BaseAgent` with Think→Act→Observe loop
- **Tools:** `core/agents/tools.py` — search_nyc_dob, search_data_gov, fetch_webpage, save_lead_to_repository, etc.
- **Registry:** `core/agents/registry.py` — `@register_agent` decorator
- **Trigger:** Telegram bot `@SalesSignalHQ_bot` or web UI at `/admin-leads/`

---

## 📡 MONITORS (95+ Total)

**File:** `core/utils/monitors/schedule.py`

### By Group
| Group | Count | Examples |
|-------|-------|----------|
| Public Records | 45+ | NYC DOB (5 boroughs), LA Building, SF Building, Austin, Dallas, Seattle, Chicago, Texas statewide, Montgomery County, Connecticut |
| Health Inspections | 11 | NYC, Chicago, Vegas, Phoenix, Tucson, Sacramento, San Diego, Santa Clara, LA County, SF, Austin |
| Social Media | 12 | Reddit (7 states), Nextdoor, Facebook Apify, Twitter Apify, Threads, TikTok |
| Reviews | 9 | Google, Yelp, BBB, Angi, Trustpilot, Porch, Thumbtack, Houzz, Google Q&A |
| Community | 9 | BiggerPockets, Alignable, Quora, Trade Forums, Parent Communities, City-Data, Patch, Craigslist, Local News |
| Google | 2+ | Google Places by category/city |

---

## ✅ WHAT'S LIVE & WORKING

- 95+ monitors (public records, health, social, reviews, community)
- Agent framework (Orchestrator, Discovery via Telegram)
- Lead Repository with REACH scoring and intent classification
- 2,257 SEO landing pages (trade/location combos)
- Full CRM (contacts, pipeline, appointments)
- Sales sequences (drip campaigns with call tasks)
- Engagement tools (voicemail drops, booking pages, review campaigns)
- SignalWire (calls, SMS, voicemail drops)
- Prospect video pages (HeyGen)
- Agent SCOUT (dataset discovery with Gemini)
- High-value leads page ($5K+ flagging)
- Customer Accounts page
- Mission Control (monitor health dashboard)
- **Salesperson sidebar** — Sales Tools section with GEO Audit & Agent REP
- **Onboard Customer link** — for sales-assisted account creation

---

## 🔨 IN PROGRESS

- **Stripe end-to-end testing** — Configured but untested
- **Data source registry** — Models done, seed script done, scraper pending
- **SendGrid integration** — DNS configured, code integration pending

---

## ⏸️ BLOCKED

- **SignalWire 10DLC** — Resubmitted for transactional/agent use only (original cold outreach rejected)
- **AWS SES production access** — SendGrid as fallback

---

## 📋 NOT STARTED

- Outlook OAuth for campaigns
- Sales portal access control middleware
- White-label dashboard (for agencies)
- Login redirect logic (role-based routing)

---

## 🐛 KNOWN BUGS

| Bug | Location | Priority |
|-----|----------|----------|
| LA County violations fetch returns 0 | `monitor_ca_health` | Low (inspections work) |
| San Diego monitor untested | `ca_health_inspections.py` | Medium |
| Pricing page wrong prices/tiers | `pricing.html` | HIGH — Fix before launch |
| Meta description says "$299/mo" | `pricing.html` | HIGH — Fix before launch |

---

## 📁 KEY FILES REFERENCE

### Models
| File | Models |
|------|--------|
| `core/models/leads.py` | Lead, LeadAssignment, AgentMission |
| `core/models/business.py` | ServiceCategory, BusinessProfile, UserKeyword |
| `core/models/sales.py` | SalesPerson, SalesProspect, SalesActivity, EmailTemplate, CallScript |
| `core/models/sales_sequences.py` | SalesSequence, SequenceStep, SequenceEnrollment, SequenceStepLog |
| `core/models/crm.py` | Contact, Activity, Appointment |
| `core/models/data_sources.py` | DatasetRegistry, ScrapeRun, DatasetCandidate |
| `core/models/engagement.py` | VoicemailDrop, BookingPage, ReviewCampaign |
| `core/models/outreach.py` | OutreachCampaign, OutreachProspect, GeneratedEmail |
| `core/models/prospect_videos.py` | ProspectVideo |

### Views
| File | Views |
|------|-------|
| `core/views/dashboard.py` | Customer dashboard |
| `core/views/admin_leads.py` | Lead repository, customer accounts, mission control, agent launch |
| `core/views/sales.py` | Pipeline, prospects, today's calls, stats, high-value leads |
| `core/views/signup.py` | Signup, billing, Stripe, `sales_create_customer` (line 351-467) |
| `core/views/agent_rep.py` | REP tool (bot) |
| `core/views/agent_scout.py` | SCOUT UI views |
| `core/views/geo_audit.py` | GEO audit view |

### Agents & Services
| File | Purpose |
|------|---------|
| `core/agents/base.py` | BaseAgent with Think→Act→Observe loop |
| `core/agents/orchestrator.py` | Orchestrator agent (Claude) |
| `core/agents/discovery.py` | Discovery agent (Claude) |
| `core/agents/tools.py` | Agent tool library |
| `core/services/signalwire_service.py` | SMS, calls, voicemail drops |
| `core/services/scout_adapters/` | Socrata, ArcGIS, CKAN adapters |
| `core/utils/reach/intent_classifier.py` | Gemini intent classification |
| `core/utils/reach/lead_value.py` | Dollar value extraction |
| `core/utils/monitors/schedule.py` | Monitor schedule (single source of truth) |

---

## 🔑 DECISIONS MADE

| Date | Decision |
|------|----------|
| Apr 12, 2026 | GEO Audit + Agent REP = Sales Tools, accessible to all staff |
| Apr 12, 2026 | Monitor Health, Video Pages, Agent SCOUT = Admin-only tools |
| Apr 12, 2026 | Workflows = Admin-only feature (automation rules) |
| Apr 12, 2026 | Use existing `sales_create_customer` view for salesperson onboarding |
| Apr 11, 2026 | GEO and REP are BOTS, not agents (no LLM reasoning) |
| Apr 11, 2026 | Orchestrator and Discovery are TRUE AGENTS (Claude-powered) |
| Apr 5, 2026 | Hot leads = phone calls at $150/each, not email |
| Apr 5, 2026 | Prospects = drip sequences selling subscriptions |
| Apr 1, 2026 | Pricing finalized at $149/$599/$899/$2499/$4999 |
| Mar 2026 | Telegram replaced SignalWire SMS as agent command interface |

---

## 🚀 DEPLOYMENT COMMANDS

### Local (PowerShell — NO && operator)
```powershell
cd C:\Users\aturs\Documents\AI_Engineering_Course\SalesSignalAI
git add .
git commit -m "message"
git push origin main
```

### VPS
```bash
cd /root/SalesSignalAI
git pull origin main
python manage.py makemigrations core
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart salessignal
```

---

## ⏭️ NEXT SESSION PRIORITIES

1. **Pricing page fix** — Wrong prices, wrong tiers, bad meta description
2. **500 error sweep** — Full pass before launch
3. **Stripe end-to-end test** — Complete checkout flow
4. **Homepage fixes** — Identified but not completed
5. **HeyGen prospect videos** — Send to Neil, Mike, Alex
6. **Landing.html period** — Remove period from "You Do The Work." if desired (never committed)

---

## 📝 SESSION PROTOCOL

### Start of Session
1. Claude reads `PROJECT_STATUS.md` from project folder
2. Andrew states what we're working on
3. Claude reads relevant files before writing code

### End of Session
1. Claude updates `PROJECT_STATUS.md` with changes made
2. Claude provides updated file for Andrew to save
3. If context > 96%, Claude provides handoff doc

---

*This document is the single source of truth. Update it at the end of every session.*