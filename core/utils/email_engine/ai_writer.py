"""
AI email generation via Claude API.
Generates unique personalized outreach emails per prospect based on
their business info, Google rating, website content, and campaign context.
"""
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)


def generate_outreach_email(prospect, campaign, sequence_number=1):
    """
    Generate a personalized outreach email for a prospect.

    Args:
        prospect: ProspectBusiness instance
        campaign: OutreachCampaign instance
        sequence_number: 1=initial, 2+=follow-up

    Returns:
        dict with 'subject' and 'body', or None on failure
    """
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        logger.warning('ANTHROPIC_API_KEY not configured — using template fallback')
        return _template_fallback(prospect, campaign, sequence_number)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.warning('anthropic package not installed — using template fallback')
        return _template_fallback(prospect, campaign, sequence_number)

    prompt = _build_prompt(prospect, campaign, sequence_number)

    try:
        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}],
        )

        text = message.content[0].text.strip()
        result = _parse_ai_response(text, prospect, campaign)
        return _append_signature(result, campaign.business)

    except Exception as e:
        logger.error(f'AI email generation failed for {prospect.name}: {e}')
        return _template_fallback(prospect, campaign, sequence_number)


def _build_prompt(prospect, campaign, sequence_number):
    """Build the Claude prompt for email generation."""
    sender_biz = campaign.business

    prospect_info = f"""
Prospect Business: {prospect.name}
Category: {prospect.category}
Location: {prospect.city}, {prospect.state}
Google Rating: {prospect.google_rating or 'N/A'} ({prospect.google_review_count or 0} reviews)
Website: {prospect.website or 'N/A'}
Contact Name: {prospect.owner_name or 'Business Owner'}
"""

    sender_info = f"""
Sender Business: {sender_biz.business_name}
Owner: {sender_biz.owner_name}
Service: {sender_biz.service_category.name if sender_biz.service_category else 'Home Services'}
Location: {sender_biz.city}, {sender_biz.state}
"""

    if sequence_number == 1:
        instruction = (
            'Write a short, professional cold outreach email introducing our services. '
            'Be friendly, concise (under 150 words), and include a clear value proposition. '
            'Reference something specific about their business to show personalization. '
            'End with a soft call-to-action (quick call or reply).'
        )
    elif sequence_number == 2:
        instruction = (
            'Write a brief follow-up email (under 100 words). Reference the previous email. '
            'Add a new angle or benefit. Keep it casual and non-pushy. '
            'Ask a simple question to prompt a reply.'
        )
    else:
        instruction = (
            'Write a final follow-up (under 80 words). Be direct but respectful. '
            'Mention this is the last reach-out. Offer an easy way to connect if interested.'
        )

    template_hint = ''
    if campaign.email_subject_template:
        template_hint = f'\nSubject line style: {campaign.email_subject_template}'
    if campaign.email_body_template:
        template_hint += f'\nEmail tone/style reference: {campaign.email_body_template[:200]}'

    # Inject the business owner's email style guide
    style_guide = ''
    if sender_biz.email_style_guide:
        style_guide = f"""
IMPORTANT — Email Style Instructions from the sender:
{sender_biz.email_style_guide}
Follow these style instructions carefully when writing the email.
"""

    # If the sender has an email signature, instruct AI to exclude sign-off
    sig_instruction = ''
    if sender_biz.email_signature:
        sig_instruction = '\nDo NOT include a sign-off or signature in the email body — the sender\'s signature will be appended automatically.'

    return f"""{instruction}{sig_instruction}

{prospect_info}
{sender_info}
{template_hint}
{style_guide}
Return the email in this exact format:
SUBJECT: <subject line>
BODY:
<email body>

Do not include any other text outside this format."""


def _append_signature(result, business):
    """Append the business's email signature to the body if configured."""
    if result and business.email_signature:
        result['body'] = result['body'].rstrip() + '\n\n' + business.email_signature
    return result


def _parse_ai_response(text, prospect, campaign):
    """Parse subject and body from Claude's response."""
    subject_match = re.search(r'SUBJECT:\s*(.+?)(?:\n|$)', text)
    body_match = re.search(r'BODY:\s*\n(.*)', text, re.DOTALL)

    if subject_match and body_match:
        return {
            'subject': subject_match.group(1).strip(),
            'body': body_match.group(1).strip(),
        }

    # Fallback: try to split on first line
    lines = text.strip().split('\n', 1)
    if len(lines) == 2:
        return {
            'subject': lines[0].strip().replace('Subject:', '').strip(),
            'body': lines[1].strip(),
        }

    return _template_fallback(prospect, campaign, 1)


def _template_fallback(prospect, campaign, sequence_number):
    """Generate email from template when AI is unavailable."""
    sender = campaign.business
    contact = prospect.owner_name or 'there'
    service = sender.service_category.name if sender.service_category else 'services'

    # Default sign-off (used when no custom email_signature is set)
    default_signoff = f'Best,\n{sender.owner_name}\n{sender.business_name}'
    if sender.phone:
        default_signoff += f'\n{sender.phone}'

    if sequence_number == 1:
        subject = campaign.email_subject_template or f'{sender.business_name} — {service} for {prospect.city or "your area"}'
        body = (
            f'Hi {contact},\n\n'
            f'I\'m {sender.owner_name} from {sender.business_name}. '
            f'We specialize in {service} in the {sender.city} area '
            f'and I noticed your business, {prospect.name}, might benefit from '
            f'what we offer.\n\n'
        )
        if prospect.google_rating and prospect.google_rating >= 4.0:
            body += (
                f'Congrats on your {prospect.google_rating}-star Google rating — '
                f'it\'s clear you care about quality. '
            )
        body += (
            f'I\'d love to chat about how we could help. '
            f'Would you have 10 minutes this week for a quick call?'
        )
    elif sequence_number == 2:
        subject = f'Re: {campaign.email_subject_template or sender.business_name}'
        body = (
            f'Hi {contact},\n\n'
            f'Just wanted to follow up on my previous email. '
            f'We\'ve helped several businesses in {prospect.city or "your area"} '
            f'and I think there could be a great fit.\n\n'
            f'Would it make sense to connect this week?'
        )
    else:
        subject = f'Last note from {sender.business_name}'
        body = (
            f'Hi {contact},\n\n'
            f'I don\'t want to be a bother — this will be my last email. '
            f'If {service} is something you\'d like to explore, '
            f'feel free to reply anytime.\n\n'
            f'Wishing you and {prospect.name} all the best!'
        )

    # Append custom signature or default sign-off
    signoff = sender.email_signature if sender.email_signature else default_signoff
    body = body.rstrip() + '\n\n' + signoff

    return {'subject': subject, 'body': body}
