# Claude Code Prompt: Signup, Stripe Payments, Onboarding Wizard & Sales-Assisted Account Creation

## Context

SalesSignalAI at /root/SalesSignalAI/ needs a complete customer acquisition flow. Currently there is NO way for new customers to create accounts. We need two paths: self-service signup via the website, and sales-assisted signup where our reps create accounts on the phone. Both paths end with a paying customer in the system with a configured dashboard.

Support email: support@salessignalai.com

## Part 1: Stripe Integration

### Setup

Install stripe: `pip install stripe`

Environment variables (.env):
```
STRIPE_SECRET_KEY=sk_live_xxxx
STRIPE_PUBLISHABLE_KEY=pk_live_xxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxx
STRIPE_SETUP_FEE_PRICE_ID=price_xxxx
```

### Stripe Products & Prices

Create these products in Stripe (or via API during setup):

| Plan | Monthly Price | Setup Fee |
|------|-------------|-----------|
| Outreach | $149/mo | $299 one-time |
| Growth | $349/mo | $299 one-time |
| Dominate | $649/mo | $299 one-time |
| Concierge | Custom | Custom |
| Custom Outbound | Custom | Custom |

Each plan is a Stripe Product with a recurring Price. The setup fee is a separate one-time Price added to the first invoice.

### Stripe Webhook

Create endpoint at `/api/stripe/webhook/` to handle:
- `checkout.session.completed` — activate the customer account
- `invoice.paid` — mark subscription as active
- `invoice.payment_failed` — notify admin, pause account
- `customer.subscription.deleted` — deactivate account
- `customer.subscription.updated` — plan changes

### Customer Portal

Integrate Stripe Customer Portal so customers can:
- Update payment method
- View invoices
- Cancel subscription
- Change plan

Add a "Billing" link in the customer dashboard sidebar that redirects to Stripe Customer Portal.

## Part 2: Self-Service Signup

### Signup Page (`/signup/`)

Clean, minimal page matching the light theme. Two sections:

**Left side:** 
- "Start Growing Your Business Today"
- Brief bullet points: "37+ lead sources monitored 24/7" / "AI-powered email campaigns" / "Real humans on the phone"
- Trust signal: "Join hundreds of service businesses already using SalesSignal AI"

**Right side — the form:**
- Business name (required)
- Your name (required)
- Email (required) — becomes their login
- Phone (required)
- Password (required, min 8 chars)
- Confirm password
- "Create Account" button
- "Already have an account? Log in" link
- Small print: "By creating an account you agree to our Terms of Service and Privacy Policy"

**On submit:**
1. Create Django User (inactive until email verified)
2. Create BusinessProfile linked to user
3. Send verification email from support@salessignalai.com: "Verify your email to get started with SalesSignal AI" with a verification link
4. Show: "Check your email! We sent a verification link to {email}"
5. Verification link activates the account and redirects to `/onboarding/`

**Email verification:** Use Django's built-in token generation. Link format: `salessignalai.com/verify/{uid}/{token}/`

### Disable Public Registration

Remove any old open registration URLs. The ONLY way to create an account should be:
1. `/signup/` (this new page)
2. Sales-assisted creation (Part 4 below)
3. Django admin (superuser only)

## Part 3: Customer Onboarding Wizard (`/onboarding/`)

Mandatory for new self-service signups. If `BusinessProfile.is_onboarded = False`, ALL authenticated pages redirect to `/onboarding/`. No skipping. No accessing the dashboard until complete.

### Progress bar at top showing all 6 steps

### Step 1: Business Info
Pre-filled from signup form. Let them edit:
- Business name
- Owner first name, last name
- Phone
- Email
- Website (optional)
- Business type/trade (dropdown — same list as industry pages: Plumber, Electrician, HVAC, Commercial Cleaning, Roofing, General Contracting, Pest Control, Landscaping, Moving, Insurance, Mortgage, Dentist, Lawyer, Accountant, Locksmith, Painter, Handyman, Flooring, Fencing, Tree Service, Pool Service, Gutter, Window, Siding, Auto Mechanic, Tow Truck, Veterinarian, Chiropractor, Real Estate Agent, Other)
- Years in business
- Number of employees (dropdown: Just me, 2-5, 6-10, 11-25, 26-50, 50+)

