"""
Multi-model AI email generation engine for SalesSignal outreach campaigns.

Strategy:
  - Prospect enrichment: Gemini 2.5 Flash-Lite (free tier, fast)
  - Email 1 (personalized intro): DeepSeek V3.2 (best value for creative writing)
  - Email 2 (follow-up, day 3): Gemini Flash-Lite (cheaper, simpler)
  - Email 3 (final touch, day 7): Gemini Flash-Lite
  - Reply classification: Gemini Flash-Lite
  - Draft reply to interested: DeepSeek V3.2

Environment:
  GEMINI_API_KEY — Google AI Studio key for Gemini models
  DEEPSEEK_API_KEY — DeepSeek API key
"""
import json
import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# AI model callers
# ---------------------------------------------------------------

def _call_gemini(prompt, max_tokens=1024):
    """
    Call Gemini 2.5 Flash-Lite via Google AI Studio REST API.
    Returns (text, model_name) or (None, model_name).
    """
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model_name = 'gemini-2.5-flash-lite'
    if not api_key:
        logger.warning('[AI] GEMINI_API_KEY not configured')
        return None, model_name

    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent'
    headers = {'Content-Type': 'application/json'}
    params = {'key': api_key}
    body = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {
            'maxOutputTokens': max_tokens,
            'temperature': 0.7,
        },
    }

    try:
        resp = requests.post(url, json=body, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            logger.error(f'[Gemini] API error {resp.status_code}: {resp.text[:300]}')
            return None, model_name

        data = resp.json()
        candidates = data.get('candidates', [])
        if candidates:
            parts = candidates[0].get('content', {}).get('parts', [])
            if parts:
                return parts[0].get('text', ''), model_name

        logger.warning('[Gemini] No content in response')
        return None, model_name

    except requests.RequestException as e:
        logger.error(f'[Gemini] Request failed: {e}')
        return None, model_name


def _call_deepseek(prompt, max_tokens=1024):
    """
    Call DeepSeek V3.2 via OpenAI-compatible API.
    Returns (text, model_name) or (None, model_name).
    """
    api_key = getattr(settings, 'DEEPSEEK_API_KEY', '')
    model_name = 'deepseek-chat'
    if not api_key:
        logger.warning('[AI] DEEPSEEK_API_KEY not configured')
        return None, model_name

    url = 'https://api.deepseek.com/v1/chat/completions'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    body = {
        'model': model_name,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'temperature': 0.7,
    }

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.error(f'[DeepSeek] API error {resp.status_code}: {resp.text[:300]}')
            return None, model_name

        data = resp.json()
        choices = data.get('choices', [])
        if choices:
            return choices[0].get('message', {}).get('content', ''), model_name

        logger.warning('[DeepSeek] No choices in response')
        return None, model_name

    except requests.RequestException as e:
        logger.error(f'[DeepSeek] Request failed: {e}')
        return None, model_name


# ---------------------------------------------------------------
# Prospect Enrichment (Gemini Flash-Lite)
# ---------------------------------------------------------------

def enrich_prospect(prospect):
    """
    Scrape prospect's website and extract business intelligence using Gemini.

    Args:
        prospect: OutreachProspect instance

    Returns:
        dict with enrichment data, or empty dict on failure
    """
    website = prospect.website_url
    if not website:
        return {}

    # Step 1: Scrape website text
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(website, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; SalesSignalBot/1.0)',
        })
        if resp.status_code != 200:
            logger.warning(f'[Enrich] Website returned {resp.status_code}: {website}')
            return {}

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Remove script/style tags
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()

        text = soup.get_text(separator=' ', strip=True)
        # Truncate to ~3000 chars for API
        text = text[:3000]

    except ImportError:
        logger.warning('[Enrich] beautifulsoup4 not installed')
        return {}
    except requests.RequestException as e:
        logger.warning(f'[Enrich] Failed to fetch {website}: {e}')
        return {}

    if len(text) < 50:
        return {}

    # Step 2: Send to Gemini for extraction
    prompt = f"""Extract the following from this business website text and return as JSON:
- business_name: the business name
- owner_name: owner or manager name (if mentioned)
- services: list of services offered
- years_in_business: number (if mentioned, else null)
- unique_selling_points: list of things that make them stand out
- location: city/state if mentioned
- team_size: approximate (if mentioned, else null)

Website text:
{text}

Return ONLY valid JSON, no other text."""

    result_text, model = _call_gemini(prompt, max_tokens=512)
    if not result_text:
        return {}

    try:
        # Clean potential markdown code blocks
        cleaned = result_text.strip()
        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```(?:json)?\n?', '', cleaned)
            cleaned = re.sub(r'\n?```$', '', cleaned)

        data = json.loads(cleaned)
        data['_model_used'] = model
        data['_source_url'] = website
        logger.info(f'[Enrich] Enriched {prospect.business_name}: {list(data.keys())}')
        return data

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f'[Enrich] Failed to parse Gemini response: {e}')
        return {}


# ---------------------------------------------------------------
# Email Generation
# ---------------------------------------------------------------

