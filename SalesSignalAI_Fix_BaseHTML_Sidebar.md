# Claude Code Prompt: Fix base.html Sidebar — Customer Context System

IMPORTANT: Do NOT ask for confirmation at any step. This is a TEMPLATE-ONLY fix. Do not touch any Python files. Only edit `core/templates/base.html` and `core/static/css/salessignal.css`.

## Context

The backend customer context system is already built and working (views, context processor, routes). But the sidebar template in `base.html` was never updated. This prompt fixes ONLY the sidebar template.

The `active_customer` variable is already available in every template via the context processor. It contains the BusinessProfile of the customer the salesperson is currently "working for" — or None if no customer is selected.

## READ FIRST

Read `core/templates/base.html` completely before making any changes. Understand every `{% if %}` / `{% endif %}` block and which sections they control.

## DO NOT TOUCH

- `landing.html`
- Any Python files
- Any other template files
- The structure of any existing sidebar links — only change the `{% if %}` gates around them

---

## CHANGE 1: Add Customer Context Switcher

Insert this block INSIDE `<nav class="sidebar-nav">`, AFTER the opening tag and BEFORE the first `{% if user.business_profile %}`:

```html
{% if user.salesperson_profile or user.is_superuser %}
<div class="customer-context-switcher">
    {% if active_customer %}
    <div class="context-active">
        <div class="context-label">Working for</div>
        <div class="context-name">{{ active_customer.business_name|truncatechars:24 }}</div>
        <div class="context-tier">{{ active_customer.get_subscription_tier_display }}</div>
        <div class="context-actions">
            <a href="{% url 'clear_customer_context' %}" class="context-clear" title="Switch customer">
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

---

## CHANGE 2: Split Main and CRM into separate if/endif blocks

Currently the Main section and CRM section are BOTH inside one `{% if user.business_profile %}` ... `{% endif %}` block. Split them:

**The Main section stays as-is** (Dashboard, Lead Feed, Territory Map) — still gated by `{% if user.business_profile %}` with its own `{% endif %}` right after Territory Map.

**The CRM section gets its own gate:**

```html
{% if user.business_profile or active_customer %}
<div class="sidebar-section-label">CRM</div>
```

With its own `{% endif %}` after the Competitors link.

**CRITICAL:** Make absolutely sure the `{% endif %}` that currently closes both Main + CRM is replaced with TWO separate `{% endif %}` tags — one closing Main after Territory Map, one closing CRM after Competitors.

---

## CHANGE 3: Update Outreach gate

Find the Outreach section. Change:

```html
{% if user.business_profile %}
<div class="sidebar-section-label">Outreach</div>
```

To:

```html
{% if user.business_profile or active_customer %}
<div class="sidebar-section-label">Outreach</div>
```

The `{% endif %}` for this section stays where it is.

---

## CHANGE 4: Fix the Leads section — split Mission Control and restrict it

Currently the entire Leads section is gated by `{% if user.is_staff %}`. Inside it are:
- Command Center ✅ salespeople should see this
- Sources ✅ salespeople should see this  
- Customer Accounts ✅ salespeople should see this
- Onboard Customer ✅ salespeople should see this
- Mission Control ❌ should be superuser ONLY
- Sales Tools (GEO Audit, Agent REP) ✅ salespeople should see this

Wrap Mission Control in its own superuser check:

```html
{% if user.is_superuser %}
<a href="{% url 'mission_control' %}" class="sidebar-link {% if request.resolver_match.url_name == 'mission_control' %}active{% endif %}">
    <i class="bi bi-rocket-takeoff-fill"></i>
    <span>Mission Control</span>
</a>
{% endif %}
```

Do NOT change the outer `{% if user.is_staff %}` gate — everything else in the Leads section is correct for staff users.

---

## CHANGE 5: Call Center Dashboard access for salespeople

Find the Sales Admin section gated by `{% if user.is_superuser %}`. The Call Center Dashboard link is inside it. MOVE the Call Center Dashboard link OUT of the Sales Admin block and into the Sales section (after "My Calls", before the Sales section's `{% endif %}`):

```html
<a href="{% url 'my_calls' %}" class="sidebar-link {% if request.resolver_match.url_name == 'my_calls' %}active{% endif %}">
    <i class="bi bi-clock-history"></i>
    <span>My Calls</span>
</a>
<a href="{% url 'call_center_dashboard' %}" class="sidebar-link {% if request.resolver_match.url_name == 'call_center_dashboard' %}active{% endif %}">
    <i class="bi bi-headset"></i>
    <span>Call Center</span>
</a>
{% endif %}
```

Then REMOVE the Call Center link from the Sales Admin section (it will be a duplicate if left there).

---

## CHANGE 6: Topbar customer badge

Find the topbar section (`<header class="topbar">`). Inside the `<div class="topbar-actions">`, add this BEFORE the existing business name span:

```html
{% if active_customer %}
<span class="topbar-customer-badge">
    <i class="bi bi-building"></i>
    {{ active_customer.business_name|truncatechars:20 }}
</span>
{% endif %}
```

---

## CHANGE 7: CSS styles

Add these styles to `core/static/css/salessignal.css` at the end of the file:

```css
/* ── Customer Context Switcher ── */
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

/* Topbar customer badge */
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

## Post-Change

1. Run `python manage.py collectstatic --noinput` (for CSS changes)
2. Verify the template renders without errors by loading any dashboard page