**After completing Step 1, show:** "Great! Let's set up your territory so we can find leads in your area."

### Step 2: Service Area
- State (dropdown — all 50 states)
- Primary city/area
- Additional cities/areas served (multi-select or comma-separated)
- ZIP codes served (optional, comma-separated)
- Service radius: "How far will you travel for a job?" (dropdown: 5 miles, 10 miles, 25 miles, 50 miles, Statewide)

**After completing Step 2, show:** A map highlighting their territory with a message: "We're already scanning {X} data sources in your area."

### Step 3: About Your Business
- "How do you currently get customers?" (checkboxes: Word of mouth, Google Ads, Angi/HomeAdvisor, Thumbtack, Social media, Cold calling, Door-to-door, Referrals, Currently no marketing, Other)
- "Monthly marketing budget?" (dropdown: Not spending anything, Under $500, $500-1,000, $1,000-2,500, $2,500-5,000, $5,000+)
- "Biggest challenge in getting new customers?" (free text)
- "How many new customers do you want per month?" (dropdown: 5-10, 10-25, 25-50, 50+)
- "Describe your business in 2-3 sentences — we'll use this to personalize your outreach" (text area)

### Step 4: Choose Your Plan + Payment

Show the pricing tiers as cards (same design as homepage pricing):

**Outreach — $149/mo**
- AI email campaigns
- You provide leads or buy a list
- You handle replies

**Growth — $349/mo** (highlighted as MOST POPULAR)
- Everything in Outreach
- Full lead intelligence from 37+ sources
- CRM & sales pipeline
- Competitor monitoring

**Dominate — $649/mo**
- Everything in Growth
- AI reply handling
- SMS/text follow-up
- Calendly auto-booking
- Multi-territory

**Concierge — Contact Us**
- Show "Talk to our team" button → opens Calendly link or contact form

**Custom Outbound — Contact Us**
- Same as Concierge

When they select a tier (Outreach, Growth, or Dominate):
1. Show Stripe Checkout embedded or redirect to Stripe Checkout
2. Include the $299 setup fee as a line item on the first invoice
3. On successful payment → Stripe webhook fires → account activated
4. Redirect back to Step 5

For Concierge/Custom:
1. Show: "Our team will reach out within 24 hours to discuss your custom plan."
2. Send notification to admin
3. Skip payment for now, mark account as "pending_plan"
4. Continue to Step 5

### Step 5: Email Setup
Three options:

**Use SalesSignal Email (recommended for quick start)**
- "Emails sent from campaigns@salessignalai.com with your reply-to address"
- Just enter their reply-to email address
- Works immediately, no setup needed

**Connect Your Gmail**
- Google OAuth flow
- "Emails sent from your actual Gmail address — more legitimate, better deliverability"

**Connect Your Outlook**
- Microsoft OAuth flow
- "Emails sent from your Microsoft 365 account"

Also collect:
- Email signature (text area): "What should go at the bottom of your outreach emails?"
- Any custom instructions for AI: "Anything specific the AI should mention or avoid in emails?"

### Step 6: You're All Set!
- Confetti or subtle celebration animation
- "Your SalesSignal AI dashboard is ready!"
- Summary of what they set up: trade, territory, plan, email method
- "What happens next:"
  - "We're scanning {X} sources in your area right now"
  - "You'll see your first leads within 24 hours"
  - "Your first email campaign can launch today"
- Big "Go to Dashboard" button
- Set `BusinessProfile.is_onboarded = True`

## Part 4: Sales-Assisted Account Creation

For when your sales rep is on the phone with a prospect who wants to sign up NOW.

### Quick Create Form (`/sales/create-customer/`)

Staff-only page. Fast form designed to fill out in 60 seconds while talking:

**Customer Info:**
- Business name (required)
- Owner name (required)
- Email (required) — becomes their login
- Phone (required)
- Trade (dropdown)
- City
- State

**Plan:**
- Plan tier (dropdown: Outreach $149, Growth $349, Dominate $649, Concierge - Custom, Custom Outbound - Custom)

