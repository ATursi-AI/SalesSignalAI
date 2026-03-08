import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from core.models import LeadAssignment

logger = logging.getLogger(__name__)


def dispatch_pending_alerts():
    """Find all un-alerted lead assignments and send alerts."""
    pending = LeadAssignment.objects.filter(
        status='new',
        alert_sent_at__isnull=True,
    ).select_related('lead', 'business', 'business__user', 'lead__detected_service_type')

    sent_count = 0
    for assignment in pending:
        business = assignment.business
        lead = assignment.lead

        methods = []

        if business.alert_via_email:
            success = send_email_alert(assignment)
            if success:
                methods.append('email')

        if business.alert_via_sms and business.alert_phone:
            success = send_sms_alert(assignment)
            if success:
                methods.append('sms')

        if methods:
            assignment.status = 'alerted'
            assignment.alert_sent_at = timezone.now()
            assignment.alert_method = '+'.join(methods)
            assignment.save()
            sent_count += 1

    return sent_count


def send_email_alert(assignment):
    """Send an HTML email alert for a new lead."""
    lead = assignment.lead
    business = assignment.business

    subject = f"[{lead.urgency_level.upper()}] New {lead.get_platform_display()} lead"
    if lead.detected_location:
        subject += f" in {lead.detected_location}"

    context = {
        'lead': lead,
        'business': business,
        'assignment': assignment,
        'dashboard_url': '/dashboard/',
        'lead_url': f'/leads/{assignment.id}/',
    }

    try:
        html_message = render_to_string('emails/lead_alert.html', context)
        plain_message = strip_tags(html_message)

        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.ALERT_FROM_EMAIL,
            recipient_list=[business.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(f"Email alert sent to {business.email} for lead #{lead.id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email alert to {business.email}: {e}")
        return False


def send_sms_alert(assignment):
    """Send an SMS alert via Twilio for a new lead."""
    from core.utils.alerts.sms import send_sms

    lead = assignment.lead
    business = assignment.business

    urgency_icon = {'hot': 'HOT', 'warm': 'WARM', 'new': 'NEW'}.get(lead.urgency_level, '')

    message = (
        f"[{urgency_icon}] SalesSignal: "
        f"Someone in {lead.detected_location or 'your area'} "
        f"needs {lead.detected_service_type.name if lead.detected_service_type else 'your service'}. "
        f"Posted on {lead.get_platform_display()}. "
        f"View: /leads/{assignment.id}/"
    )

    return send_sms(business.alert_phone, message)
