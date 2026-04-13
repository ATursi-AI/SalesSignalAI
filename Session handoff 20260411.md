# SalesSignalAI Session Handoff — April 11, 2026

## Project Overview

**SalesSignalAI** (salessignalai.com) — B2B SaaS lead intelligence platform. Monitors 37+ public data sources to surface real-time leads for local service businesses and professional services. Automates multi-channel outreach via AI email campaigns, SMS, personalized video landing pages, and browser-based call center.

**Owner:** Andrew Tursi — serial entrepreneur, Lynbrook NY, also runs FastCredentials (sister site for backlinking)

**Tech Stack:**
- Python 3.12 / Django 5.x / SQLite / Gunicorn / Nginx
- Hostinger Ubuntu 24 VPS
- VPS path: `/root/SalesSignalAI/`
- Port: 8003
- Service name: `salessignal`
- Superuser: `artursi`
- Local path: `C:\Users\aturs\Documents\AI_Engineering_Course\SalesSignalAI`
- GitHub: `https://github.com/ATursi-AI/SalesSignalAI`

---

## Andrew's Working Rules (NON-NEGOTIABLE)

1. **Source first protocol** — Read all relevant files before writing any code. Never guess at filenames or structure.
2. **Complete replacement files only** — No partial edits
3. **Never touch `landing.html` or `base.html`** unless explicitly instructed
4. **Always provide push/pull commands** after every build
5. **Run `collectstatic` after static file changes**
6. **Restart service after Python changes**: `sudo systemctl restart salessignal`
7. **No money-back guarantee** — Never offered, remove all mentions
8. **"Starting at" required** before all prices
9. **Warn at 96% context** and provide handoff document

**Local PowerShell uses `;` not `&&`**

---

## What Was Built This Session

### 1. Data Source Registry (COMPLETED)

**Models** at `/root/SalesSignalAI/core/models/data_sources.py`:

- `DatasetRegistry` — Known good datasets ready for scraping
- `ScrapeRun` — Audit log per scrape job
- `DatasetCandidate` — Discovered datasets pending approval

**DatasetCandidate fields include:**
- Standard: name, portal_domain, dataset_id, api_url, state, city, data_type
- Detection: has_phone_field, has_email_field, has_name_field
- Analysis: contact_fields_found, all_fields, sample_data, relevance (HIGH/MEDIUM/LOW)
- Gemini: service_matches, recommended_filters, lead_value_signals, gemini_analyzed
- Status: new/approved/rejected

### 2. Generic SODA Scraper (COMPLETED)

**File:** `/root/SalesSignalAI/core/management/commands/scrape_registry.py`

```bash
python manage.py scrape_registry --dataset-id 43nn-pn8j --days 30
python manage.py scrape_registry --state NY --days 7
python manage.py scrape_registry --all --limit 500
```

### 3. Agent SCOUT — Dataset Discovery Agent (COMPLETED)

**Purpose:** Discovers datasets across open data platforms, analyzes for lead potential, creates DatasetCandidate records for human approval.

**Architecture:** Adapter pattern with pluggable platform support

**Files:**
```
core/services/scout_adapters/
├── __init__.py
├── base.py          — Abstract base class
├── socrata.py       — Socrata/SODA API adapter
├── arcgis.py        — ArcGIS REST Services adapter (built, needs portal URLs)
├── ckan.py          — CKAN API adapter (data.gov)
└── portals.py       — 23 portals mapped to adapters + states
```

**Command:** `/root/SalesSignalAI/core/management/commands/scout_datasets.py`

```bash
# Fast mode (no AI)
python manage.py scout_datasets --state NY --limit 5

# Smart mode with Gemini analysis
python manage.py scout_datasets --state NY --smart --limit 5

# Specific adapter
python manage.py scout_datasets --adapter ckan --limit 3

# Specific portal
python manage.py scout_datasets --portal data.sfgov.org --limit 5

# All states
python manage.py scout_datasets --all --limit 5
```

**Smart mode features:**
- Fetches 5 sample records per dataset
- Scans actual data values for phone patterns (not just column names)
- Calls Gemini to analyze:
  - Actual contact fields
  - Lead value signals (e.g., "permits without contractor = DIY homeowner")
  - Service matches (pest control, cleaning, contractors)
  - Recommended $where filters
  - HIGH/MEDIUM/LOW relevance with reasoning

**Portal Registry (23 portals):**
- 20 Socrata: NY, CA (SF, LA, Santa Clara), TX, IL (Chicago), FL, PA, OH, GA, NC, MI, WA, CO, AZ, NV, MD, CT + city portals
- 1 CKAN: catalog.data.gov (federal)
- ArcGIS: Stubs ready, need portal URLs researched

**Web UI:**
- `/tools/agent-scout/` — Trigger scout, view results, approve/reject
- Background subprocess spawning (no timeout issues)
- Polls status every 8 seconds
- Smart mode checkbox NOT yet added to UI

### 4. Agent SCOUT UI (COMPLETED)

