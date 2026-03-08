"""
SPF, DKIM, and DMARC DNS configuration guide for outreach email deliverability.

This module provides a management command helper that outputs the exact DNS
records needed for the sending domain.
"""

DNS_GUIDE = """
===============================================================================
  EMAIL DELIVERABILITY — DNS CONFIGURATION GUIDE
===============================================================================

Your sending domain: {domain}

1. SPF RECORD
   -------------------------------------------------------------------------
   Type: TXT
   Host: @
   Value: v=spf1 include:sendgrid.net ~all

   This tells receiving servers that SendGrid is authorized to send on
   behalf of your domain.


2. DKIM RECORD (via SendGrid)
   -------------------------------------------------------------------------
   Go to: SendGrid Dashboard > Settings > Sender Authentication > Authenticate Your Domain
   SendGrid will provide two CNAME records to add:

   Type: CNAME
   Host: s1._domainkey.{domain}
   Value: s1.domainkey.u{sendgrid_id}.wl.sendgrid.net

   Type: CNAME
   Host: s2._domainkey.{domain}
   Value: s2.domainkey.u{sendgrid_id}.wl.sendgrid.net

   (Replace {sendgrid_id} with your SendGrid account ID)


3. DMARC RECORD
   -------------------------------------------------------------------------
   Type: TXT
   Host: _dmarc
   Value: v=DMARC1; p=none; rua=mailto:dmarc-reports@{domain}; pct=100

   Start with p=none (monitor only), then move to p=quarantine after
   verifying no legitimate emails are failing, then p=reject.

   DMARC progression:
     Week 1-2:  p=none     (monitor, collect reports)
     Week 3-4:  p=quarantine; pct=25   (quarantine 25% of failures)
     Week 5-6:  p=quarantine; pct=100  (quarantine all failures)
     Week 7+:   p=reject   (reject all failures)


4. RETURN PATH / BRANDED LINK (optional)
   -------------------------------------------------------------------------
   In SendGrid, enable "Link Branding" and "Return Path" for your domain.
   This improves deliverability by showing your domain in "via" header.


5. VERIFICATION CHECKLIST
   -------------------------------------------------------------------------
   [ ] SPF record added and verified (dig TXT {domain})
   [ ] DKIM authenticated in SendGrid dashboard
   [ ] DMARC record added
   [ ] Test email passes SPF, DKIM, DMARC (use mail-tester.com)
   [ ] SendGrid sender identity verified
   [ ] Dedicated IP (if sending 50k+/month)

===============================================================================
"""


def get_dns_guide(domain='yourdomain.com', sendgrid_id='XXXXXXX'):
    """Return formatted DNS configuration guide."""
    return DNS_GUIDE.format(domain=domain, sendgrid_id=sendgrid_id)
