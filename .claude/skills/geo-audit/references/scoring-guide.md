# GEO/AEO Scoring Reference Guide

## Industry Benchmarks (as of 2026)

Most small/local business websites score between 15-35 on the GEO scale. This is because GEO optimization is still nascent and almost no one outside tech-forward companies has implemented it. This means:

- A score of 40+ puts a business ahead of 80% of competitors
- A score of 60+ is genuinely impressive for a local business
- A score of 80+ typically only seen in SaaS companies and tech-forward brands

## Common Gaps by Business Type

### Local Service Businesses (plumbers, electricians, HVAC, etc.)
- Almost never have llms.txt (0% adoption)
- Rarely have robots.txt with AI bot rules (<5% adoption)
- Usually have basic LocalBusiness schema from their website builder
- FAQ pages exist but rarely use FAQPage schema
- Typical score range: 15-35

### Professional Services (lawyers, accountants, consultants)
- Slightly better schema adoption (~15%)
- Usually have About pages with credentials
- Blog content is common but generic
- Typical score range: 25-45

### E-commerce / SaaS
- Better technical foundation
- More likely to have Product/SoftwareApplication schema
- Still rarely have llms.txt or AI-specific robots.txt
- Typical score range: 30-55

### Enterprise / Tech Companies
- Best adoption of structured data
- More likely to have considered AI search
- May already have llms.txt
- Typical score range: 40-75

## What AI Search Engines Actually Look For

### ChatGPT (via Bing/GPTBot)
- Crawls via GPTBot user agent
- Respects robots.txt
- Prioritizes: clear entity descriptions, authoritative content, structured data
- Heavily weights: recent content, FAQ formatting, citations

### Perplexity
- Uses PerplexityBot
- Indexes and cites specific passages
- Prioritizes: quotable statements, data/statistics, clear expertise signals
- Heavily weights: unique insights, primary research, recent data

### Claude (Anthropic)
- Uses ClaudeBot / Claude-Web
- Checks llms.txt specifically
- Prioritizes: well-structured content, clear headings, honest/accurate info
- Heavily weights: E-E-A-T signals, content depth, original analysis

### Google AI Overviews (SGE)
- Uses existing Googlebot + Google-Extended
- Prioritizes: existing SEO signals + schema markup
- Heavily weights: FAQPage schema, HowTo schema, authoritative sources

## Recommendations Framework

When making recommendations, prioritize by:

1. **Impact** — How much will this improve AI visibility?
2. **Effort** — How hard is it to implement?
3. **Speed** — How quickly can it be done?

### Quick Wins (< 1 hour each)
- Add AI bot permissions to robots.txt
- Create llms.txt
- Add FAQPage schema to existing FAQ content
- Add Organization/LocalBusiness schema
- Update meta descriptions to be more "quotable"

### Medium Effort (1-5 hours each)
- Create comprehensive About page with founder story
- Add BreadcrumbList schema sitewide
- Create FAQ page with proper schema
- Add author bylines to blog posts with credentials
- Restructure headings for clarity

### High Effort (5+ hours each)
- Create original research/data content
- Build comprehensive service pages with schema
- Develop case studies with specific results
- Create video content with VideoObject schema
- Build topical authority through content clusters

## Sales Framing

When presenting GEO audits as a service, frame it around competitive advantage:

- "Your competitors are invisible to AI search — you can be the one ChatGPT recommends"
- "70% of consumers will use AI search by 2027 — is your business ready?"
- "Right now, when someone asks ChatGPT for a [plumber/electrician/etc.] in [city], your business doesn't come up. We can change that."

The GEO Score makes an excellent addition to sales proposals because:
1. It's quantifiable (prospects love numbers)
2. It reveals gaps they didn't know existed
3. The fix is actionable and sellable
4. Almost no one else is offering this yet
