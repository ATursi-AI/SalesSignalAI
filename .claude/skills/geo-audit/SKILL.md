---
name: geo-audit
description: "GEO/AEO Audit — Generative Engine Optimization & Answer Engine Optimization audit for any website. Use this skill whenever the user mentions GEO, AEO, AI search optimization, generative engine optimization, answer engine optimization, AI visibility, LLM crawlability, llms.txt, AI-readiness audit, or wants to analyze how well a website performs in AI-powered search engines like ChatGPT, Perplexity, Claude, Gemini, or Copilot. Also trigger when the user asks about schema markup audits, structured data for AI, or wants to generate a GEO audit report for a client."
---

# GEO/AEO Audit Skill

Run a comprehensive Generative Engine Optimization (GEO) and Answer Engine Optimization (AEO) audit against any website URL. This produces a professional, client-ready report scoring how well a site is optimized for AI-powered search engines (ChatGPT, Perplexity, Claude, Gemini, Copilot, etc.).

## Why This Matters

Traditional SEO optimizes for Google's link-based results. GEO/AEO optimizes for the new paradigm: AI models that read, synthesize, and cite web content in conversational answers. Businesses that aren't optimized for AI search are invisible to a rapidly growing channel. This skill lets you audit any site and produce a sellable report.

## How To Run An Audit

### Step 1: Get the target URL

Ask the user for the website URL to audit. If they provide a business name instead, search for it and confirm the URL.

### Step 2: Run the automated audit script

Execute the Python audit script bundled with this skill:

```
python /path/to/geo-audit/scripts/geo_audit.py https://example.com
```

This script checks:
- **Crawlability** — robots.txt AI bot permissions, llms.txt presence, sitemap.xml
- **Schema Markup** — JSON-LD structured data (Organization, LocalBusiness, FAQPage, Article, Product, Service, BreadcrumbList, etc.)
- **Content Structure** — H1/H2/H3 hierarchy, FAQ sections, definition patterns
- **Meta Quality** — Title tags, meta descriptions, Open Graph, Twitter Cards
- **Technical Signals** — HTTPS, mobile viewport, canonical tags, page speed indicators

The script outputs a JSON results file. Read it after execution.

### Step 3: Manual Deep-Dive (Claude performs these checks)

After the automated scan, perform these additional qualitative checks by reading the page content:

1. **Entity Clarity** — Can an AI clearly identify what this business does, where it operates, and who runs it within the first 2 paragraphs? Is there an About page with founder/team info?

2. **Quotability** — Are there concise, authoritative statements an AI would naturally quote? (e.g., "We serve 500+ clients across 12 states" rather than vague marketing fluff)

3. **E-E-A-T Signals** — Experience, Expertise, Authoritativeness, Trustworthiness:
   - Author bylines with credentials?
   - Case studies or testimonials?
   - Industry certifications mentioned?
   - Physical address and contact info visible?

4. **FAQ Optimization** — Are common questions answered in a format AI can easily extract? Look for Q&A patterns, `<details>` elements, or dedicated FAQ sections.

5. **Citation Worthiness** — Does the content contain original data, statistics, or insights that AI would want to cite? Or is it generic content that could come from anywhere?

6. **AI Platform Presence** — Search for the business name on Perplexity and note whether AI already surfaces this business in relevant queries.

### Step 4: Calculate the GEO Score

Score each category on a 0-10 scale:

| Category | Weight | What It Measures |
|----------|--------|-----------------|
| AI Crawlability | 15% | robots.txt AI permissions, llms.txt, sitemap |
| Schema & Structured Data | 20% | JSON-LD richness, schema types, completeness |
| Content Structure | 15% | Heading hierarchy, FAQ formatting, readability |
| Entity Clarity | 15% | Can AI identify who/what/where in seconds |
| Quotability & Citations | 15% | Original data, quotable statements, authority |
| E-E-A-T Signals | 10% | Author info, credentials, trust markers |
| Technical Foundation | 10% | HTTPS, mobile, speed, canonical, OG tags |

**Overall GEO Score** = weighted average, reported as X/100.

**Score Interpretation:**
- 80-100: AI-Optimized — Site is well-positioned for AI search visibility
- 60-79: Partially Optimized — Good foundation but significant gaps
- 40-59: Needs Work — Missing key elements for AI discoverability
- 0-39: Not AI-Ready — Major overhaul needed

### Step 5: Generate the Report

Create a professional report (either .docx or .md based on user preference). Use this structure:

```
# GEO/AEO Audit Report
## [Business Name] — [URL]
## Audit Date: [Date]

### Executive Summary
[2-3 sentences: overall score, biggest strength, biggest gap]

### Overall GEO Score: [X]/100
[Visual score breakdown by category]

### Detailed Findings

#### 1. AI Crawlability ([X]/10)
[What was found, what's missing, why it matters]

#### 2. Schema & Structured Data ([X]/10)
[Schema types found, what's missing, recommendations]

#### 3. Content Structure ([X]/10)
[Heading analysis, FAQ presence, readability notes]

#### 4. Entity Clarity ([X]/10)
[How quickly AI can identify the business, gaps]

#### 5. Quotability & Citation Potential ([X]/10)
[Original content assessment, quotable statements found]

#### 6. E-E-A-T Signals ([X]/10)
[Trust markers found and missing]

#### 7. Technical Foundation ([X]/10)
[HTTPS, mobile, speed, meta tags assessment]

### Priority Recommendations
[Numbered list, highest-impact first, with estimated difficulty]

### Quick Wins (Do This Week)
[3-5 things they can implement immediately]

### Competitive Context
[How this score compares to typical sites in their industry]
```

### Step 6: Offer Next Steps

After delivering the report, suggest:
- "Would you like me to implement any of these recommendations?"
- "Want me to create the llms.txt and enhanced robots.txt for this site?"
- "Should I generate the JSON-LD schema markup they're missing?"
- "Want me to audit a competitor's site for comparison?"

## Important Notes

- Always use WebFetch to retrieve page content — never use curl/wget/requests from bash
- The Python audit script uses only `urllib` from stdlib for basic checks (robots.txt, headers) — it does NOT fetch full page content
- For full content analysis, use WebFetch + Claude's own reading ability
- Be honest in scoring — inflated scores destroy credibility
- Frame recommendations constructively, not as criticism
- The report should be professional enough to hand to a client or attach to a sales email
