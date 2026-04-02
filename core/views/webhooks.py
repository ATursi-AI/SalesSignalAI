"""
Webhook endpoints for SendGrid events (bounces, complaints, unsubscribes)
and a public unsubscribe page for CAN-SPAM compliance.
"""
import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from core.utils.email_engine.sender import handle_bounce, handle_complaint, add_unsubscribe

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def sendgrid_webhook(request):
    """
    Handle SendGrid Event Webhook.
    Events: bounce, dropped, spamreport, unsubscribe, delivered, open, click.

    Configure in SendGrid: Settings > Mail Settings > Event Webhook
    URL: https://yourdomain.com/webhooks/sendgrid/
    """
    try:
        events = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return HttpResponse(status=400)

    for event in events:
        event_type = event.get('event', '')
        email = event.get('email', '')

        if not email:
            continue

        if event_type == 'bounce':
            bounce_type = 'hard' if event.get('type') == 'bounce' else 'soft'
            handle_bounce(email, bounce_type=bounce_type)

        elif event_type == 'dropped':
            handle_bounce(email, bounce_type='hard')

        elif event_type == 'spamreport':
            handle_complaint(email)

        elif event_type == 'unsubscribe':
            add_unsubscribe(email, reason='sendgrid_unsubscribe')

        elif event_type == 'delivered':
            from core.utils.email_engine.warming import record_delivery
            record_delivery()

    return HttpResponse(status=200)


@require_GET
def unsubscribe_page(request):
    """
    Public unsubscribe page. User clicks link in email footer.
    Adds their email to the unsubscribe list.
    """
    email = request.GET.get('email', '').strip()
    if email:
        add_unsubscribe(email, reason='user_unsubscribe')
        return HttpResponse(
            '<html><body style="background:#07070C;color:#F1F1F5;font-family:sans-serif;'
            'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">'
            '<div style="text-align:center;max-width:400px;">'
            '<h2>You have been unsubscribed</h2>'
            '<p style="color:#A0A0B8;">You will no longer receive outreach emails from us. '
            'We apologize for any inconvenience.</p>'
            '</div></body></html>',
            content_type='text/html',
        )
    return HttpResponse(
        '<html><body style="background:#07070C;color:#F1F1F5;font-family:sans-serif;'
        'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">'
        '<div style="text-align:center;">'
        '<h2>Unsubscribe</h2>'
        '<p style="color:#A0A0B8;">No email address provided.</p>'
        '</div></body></html>',
        content_type='text/html',
    )
