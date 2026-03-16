# Claude Code Prompt: SalesSignalAI Complete Visual Redesign

## Context

SalesSignalAI is a B2B SaaS platform at /root/SalesSignalAI/ (Django app). It sells real-time lead intelligence to local service businesses (plumbers, cleaners, electricians, contractors, etc.) nationwide. The platform monitors public records and social channels to find people who need services RIGHT NOW, then automates outreach and provides human phone follow-up.

The current site has a dark developer-looking theme. It needs to look like a premium $249/mo SaaS product. The visual target is Attio.com — clean, light, sophisticated, with strong typography and a pipeline/flow-based visual language.

## Brand Identity

**Tagline:** "Humans Backed By AI"

**Core message:** We find people who need your service today and get them on the phone with you tomorrow.

**What makes us different:**
1. We detect leads from 37+ sources (public records, social channels, review sites) — not just Google Maps like competitors
2. We automate personalized email outreach — table stakes but on top of a much bigger lead pool
3. Real humans pick up the phone and follow up — this is the moat. No AI company does this. "Most platforms stop at the email. We pick up the phone."

**Tone:** Confident, direct, results-focused. Not corporate. Not startup-cute. Think: experienced sales veteran who built technology to do what he's been doing for 20 years, but at scale.

**Color palette:** Light theme. White/off-white backgrounds. One strong accent color (suggest a confident blue or deep teal — NOT generic SaaS purple or gradient). Dark text for readability. Color-coded urgency badges (red/orange/blue) in the dashboard.

## Part 1: Landing Page Redesign

The landing page is at the root URL /. It's a Django template. Redesign it completely.

### Design Reference
Study Attio.com for visual inspiration — the clean typography, generous whitespace, subtle animations, and sophisticated feel. Don't copy it. Use it as a quality benchmark.

### Typography
Use distinctive, premium fonts. NOT Inter, Roboto, Arial, or system fonts. Load from Google Fonts. Suggest a strong display font for headings paired with a clean body font. The typography alone should signal "this is a serious product."

### Structure

**Hero Section:**
- "Humans Backed By AI" as the primary tagline — large, bold, memorable
- Below: "We find people who need your service today and get them on the phone with you tomorrow."
- One CTA button: "See It In Action" or "Get Started"
- NO stock images. Instead, a subtle animated element or a stylized dashboard preview that hints at the product without showing a full screenshot. Or a live-feeling data counter.
- Background: clean, light, with subtle depth — maybe a very faint grid or topographic pattern, not a flat white wall

**Pipeline Section — "How Your Next Customer Finds You":**
- Three connected stages shown as a horizontal flow (like a pipeline/kanban):
  - DETECT: "Our AI monitors public records, social channels, and review sites 24/7. When someone posts 'I need a plumber' or a building gets a violation, we catch it in minutes."
  - CONTACT: "Personalized email outreach reaches them before they start searching Google. AI writes it. You approve it. Or let it fly on autopilot."
  - CONNECT: "Our sales team follows up by phone. Real humans, real conversations. We book the appointment. You show up and close."
- This should be THE visual centerpiece of the page. Animate it subtly — a gentle flow from left to right, showing data moving through the pipeline.
- Under the CONNECT stage, a callout: "Most platforms stop at the email. We pick up the phone."

**Social Monitoring Section — The Mystery Angle:**
- Short section with compelling copy about the social monitoring without naming platforms:
- Something like: "Every day, thousands of people ask their neighbors 'does anyone know a good [your service]?' on community forums and social platforms. Our systems detect these requests across dozens of private channels in real-time. Which channels? That's proprietary — and it's why your competitors won't see these leads first."
- Keep this brief. One paragraph. Intriguing, not defensive.

**Public Records Section — "Leads Others Can't See":**
- Show the types of public data we monitor WITHOUT making it a boring bullet list
- Frame each as a result/scenario:
  - "A building owner in Queens just got a $10,000 violation. They must hire a contractor or the fines double. You had their name and address at 6am."
  - "A new restaurant just filed for a liquor license. They'll need cleaning, pest control, kitchen equipment, signage, insurance. You're in their inbox before they open the doors."
  - "A property just sold for $800,000. The new owner needs movers, painters, cleaners, landscapers, locksmiths. You're the first call they get."
