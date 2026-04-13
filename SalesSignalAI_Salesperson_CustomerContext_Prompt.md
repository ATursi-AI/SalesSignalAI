# Claude Code Prompt: Salesperson Customer Context System

IMPORTANT: Do NOT ask for confirmation at any step. Read every file listed in the READ FIRST section before writing any code. Never guess at filenames, variables, or folder structures. If something is unclear, stop and ask. Do NOT touch `landing.html` or any file not explicitly listed. After Python changes, restart the service. After static/template changes, run collectstatic.

## Context

SalesSignalAI is at `/root/SalesSignalAI/`. Django 5.x, Python 3.12, SQLite, Gunicorn, Nginx. Port 8003, service name `salessignal`, superuser `artursi`.

**What SalesSignalAI does:** Full-service customer acquisition engine for service businesses (plumbers, electricians, HVAC, insurance agents, lawyers, etc.). AI monitors 95+ public data sources to find people who need services RIGHT NOW. Then a human sales team picks up the phone, runs email/video campaigns, and books appointments. The customer just shows up and does the work. Tagline: "You Do The Work. We Get You The Work."

**The problem we're solving:** Salespeople are the human backbone of this service — they work ON BEHALF of paying customers. They need to operate inside a customer's dashboard to manage their CRM, send campaigns, set appointments, and handle leads. Currently, the CRM and Outreach sections are gated behind `BusinessProfile` which salespeople don't have. Salespeople have a `SalesPerson` profile instead.

**The architecture:** A session-based "customer context" system. When a salesperson selects a customer, all CRM/Outreach/Engagement views use that customer's `BusinessProfile`. The salesperson and the customer see the same data — same contacts, same pipeline, same appointments. Either one can act on it.

---

## READ FIRST — Source-First Protocol

Read ALL of these files completely before writing any code:

1. `core/views/crm.py` — Understand every view function, how `_get_business()` works, what models are queried
2. `core/views/conversations.py` — Has NOT been reviewed yet. Read it, understand its access pattern
3. `core/views/campaigns.py` — Verify it uses `is_staff` checks (believed to already work for salespeople)
4. `core/views/workflows.py` — Read to understand access pattern
5. `core/views/engagement.py` — Already updated with `_is_sales_user()` pattern last session. Read to understand the existing pattern
6. `core/views/admin_leads.py` — Find the `lead_delete_all` view
7. `core/views/sales.py` — Read but DO NOT modify existing views. Only ADD new endpoints
8. `core/templates/base.html` — Full sidebar navigation, understand all conditional blocks
9. `core/context_processors.py` — Understand what's already injected into template context
10. `core/models/business.py` — Understand BusinessProfile model
11. `core/models/sales.py` — Understand SalesPerson model and its relationship to User
12. `core/models/crm.py` — Understand Contact, Activity, Appointment models

---

## DO NOT TOUCH

- `landing.html` — Protected, never edit
- `core/views/sales.py` — Do NOT modify existing views (sales_dashboard, pipeline, prospects, etc.). Only ADD the two new customer context endpoints at the bottom of the file
- Any model files — No model changes needed for this task
- `core/views/call_center.py` — Not part of this task
- Any migration files
- `.env` files — Never display or modify

---

## PHASE 1: Customer Context Session System

### 1A. Add two new views to `core/views/sales.py`

Add these at the BOTTOM of the file. Do not modify any existing views:

```python
@login_required
def set_customer_context(request):
    """
    POST: Set the active customer context for a salesperson.
    Stores the selected BusinessProfile ID in the session.
    Salespeople use this to "work as" a specific customer.
    """
```

Logic:
- Only allow if user has `salesperson_profile` or `is_superuser`
- Accept POST with `customer_id` (BusinessProfile pk)
- Validate the BusinessProfile exists and is active
- Store in `request.session['active_customer_id']`
- Redirect to the `next` param or `crm_pipeline`

```python
@login_required
def clear_customer_context(request):
    """
    POST or GET: Clear the active customer context.
    Returns salesperson to their own dashboard view.
    """
```

Logic:
- Remove `active_customer_id` from session
- Redirect to `sales_dashboard`

### 1B. Add URL routes to `core/urls.py`

Add in the Sales section (near the other `/sales/` routes):

```python
path('sales/set-customer/', sales.set_customer_context, name='set_customer_context'),
path('sales/clear-customer/', sales.clear_customer_context, name='clear_customer_context'),
```

### 1C. Update `core/context_processors.py`

Add `active_customer` to the template context so every template can access it:

```python
def active_customer_context(request):
    """Inject the salesperson's active customer context into all templates."""
    active_customer = None
    if hasattr(request, 'session') and request.user.is_authenticated:
        customer_id = request.session.get('active_customer_id')
        if customer_id:
            try:
                from core.models import BusinessProfile
                active_customer = BusinessProfile.objects.get(pk=customer_id)
            except BusinessProfile.DoesNotExist:
                # Customer was deleted, clear stale session
                del request.session['active_customer_id']
    return {'active_customer': active_customer}
```