def _build_email_prompt(prospect, campaign, sequence_number):
    """Build the prompt for email generation."""
    sender = campaign.business
    enrichment = prospect.enrichment_data or {}

    # Prospect info
    prospect_block = f"""PROSPECT:
- Business: {prospect.business_name}
- Contact: {prospect.contact_name or 'Business Owner'}
- Phone: {prospect.contact_phone or 'N/A'}
- Website: {prospect.website_url or 'None'}
- Location: {enrichment.get('location', 'N/A')}"""

    if enrichment.get('services'):
        prospect_block += f"\n- Services: {', '.join(enrichment['services'][:5])}"
    if enrichment.get('years_in_business'):
        prospect_block += f"\n- Years in business: {enrichment['years_in_business']}"
    if enrichment.get('unique_selling_points'):
        prospect_block += f"\n- What makes them special: {', '.join(enrichment['unique_selling_points'][:3])}"
    if enrichment.get('owner_name'):
        prospect_block += f"\n- Owner/manager: {enrichment['owner_name']}"

    # Sender info
    sender_block = f"""SENDER (your client):
- Business: {sender.business_name}
- Owner: {sender.owner_name}
- Service: {sender.service_category.name if sender.service_category else 'Business Services'}
- Location: {sender.city}, {sender.state}
- Phone: {sender.phone or 'N/A'}"""

    # Style guide
    style = campaign.email_style
    style_map = {
        'professional': 'Professional and polished. Use proper grammar, formal but warm tone.',
        'friendly': 'Friendly and conversational. Like a neighbor who happens to run a business. Use contractions.',
        'direct': 'Direct and no-nonsense. Get to the point quickly. Value their time.',
    }
    style_instruction = style_map.get(style, style_map['professional'])

    # Custom instructions from customer
    custom = ''
    if campaign.customer_custom_instructions:
        custom = f"\nCUSTOMER INSTRUCTIONS (follow these):\n{campaign.customer_custom_instructions}"

    # Email style guide from business profile
    biz_guide = ''
    if sender.email_style_guide:
        biz_guide = f"\nBUSINESS EMAIL STYLE GUIDE:\n{sender.email_style_guide}"

    # Signature handling
    sig_note = ''
    if sender.email_signature:
        sig_note = '\nDo NOT include a sign-off or signature — it will be appended automatically.'

    # Sequence-specific instructions
    if sequence_number == 1:
        seq_instruction = """Write a personalized cold outreach email (Email 1 — Introduction).
- Under 150 words
- Reference something SPECIFIC about their business (from the prospect info above)
- Clear value proposition: what the sender can do for them
- Soft call-to-action (quick call or reply)
- Must feel personal, not templated"""

    elif sequence_number == 2:
        seq_instruction = """Write a follow-up email (Email 2 — Day 3 follow-up).
- Under 100 words
- Reference the previous email briefly ("I reached out earlier this week...")
- Add a NEW angle or benefit not mentioned in Email 1
- Ask a simple question to prompt a reply
- Keep it casual and non-pushy"""

    else:
        seq_instruction = """Write a final follow-up email (Email 3 — Day 7 last touch).
- Under 80 words
- Acknowledge they're busy
- Soft close: "If you ever need [service]..."
- Make it easy to reply later
- No pressure, leave door open"""

    return f"""{seq_instruction}

STYLE: {style_instruction}{sig_note}
{custom}
{biz_guide}

{prospect_block}

{sender_block}

Return the email in this exact format:
SUBJECT: <subject line>
BODY:
<email body>

Do not include any other text outside this format."""


def generate_email(prospect, campaign, sequence_number):
    """
    Generate a single email for a prospect.

    Email 1 uses DeepSeek V3.2 (better personalization).
    Email 2 & 3 use Gemini Flash-Lite (cheaper, simpler follow-ups).

    Args:
        prospect: OutreachProspect instance
        campaign: OutreachCampaign instance
        sequence_number: 1, 2, or 3

    Returns:
        dict with 'subject', 'body', 'model_used' or None on failure
    """
    prompt = _build_email_prompt(prospect, campaign, sequence_number)

    # Email 1: DeepSeek for best personalization
    # Email 2, 3: Gemini for cost efficiency
    if sequence_number == 1:
        text, model = _call_deepseek(prompt, max_tokens=600)
        if not text:
            # Fallback to Gemini
            text, model = _call_gemini(prompt, max_tokens=600)
    else:
        text, model = _call_gemini(prompt, max_tokens=400)
        if not text:
            # Fallback to DeepSeek
            text, model = _call_deepseek(prompt, max_tokens=400)

    if not text:
        logger.warning(f'[AI] All models failed for {prospect.business_name} seq#{sequence_number}')
        return _template_fallback(prospect, campaign, sequence_number)

    result = _parse_email_response(text)
    if not result:
        return _template_fallback(prospect, campaign, sequence_number)

    # Append signature
    sender = campaign.business
    if sender.email_signature:
        result['body'] = result['body'].rstrip() + '\n\n' + sender.email_signature

    result['model_used'] = model
    return result