**Files:**
- `/root/SalesSignalAI/core/views/agent_scout.py` — 4 views (tool, api, status, approve, reject)
- `/root/SalesSignalAI/core/templates/tools/agent_scout.html` — Polling UI
- Sidebar link in base.html (binoculars icon, cyan #0EA5E9, superuser only)

**URLs:**
```python
path('tools/agent-scout/', agent_scout.agent_scout_tool, name='agent_scout_tool'),
path('tools/agent-scout/api/', agent_scout.agent_scout_api, name='agent_scout_api'),
path('tools/agent-scout/status/', agent_scout.agent_scout_status, name='agent_scout_status'),
path('tools/agent-scout/approve/', agent_scout.agent_scout_approve, name='agent_scout_approve'),
path('tools/agent-scout/reject/', agent_scout.agent_scout_reject, name='agent_scout_reject'),
```

---

## Agent Team Status

| Agent | Icon | Color | Type | Status |
|-------|------|-------|------|--------|
| Monitor Health | bi-activity | default | Bot | ✅ Working |
| Video Pages | bi-camera-video-fill | default | Bot | ✅ Working |
| GEO Audit | bi-robot | default | Needs review | ✅ Working |
| Agent REP | bi-shield-exclamation | #EA580C orange | Needs review | ✅ Working |
| Agent SCOUT | bi-binoculars | #0EA5E9 cyan | Agent (Gemini) | ✅ Working |

**Note:** REP and GEO need code review to determine if they use AI reasoning (agents) or just fetch/calculate (bots).

---

## Key Concepts Established

### Bot vs Agent
- **Bot:** Fixed instructions, same behavior every time, no reasoning
- **Agent:** Has goals, adapts based on findings, reasons about results

Scout with `--smart` flag = Agent (uses Gemini reasoning)
Scout without flag = Bot (fixed patterns)

### Open Data Platforms

| Platform | API Style | Adapter Status |
|----------|-----------|----------------|
| Socrata/SODA | REST, SQL-like queries | ✅ Built |
| ArcGIS/Esri | REST FeatureServer | ✅ Built, needs portal URLs |
| CKAN | JSON REST | ✅ Built |
| OpenDataSoft | REST | Not built |
| Google Dataset Search | No API (SerpAPI $50/mo) | Not built |

### Scout's Job vs Monitor's Job
- **Scout:** Finds datasets → DatasetCandidate → you approve → DatasetRegistry
- **Monitor:** Scrapes leads from datasets in DatasetRegistry
- Different agents, different purposes

---

## Strategic Ideas Discussed

### 1. Platform Partnership Model
Sell to contractor software platforms (ServiceTitan, Housecall Pro, Jobber) as data layer:
- You provide API of leads
- They build "Lead Alerts" tab in their dashboard
- Revenue split per activation
- One deal = thousands of end users

### 2. Lead Value Intelligence
Not just "has phone field" but understanding data:
- Construction permits WITHOUT contractor = DIY homeowner = needs services
- Health violations with low scores = urgent need = higher value
- Recent dates = fresh leads

This is built into Scout's `--smart` mode.

---

## Pending / Next Steps

1. **Add --smart checkbox to Scout UI** — Currently only CLI has smart mode
2. **Research ArcGIS portal URLs** — County health departments, utilities
3. **Test smart mode thoroughly** — Verify Gemini analysis quality
4. **Clear old candidates and re-run** — Database has old LOW relevance items
5. **Add more Socrata portals** — As discovered

---

## Deployment Commands

**Local PowerShell:**
```powershell
cd C:\Users\aturs\Documents\AI_Engineering_Course\SalesSignalAI
git add .
git commit -m "message here"
git push origin main
```

**VPS SSH:**
```bash
cd /root/SalesSignalAI
git pull origin main
python manage.py makemigrations core
python manage.py migrate
sudo systemctl restart salessignal
```

**Test Scout:**
```bash
# Socrata
python manage.py scout_datasets --state NY --limit 3

# CKAN federal
python manage.py scout_datasets --adapter ckan --limit 3

# Smart mode with Gemini
python manage.py scout_datasets --state NY --smart --limit 3
```

---

## Current Migration State

Latest: `0047_merge_...` (merged 0046 conflict)

---

## File Reference

```
core/models/data_sources.py              — DatasetRegistry, ScrapeRun, DatasetCandidate
core/management/commands/scout_datasets.py — Scout command
core/management/commands/scrape_registry.py — Generic SODA scraper
core/services/scout_adapters/            — Adapter architecture
core/views/agent_scout.py                — Scout UI views
core/templates/tools/agent_scout.html    — Scout UI template
core/utils/monitors/lead_processor.py    — process_lead() function
scripts/seed_dataset_registry.py         — Seed 15 known datasets
```

---

## Session End State

- Scout adapter architecture deployed and tested
- Socrata + CKAN working
- ArcGIS adapter built but needs portal URLs
- Smart mode (Gemini) built but not tested this session
- All tests passing on VPS

**Last successful test output:** Scout found 12 new datasets via CKAN federal, 11 via SF Socrata portal.