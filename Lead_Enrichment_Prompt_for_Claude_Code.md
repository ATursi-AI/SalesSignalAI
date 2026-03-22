# Lead Enrichment System — BUILD THIS NOW

## CRITICAL INSTRUCTION
You MUST write actual code files and save them to disk. Do NOT just describe what you would build. Every file listed below must be created or modified and saved. Confirm each file was written by showing its path after saving.

## BEFORE WRITING ANY CODE
Read these files first and understand the existing patterns:

```
cat core/models/leads.py
ls core/templates/admin_leads/
cat core/views/admin_leads.py
cat core/utils/__init__.py
ls core/utils/
cat .env | grep GEMINI
```

Use the patterns you find (action handlers, template structure, URL routing, bulk actions) to ensure your code integrates cleanly.

## WHAT TO BUILD

### 1. Lead Model Migration
Add two fields to the Lead model in `core/models/leads.py`:

```python
enrichment_status = models.CharField(
    max_length=20,
    choices=[
        ('not_enriched', 'Not Enriched'),
        ('enriched', 'Enriched'),
        ('enrichment_failed', 'Failed'),
        ('manually_enriched', 'Manually Enriched'),
    ],
    default='not_enriched'
)
enrichment_date = models.DateTimeField(null=True, blank=True)
```

Then run: `python manage.py makemigrations core` and `python manage.py migrate`

### 2. Enrichment Service — NEW FILE: `core/utils/enrichment_service.py`

```python
# Gemini 2.0 Flash REST API integration
# Endpoint: https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}
# Read GEMINI_API_KEY from django.conf settings (which reads from .env)
```

**Logic:**
- Function: `enrich_lead(lead)` — takes a Lead instance, returns dict with results
- SKIP if lead already has a phone number (return early with message)
- Build a prompt using the lead's: `contact_name`, `contact_business`, `contact_address`, `source_type`, and any useful fields from `raw_data`
- Prompt should instruct Gemini to find: phone number, email, website, owner/manager name
- Prompt should say: "Search public records, business registries, licensing databases, property records, NPI databases, and business directories. Return ONLY a JSON object with keys: phone, email, website, owner_name. Use null for any field you cannot find. No explanation, just JSON."
- Parse the JSON response from Gemini
- Update the lead's contact fields (only overwrite if the field was previously empty)
- Store the full Gemini response in `lead.raw_data['enrichment']`
- Set `lead.enrichment_status = 'enriched'` (or `'enrichment_failed'` if no useful data found)
- Set `lead.enrichment_date = timezone.now()`
- Save the lead
- Return a result dict: `{'success': True/False, 'phone': ..., 'email': ..., 'website': ..., 'owner_name': ...}`

**Error handling:** Catch API errors, timeout (15 second timeout), JSON parse failures. Set status to `enrichment_failed` on any error.

### 3. API Endpoints — MODIFY: `core/views/admin_leads.py`

**Single lead enrichment:**
- Find the existing single-lead action handler (the view that handles actions like delete on individual leads)
- Add an `'enrich'` action that calls `enrich_lead(lead)` and returns JSON with the results

**Bulk enrichment:**
- Find the existing bulk action handler
- Add an `'enrich'` action that loops through selected leads, calls `enrich_lead()` on each, and returns JSON summary (enriched count, failed count, skipped count)

**Contact status filter:**
- Find the existing `_apply_filters()` function (or equivalent filter logic)
- Add a `contact_status` filter parameter:
  - `has_phone` → `contact_phone__isnull=False` and not empty
  - `needs_enrichment` → `enrichment_status='not_enriched'` AND phone is empty
  - `enriched` → `enrichment_status='enriched'`
  - `failed` → `enrichment_status='enrichment_failed'`

### 4. Template Changes — MODIFY all admin-leads templates that show lead cards/detail

**In the lead DETAIL panel (the side panel or detail view that shows when you click a lead):**
- Add a purple "Enrich" button in the action buttons area
- Style: `background: #7c3aed; color: white; border-radius: 6px; padding: 8px 16px;`
- Icon: magnifying glass or search icon (use an inline SVG or existing icon pattern in the templates)
- On click: POST to the single-lead action endpoint with `action: 'enrich'`
- While loading: show a spinner, disable the button
- On success: green toast showing what was found (e.g. "Found: (718) 555-1234 | owner@email.com")
- On partial: yellow toast "No additional contact info found"
- If lead already has phone: blue toast "Lead already has contact info"
- After success: refresh the detail panel to show updated contact info

**In the bulk action bar (appears when leads are selected via checkbox):**
- Add "Enrich Selected" button next to existing bulk actions (like Delete Selected)
- Same purple styling
- On click: POST to bulk action endpoint with `action: 'enrich'` and selected lead IDs
- Show progress: "Enriching 3 of 12..." updating in real-time
- Summary toast when done: "Enriched 8 leads — 5 found contacts, 2 no data, 1 failed"

**In the filter bar:**
- Add a "Contact" dropdown next to existing filter dropdowns
- Options: All, Has Phone, Needs Enrichment, Enriched, Failed
- Submit filter on change (match existing filter pattern)

### 5. Enrichment Status Badges — in lead cards/list view
- Show a small badge on each lead card indicating enrichment status:
  - `not_enriched` + no phone → gray "No Contact" badge  
  - `enriched` → green "Enriched" badge
  - `enrichment_failed` → red "Failed" badge
  - `manually_enriched` → blue "Manual" badge
  - Has phone (regardless of status) → no badge needed, the phone number itself is visible

## STYLE NOTES
- The platform uses a LIGHT theme with Exo 2 font
- Purple (#7c3aed) for enrichment-related UI elements
- Green for success, yellow for warnings, red for errors, blue for info
- Toast notifications should match existing toast pattern in the templates (look for how delete confirmations work)
- Keep all JavaScript inline in the templates (no separate JS files)

## AFTER WRITING ALL CODE
1. Run `python manage.py makemigrations core`
2. Run `python manage.py migrate`
3. List every file you created or modified with its full path
4. Do NOT just describe what you built — confirm the files are saved