Register this processor in `salessignal/settings/base.py` — add `'core.context_processors.active_customer_context'` to the `TEMPLATES[0]['OPTIONS']['context_processors']` list.

---

## PHASE 2: The `_get_effective_business()` Helper

This is the KEY function that replaces `_get_business()` for all CRM/Outreach views.

Add this to `core/views/crm.py` (keep the existing `_get_business` — other views may use it):

```python
def _get_effective_business(request):
    """
    Resolve the 'effective' BusinessProfile for the current request.
    
    Priority:
    1. If user has a BusinessProfile (they're a customer) → return it
    2. If user is a salesperson with an active customer context → return that customer's BP
    3. Otherwise → return None
    """
    # Customer's own profile
    bp = getattr(request.user, 'business_profile', None)
    if bp:
        return bp
    
    # Salesperson with active customer context
    customer_id = request.session.get('active_customer_id')
    if customer_id:
        try:
            return BusinessProfile.objects.get(pk=customer_id)
        except BusinessProfile.DoesNotExist:
            pass
    
    return None
```

Also add a helper to check sales user status:

```python
def _is_sales_user(request):
    """Check if the current user is a salesperson or admin."""
    return hasattr(request.user, 'salesperson_profile') or request.user.is_superuser
```

---

## PHASE 3: Update CRM Views

Update EVERY view in `core/views/crm.py` that currently calls `_get_business(request)`. Replace the pattern:

**BEFORE (current pattern):**
```python
bp = _get_business(request)
if not bp:
    return redirect('onboarding')
```

**AFTER (new pattern):**
```python
bp = _get_effective_business(request)
if not bp:
    if _is_sales_user(request):
        messages.warning(request, 'Select a customer first to access CRM tools.')
        return redirect('customer_accounts')
    return redirect('onboarding')
```

Apply this to ALL of these views (verify each exists by reading the file first):
- `pipeline()`
- `pipeline_move()`
- `contact_list()`
- `contact_detail()`
- `contact_add_note()`
- `contact_create()`
- `contact_update()`
- `inbox()`
- `appointment_list()`
- `appointment_create()`
- `appointment_update_status()`
- `competitor_dashboard()`
- `revenue_data()`

**IMPORTANT:** Every query that filters by `business=bp` stays exactly the same. The only change is HOW `bp` is resolved. The salesperson sees the exact same data the customer would see.

---

## PHASE 4: Update Conversations Views

Read `core/views/conversations.py` first. Apply the same `_get_effective_business()` pattern. Import the helper from crm.py:

```python
from core.views.crm import _get_effective_business, _is_sales_user
```

Or define locally if there are circular import issues. Apply to every view that currently gates behind `business_profile`.

---

## PHASE 5: Update Outreach Views (Sidebar Only + Verification)

### Campaigns (`core/views/campaigns.py`)
Read the file first. It's believed to already use `is_staff` checks. Verify this is true. If any view calls `_get_business()` and redirects to onboarding, apply the `_get_effective_business()` pattern. If it truly already works with `is_staff`, leave it alone — the only fix needed is the sidebar visibility (Phase 6).

### Workflows (`core/views/workflows.py`)
Read the file first. Apply the `_get_effective_business()` pattern if it gates behind `business_profile`.

---

## PHASE 6: Sidebar Transformation in `base.html`

This is the visual centerpiece. Make it stunning.

### 6A. Customer Context Switcher Banner

Add this INSIDE the sidebar, right after the `<nav class="sidebar-nav">` opening tag, BEFORE any `{% if %}` blocks. Only show for salespeople:

```html
{% if user.salesperson_profile or user.is_superuser %}
<!-- Customer Context Switcher -->
<div class="customer-context-switcher">
    {% if active_customer %}
    <div class="context-active">
        <div class="context-label">Working for</div>
        <div class="context-name">{{ active_customer.business_name|truncatechars:24 }}</div>
        <div class="context-tier">{{ active_customer.get_subscription_tier_display }}</div>
        <div class="context-actions">
            <a href="{% url 'clear_customer_context' %}" class="context-clear" title="Stop working for this customer">
                <i class="bi bi-x-circle"></i> Switch
            </a>
        </div>
    </div>
    {% else %}
    <a href="{% url 'customer_accounts' %}" class="context-empty">
        <i class="bi bi-building"></i>
        <span>Select a customer</span>
        <i class="bi bi-chevron-right" style="margin-left:auto;opacity:0.5;"></i>
    </a>
    {% endif %}
</div>
{% endif %}
```

### 6B. CRM Section Gate Change

Find the CRM section (currently around line 67). Change the gate:

**BEFORE:**
```html
{% if user.business_profile %}
<div class="sidebar-section-label">CRM</div>
```

**AFTER:**
```html
{% if user.business_profile or active_customer %}
<div class="sidebar-section-label">CRM</div>
```

Find the `{% endif %}` that closes this CRM block (currently around line 93) and make sure it closes properly. The CRM section should have its OWN `{% if %}...{% endif %}` pair, separate from the Main section above it.

### 6C. Outreach Section Gate Change

Find the Outreach section (currently around line 111). Change:

**BEFORE:**
```html
{% if user.business_profile %}
<div class="sidebar-section-label">Outreach</div>
```

**AFTER:**
```html
{% if user.business_profile or active_customer %}
<div class="sidebar-section-label">Outreach</div>
```

Make sure this section has its own `{% endif %}` that closes properly.

### 6D. Call Center Dashboard Access

Find the Call Center Dashboard link (currently around line 189, gated by `is_superuser`). Change:

**BEFORE:**
```html
{% if user.is_superuser %}
...
<a href="{% url 'call_center_dashboard' %}" ...>Call Center</a>
{% endif %}
```

**AFTER:**
```html
{% if user.is_superuser or user.salesperson_profile %}
...
<a href="{% url 'call_center_dashboard' %}" ...>Call Center</a>
{% endif %}
```

### 6E. CRITICAL — `{% endif %}` Audit

The base.html sidebar has nested `{% if %}` blocks. The Main section and CRM section are currently inside the SAME `{% if user.business_profile %}` block with one `{% endif %}` closing both. This MUST be split:

```html
{% if user.business_profile %}
<div class="sidebar-section-label">Main</div>
... Dashboard, Lead Feed, Territory Map ...
{% endif %}

{% if user.business_profile or active_customer %}
<div class="sidebar-section-label">CRM</div>
... Conversations, Pipeline, Contacts, Inbox, Appointments, Competitors ...
{% endif %}

{% if user.business_profile or active_customer %}
<div class="sidebar-section-label">Outreach</div>
... Compose Email, Campaigns, Workflows ...
{% endif %}
```

Each section must have its own independent `{% if %}`/`{% endif %}` pair.

---

## PHASE 7: Customer Context Switcher on Customer Accounts Page

The Customer Accounts page (`core/views/admin_leads.py` → `customer_accounts` view) already shows a list of all customers. Each customer row needs a "Work As" button that POSTs to `set_customer_context`.

Read `core/templates/admin_leads/customer_accounts.html` (or whatever template it uses). Add a button to each customer row:

```html
<form method="post" action="{% url 'set_customer_context' %}" style="display:inline;">
    {% csrf_token %}
    <input type="hidden" name="customer_id" value="{{ customer.id }}">
    <input type="hidden" name="next" value="{% url 'crm_pipeline' %}">
    <button type="submit" class="btn-work-as">
        <i class="bi bi-box-arrow-in-right"></i> Work As
    </button>
</form>
```

Style the "Work As" button to be visually prominent — teal accent, stands out from other actions.

---

## PHASE 8: Delete All Lead Protection

In `core/views/admin_leads.py`, find the `lead_delete_all` view. Add at the top:

```python
if not request.user.is_superuser:
    messages.error(request, 'Only administrators can delete all leads.')
    return redirect('admin_lead_repository')
```

In the template that shows the "Delete All" button (find it — likely in a Command Center template), wrap it:

```html
{% if user.is_superuser %}
<button ...>Delete All Leads</button>
{% endif %}
```

Salespeople keep individual delete and page-level bulk actions. Only the nuclear "delete all" button is restricted.

---

## PHASE 9: Visual Design — Customer Context Switcher CSS

Add these styles to `core/static/css/salessignal.css`. The customer context switcher should be VISUALLY STUNNING — this is the most important new UI element. It tells the salesperson "you are now operating inside this customer's world."

Design requirements:
- **When active (customer selected):** Vibrant gradient background (teal to emerald, matching the brand), white text, subtle glow effect, the customer name should be prominent. It should feel like a "mode" indicator — you've entered this customer's world.
- **When empty (no customer selected):** Subtle card with dashed border, muted text, inviting click. Feels like "pick a customer to get started."
- **Transition:** Smooth transition when switching between states.
- **Mobile:** Must look great on mobile sidebar.