- Maybe show these as cards that subtly animate in, each one a different scenario.

**Industries Section:**
- Show this works for multiple verticals: Commercial Cleaning, Plumbing, Electrical, HVAC, General Contracting, Pest Control, Landscaping, Roofing, Moving, and more.
- NOT a grid of icons with labels. Instead, maybe a horizontal scroll or a subtle animated ticker showing industry names. Or tabs that show a different scenario for each industry.

**Live Data Counter (optional but powerful):**
- "This week: 4,286 leads detected in New York. Expanding to California, New Jersey, and Florida."
- This can be a styled counter that feels alive. Even if the number is semi-static, it signals the platform is active and real.

**Pricing Section:**
- Four tiers framed around ROI, not features:
  - Signal ($99/mo): "Know who needs you." Real-time alerts from all sources.
  - Growth ($249/mo): "Reach them first." Alerts + AI email campaigns.
  - Dominate ($499/mo): "Own your market." + AI reply handling, multi-territory, Calendly integration.
  - Concierge ($1,200-2,000/mo): "We sell for you." + Human phone follow-up, appointment setting.
- Setup fee: $199-299 (mention it, don't hide it)
- ROI callout: "One new customer from our leads pays for 3-6 months of SalesSignalAI."
- Don't clutter with feature checklists. Keep each tier to 2-3 lines max.

**Final CTA Section:**
- Strong closing. "Your competitors are getting the call because they saw the lead first. Change that today."
- One button.

**Footer:**
- Clean, minimal. Company name, contact email, links to login/dashboard.

### Animations
- Subtle, purposeful. Staggered fade-ins as sections scroll into view.
- The pipeline section should have a gentle left-to-right flow animation.
- Don't overdo it. No parallax, no particle effects, no floating elements.
- Page load should feel smooth and fast, not heavy.

### Mobile
- Fully responsive. The pipeline section stacks vertically on mobile.
- Hero section should be just as impactful on a phone screen.

## Part 2: Dashboard Visual Overhaul

Apply the same light theme and design language to the entire authenticated app — all dashboard pages, the lead repository, the CRM, the sales pipeline.

### Global Changes
- Light background (white or very light gray, like #FAFBFC)
- Dark text (#1a1a2e or similar, NOT pure black)
- Same accent color as landing page
- Same typography as landing page
- Cards with subtle shadows (0 1px 3px rgba(0,0,0,0.08)) not hard borders
- Generous padding and whitespace
- Consistent 8px spacing grid

### Command Center (/admin-leads/)
- The sidebar should be clean with clear hierarchy, counts in subtle badges
- Urgency cards (HOT/WARM/COLD) should use color coding that's instantly scannable — red, amber, blue
- The lead feed should feel like a premium email inbox — clean rows, hover states, quick actions
- Each lead row shows: urgency badge (colored dot or pill), source icon, title, location, time ago, contact name if available
- Filter bar should be minimal — dropdowns that don't clutter the interface

### Pipeline/Kanban (/dashboard/pipeline/)
- This is the page the user said is their favorite. Make it exceptional.
- Clean columns with card-based layout
- Cards should have subtle depth (shadow on hover)
- Drag-and-drop should feel smooth
- Stage headers with counts
- Each card shows: business name, value, stage duration, next action
- Color accents on the left border of each card indicating status

### General Dashboard Elements
- Tables: clean, no heavy borders, alternating row tints (very subtle)
- Buttons: solid accent color for primary, ghost/outline for secondary
- Forms: clean inputs with focus states that use the accent color
- Navigation: top bar or sidebar, not both competing
- Loading states: subtle skeleton screens, not spinners
- Empty states: friendly message + CTA, not just "No data"

## Implementation Notes

- This is a Django app using templates. The CSS can be in a single stylesheet or modular per page.
- Use Google Fonts for typography — load only what's needed.
- CSS variables for the color palette so it's easy to tweak.
- Ensure fast load times — no massive JS libraries for simple animations. CSS transitions and @keyframes are preferred.
- Test on mobile. The landing page especially gets phone traffic.
- Keep existing URL structure and Django template blocks. Redesign the visual layer, don't restructure the app routing.
- The landing page should be fully self-contained — someone should be able to understand what SalesSignalAI does and why they should sign up without clicking any links.