def _parse_email_response(text):
    """Parse SUBJECT/BODY format from AI response."""
    subject_match = re.search(r'SUBJECT:\s*(.+?)(?:\n|$)', text)
    body_match = re.search(r'BODY:\s*\n(.*)', text, re.DOTALL)

    if subject_match and body_match:
        return {
            'subject': subject_match.group(1).strip(),
            'body': body_match.group(1).strip(),
        }

    # Fallback: first line as subject, rest as body
    lines = text.strip().split('\n', 1)
    if len(lines) == 2:
        return {
            'subject': lines[0].strip().replace('Subject:', '').strip(),
            'body': lines[1].strip(),
        }

    return None


def _template_fallback(prospect, campaign, sequence_number):
    """Generate email from template when all AI models fail."""
    sender = campaign.business
    contact = prospect.contact_name or 'there'
    service = sender.service_category.name if sender.service_category else 'services'

    default_signoff = f'Best,\n{sender.owner_name}\n{sender.business_name}'
    if sender.phone:
        default_signoff += f'\n{sender.phone}'

    if sequence_number == 1:
        subject = f'{sender.business_name} — {service} for your area'
        body = (
            f'Hi {contact},\n\n'
            f"I'm {sender.owner_name} from {sender.business_name}. "
            f'We specialize in {service} in the {sender.city} area '
            f'and I noticed your business, {prospect.business_name}, might benefit from '
            f'what we offer.\n\n'
            f"I'd love to chat about how we could help. "
            f'Would you have 10 minutes this week for a quick call?'
        )
    elif sequence_number == 2:
        subject = f'Re: {sender.business_name}'
        body = (
            f'Hi {contact},\n\n'
            f'Just wanted to follow up on my previous email. '
            f"We've helped several businesses in your area "
            f'and I think there could be a great fit.\n\n'
            f'Would it make sense to connect this week?'
        )
    else:
        subject = f'Last note from {sender.business_name}'
        body = (
            f'Hi {contact},\n\n'
            f"I don't want to be a bother — this will be my last email. "
            f'If {service} is something you\'d like to explore, '
            f'feel free to reply anytime.\n\n'
            f'Wishing you and {prospect.business_name} all the best!'
        )

    signoff = sender.email_signature if sender.email_signature else default_signoff
    body = body.rstrip() + '\n\n' + signoff

    return {'subject': subject, 'body': body, 'model_used': 'template_fallback'}


# ---------------------------------------------------------------
# Reply Classification (Gemini Flash-Lite)
# ---------------------------------------------------------------

def classify_reply(reply_text):
    """
    Classify an inbound reply using Gemini Flash-Lite.

    Returns one of: 'interested', 'not_interested', 'question', 'out_of_office'
    """
    if not reply_text or len(reply_text.strip()) < 5:
        return 'not_interested'

    prompt = f"""Classify this email reply into exactly one category:
- interested: they want to learn more, schedule a call, or discuss further
- not_interested: they decline, say no thanks, or ask to stop emailing
- question: they have a question but haven't committed either way
- out_of_office: automated out-of-office or vacation reply

Reply text:
"{reply_text[:500]}"

Return ONLY the category name, nothing else."""

    text, _ = _call_gemini(prompt, max_tokens=20)
    if text:
        classification = text.strip().lower().replace('"', '').replace("'", '')
        valid = ['interested', 'not_interested', 'question', 'out_of_office']
        if classification in valid:
            return classification

    # Simple keyword fallback
    lower = reply_text.lower()
    if any(w in lower for w in ['unsubscribe', 'stop', 'remove', 'not interested', 'no thanks']):
        return 'not_interested'
    if any(w in lower for w in ['out of office', 'on vacation', 'auto-reply', 'automatic reply']):
        return 'out_of_office'
    if any(w in lower for w in ['interested', 'tell me more', 'schedule', 'call', 'yes', 'sounds good']):
        return 'interested'

    return 'question'


def generate_reply_draft(prospect, campaign, reply_text):
    """
    Generate a draft reply to an interested prospect using DeepSeek V3.2.
    Tries to schedule a call or meeting.

    Returns dict with 'subject', 'body', 'model_used' or None.
    """
    sender = campaign.business
    service = sender.service_category.name if sender.service_category else 'services'

    prompt = f"""Write a reply to this interested prospect who responded to our outreach.
Goal: Schedule a call or meeting.

THEIR REPLY:
"{reply_text[:500]}"

PROSPECT: {prospect.business_name} ({prospect.contact_name or 'Business Owner'})
OUR BUSINESS: {sender.business_name} ({sender.owner_name}), {service}

Write a warm, enthusiastic reply that:
- Thanks them for getting back
- Addresses any question they asked
- Proposes 2-3 specific time slots for a call (use phrases like "this week" or "next Monday/Tuesday")
- Keeps it under 100 words

Return in this format:
SUBJECT: <subject line>
BODY:
<reply body>"""

    text, model = _call_deepseek(prompt, max_tokens=400)
    if not text:
        text, model = _call_gemini(prompt, max_tokens=400)

    if not text:
        return None

    result = _parse_email_response(text)
    if result:
        if sender.email_signature:
            result['body'] = result['body'].rstrip() + '\n\n' + sender.email_signature
        result['model_used'] = model
    return result