```css
/* Customer Context Switcher */
.customer-context-switcher {
    margin: 0 12px 12px;
    border-radius: 12px;
    overflow: hidden;
}

.context-active {
    background: linear-gradient(135deg, #0D9488 0%, #065F46 100%);
    padding: 14px 16px;
    border-radius: 12px;
    position: relative;
    box-shadow: 0 4px 20px rgba(13, 148, 136, 0.3);
}

.context-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.7);
    margin-bottom: 4px;
}

.context-name {
    font-family: 'Exo 2', sans-serif;
    font-size: 1.05rem;
    font-weight: 700;
    color: #ffffff;
    line-height: 1.2;
}

.context-tier {
    font-size: 0.72rem;
    color: rgba(255,255,255,0.6);
    margin-top: 2px;
}

.context-actions {
    margin-top: 8px;
}

.context-clear {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    color: rgba(255,255,255,0.85);
    text-decoration: none;
    padding: 4px 10px;
    border-radius: 6px;
    background: rgba(255,255,255,0.15);
    transition: background 0.2s;
}

.context-clear:hover {
    background: rgba(255,255,255,0.25);
    color: #fff;
}

.context-empty {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 16px;
    border: 1.5px dashed var(--border-color, rgba(0,0,0,0.15));
    border-radius: 12px;
    color: var(--text-secondary, #6b7280);
    text-decoration: none;
    font-size: 0.85rem;
    font-weight: 500;
    transition: all 0.2s;
    background: transparent;
}

.context-empty:hover {
    border-color: #0D9488;
    color: #0D9488;
    background: rgba(13, 148, 136, 0.05);
}

.context-empty i:first-child {
    font-size: 1.1rem;
}
```

Verify these styles work with BOTH light and dark theme by checking the CSS variable names used in `salessignal.css`. Adjust variable references if needed (the existing theme uses `--text-secondary`, `--border-color`, etc. — match whatever the file actually uses).

### "Work As" Button Styles

```css
.btn-work-as {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 6px 14px;
    font-size: 0.78rem;
    font-weight: 600;
    color: #0D9488;
    background: rgba(13, 148, 136, 0.1);
    border: 1px solid rgba(13, 148, 136, 0.2);
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s;
}

.btn-work-as:hover {
    background: #0D9488;
    color: #ffffff;
    border-color: #0D9488;
    box-shadow: 0 2px 8px rgba(13, 148, 136, 0.3);
}
```

---

## PHASE 10: Topbar Customer Indicator

When a salesperson has an active customer context, also show the customer name in the topbar (the `<header class="topbar">` area). This reinforces which customer they're working for at all times.

In `base.html`, find the topbar-actions div. Add before the theme toggle button:

```html
{% if active_customer %}
<span class="topbar-customer-badge">
    <i class="bi bi-building"></i>
    {{ active_customer.business_name|truncatechars:20 }}
</span>
{% endif %}
```

Style:

```css
.topbar-customer-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 0.8rem;
    font-weight: 600;
    color: #ffffff;
    background: linear-gradient(135deg, #0D9488, #065F46);
    padding: 5px 12px;
    border-radius: 8px;
    margin-right: 8px;
    box-shadow: 0 2px 8px rgba(13, 148, 136, 0.25);
}
```

---

## Post-Build Checklist

After all changes:

1. Run `python manage.py collectstatic --noinput`
2. Run `sudo systemctl restart salessignal`
3. Test as superuser (artursi): Verify sidebar shows customer context switcher, all CRM/Outreach links appear when customer selected
4. Test navigation: Go to Customer Accounts → click "Work As" → verify redirect to CRM Pipeline → verify sidebar shows active customer → verify topbar shows customer name
5. Test CRM views: Pipeline, Contacts, Appointments should show the selected customer's data
6. Test clearing context: Click "Switch" → verify return to sales dashboard → verify CRM/Outreach sections disappear from sidebar
7. Test as regular customer: Verify nothing changed — their dashboard works exactly as before
8. Test delete all: Verify only superuser sees "Delete All" button in Command Center

---

## Files Modified Summary

| File | Action |
|------|--------|
| `core/views/sales.py` | ADD `set_customer_context` and `clear_customer_context` at bottom |
| `core/views/crm.py` | ADD `_get_effective_business()` and `_is_sales_user()`. UPDATE all views to use new pattern |
| `core/views/conversations.py` | READ FIRST. Apply `_get_effective_business()` pattern |
| `core/views/workflows.py` | READ FIRST. Apply pattern if gated by business_profile |
| `core/views/campaigns.py` | READ FIRST. Verify is_staff checks. Apply pattern only if needed |
| `core/views/admin_leads.py` | ADD superuser guard to `lead_delete_all` |
| `core/context_processors.py` | ADD `active_customer_context` processor |
| `core/urls.py` | ADD 2 routes for set/clear customer context |
| `core/templates/base.html` | ADD customer context switcher, UPDATE CRM/Outreach gates, ADD call center access, SPLIT if/endif blocks, ADD topbar badge |
| `core/templates/admin_leads/customer_accounts.html` | ADD "Work As" button per customer row (READ template name first) |
| `core/static/css/salessignal.css` | ADD customer context switcher styles, work-as button, topbar badge |
| `salessignal/settings/base.py` | ADD context processor to TEMPLATES config |