**Payment (choose one):**
- "Send Payment Link" — generates a Stripe Payment Link and sends it via email and SMS to the customer. They pay on their own time. Account stays in "pending_payment" until they pay.
- "Enter Card Now" — Stripe card input (use Stripe Elements) so the rep can take the card number over the phone.
- "Invoice Later" — for Concierge/Custom plans, skip payment, send invoice later.

**Email Setup:**
- Default to "SalesSignal Email" — customer can connect Gmail/Outlook later from their dashboard

**On submit:**
1. Create Django User with auto-generated temporary password
2. Create BusinessProfile with all info, set `is_onboarded = True` (rep collected info verbally, no wizard needed)
3. Set up their territory based on city/state
4. If "Send Payment Link" — create Stripe Checkout session, send link via email + SMS
5. If "Enter Card Now" — process payment immediately
6. Send welcome email to customer: "Welcome to SalesSignal AI! Here's your login: {email} / Temporary password: {temp_password}. Please change your password on first login: salessignalai.com/login/"
7. Show confirmation to rep: "Account created! Customer will receive login details at {email}"
8. Auto-convert the Sales CRM prospect to a paying customer if they exist in the pipeline

### Customer First Login After Sales-Assisted Signup

When a sales-assisted customer logs in for the first time:
- Don't show the full onboarding wizard (rep already collected the info)
- Show a simplified "Welcome" screen:
  - "Welcome to SalesSignal AI, {name}!"
  - "Your {trade} leads in {city} are already being scanned"
  - Quick optional steps: "Connect your Gmail for better email deliverability" / "Add your business description for AI-personalized emails" / "Add competitors to track"
  - "Skip for now → Go to Dashboard" option
- Prompt to change their temporary password

## Part 5: Login Page Redesign

### Login Page (`/login/`)

Clean page matching the light theme:
- SalesSignal AI logo
- "Welcome back"
- Email input
- Password input
- "Log In" button
- "Forgot password?" link
- "Don't have an account? Sign up" link
- Small SalesSignal branding at bottom

### Password Reset
Use Django's built-in password reset:
- `/password-reset/` — enter email
- Email sent from support@salessignalai.com with reset link
- `/password-reset/confirm/{uid}/{token}/` — enter new password
- Redirect to login after reset

### All auth emails come from: support@salessignalai.com

## Part 6: Account Management

### Customer Dashboard — Billing Section

Add to customer dashboard sidebar: "Billing" link

Billing page shows:
- Current plan name and price
- Next billing date
- "Manage Billing" button → Stripe Customer Portal
- "Upgrade Plan" button → shows plan comparison, upgrade via Stripe
- Payment history (last 6 invoices from Stripe)

### Account Settings

Add to customer dashboard sidebar: "Settings" link

Settings page:
- Business info (edit name, phone, website, description)
- Service area (edit territory)
- Email setup (change between SalesSignal Email / Gmail / Outlook)
- Password change
- Notification preferences
- "Delete Account" (with confirmation — cancels Stripe subscription, deactivates account)

## Part 7: Homepage + Navigation Updates

### Update "Get Started" / "See It In Action" buttons
All CTA buttons across the site should link to `/signup/`:
- Homepage hero "See It In Action" → `/signup/`
- Homepage pricing "Get Started" buttons → `/signup/?plan=outreach` (pre-select plan)
- Industry page CTAs → `/signup/`
- "Ready To Never Miss A Sale Again?" CTA → `/signup/`

### Update navigation
- Add "Sign Up" button in main nav (next to "Log In")
- If user is logged in, show "Dashboard" instead of "Sign Up" / "Log In"

### Footer
- Add "Sign Up" and "Log In" links
- Add "Support: support@salessignalai.com"

## Design Notes

- ALL new pages use the light theme with Exo 2 headings
- The onboarding wizard should feel smooth and modern — one step per page, clean transitions
- The Stripe Checkout should be embedded (Stripe Elements) not a redirect — keeps the user on your site
- Mobile responsive — the signup and onboarding flow must work perfectly on phones
- Loading states on all form submissions — don't let users double-click
- Error handling with clear messages — "This email is already registered" etc.

## Security

- Email verification required before account access
- Rate limit signup form (prevent bot registrations)
- CSRF protection on all forms
- Stripe webhook signature verification
- Temporary passwords are random 12-character strings
- Force password change on first login for sales-assisted accounts
- All payment data handled by Stripe — never store card numbers
