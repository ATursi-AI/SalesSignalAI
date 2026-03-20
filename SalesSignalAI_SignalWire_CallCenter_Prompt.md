# Claude Code Prompt: SignalWire Call Center & SMS System for SalesSignalAI

## Context

SalesSignalAI at /root/SalesSignalAI/ (Django app) needs a full communication system built on SignalWire. This covers SMS outreach, inbound text handling, browser-based calling for sales reps, and a call center interface integrated with the existing lead and sales CRM.

## Environment Variables (add to .env)

```
SIGNALWIRE_PROJECT_ID=your_project_id_here
SIGNALWIRE_API_TOKEN=your_api_token_here
SIGNALWIRE_SPACE_URL=your_space.signalwire.com
SIGNALWIRE_PHONE_NUMBER=+1XXXXXXXXXX
SIGNALWIRE_FALLBACK_PHONE=+1XXXXXXXXXX  # Andrew's cell for forwarding
```

## Dependencies

Install the SignalWire Python SDK:
```bash
pip install signalwire
```

Also need: `channels` and `channels-redis` for WebSocket support (browser softphone)

## Part 1: Core SignalWire Service

Create `core/services/signalwire_service.py` — a service class that wraps all SignalWire API calls:

```python
class SignalWireService:
    # SMS
    def send_sms(self, to_number, message, media_url=None)
    def send_bulk_sms(self, recipients_list)  # list of {number, message}
    
    # Voice
    def make_call(self, to_number, from_number=None, webhook_url=None)
    def forward_call(self, to_number)
    def get_call_status(self, call_sid)
    
    # Number management
    def list_phone_numbers(self)
    def buy_phone_number(self, area_code)
    
    # Call logs
    def get_call_logs(self, days=7)
    def get_sms_logs(self, days=7)
```

All methods should have error handling, logging, and return consistent response objects.

## Part 2: SMS System

### Outbound SMS

Create a management command and API endpoint for sending SMS:

**Management command:**
```bash
python manage.py send_sms --to "+15165551234" --message "Hey Joe - check this out: salessignalai.com/demo/joes-plumbing"
```

**API endpoint:** POST `/api/sms/send/`
```json
{
    "to": "+15165551234",
    "message": "Hey Joe...",
    "media_url": null,
    "lead_id": 123
}
```

When an SMS is sent linked to a lead, log it in the lead's activity history.

**Bulk SMS from dashboard:**
Staff can select multiple leads and click "Send SMS" to send a templated message to all of them. The template supports variables: {name}, {business}, {city}, {service}.

### Inbound SMS / Webhook

Create a webhook endpoint at `/api/signalwire/sms-webhook/` that SignalWire calls when someone texts back:

1. Receive the incoming SMS (from_number, body, to_number)
2. Look up the from_number in leads and prospect videos
3. If found — log the response on the lead, update status
4. If the body contains "YES" (case insensitive):
   - Auto-reply: "Thanks! Someone from our team will call you within the hour."
   - Send notification to admin (email + SMS to SIGNALWIRE_FALLBACK_PHONE): "HOT LEAD: {name} texted YES. Their number: {from_number}. Lead: {details}"
   - Mark the lead/prospect video as responded
5. If body contains "STOP":
   - Add number to opt-out list
   - Auto-reply: "You've been unsubscribed. Reply START to re-subscribe."
   - Never text this number again
6. Any other response — log it, notify admin, let admin respond manually

### SMS Inbox in Dashboard

Add `/sales/sms-inbox/` page showing:
- All inbound and outbound SMS conversations grouped by phone number
- Thread view like iMessage — your messages on right, theirs on left
- Reply box at the bottom to send a response
- Mark as read/unread
- Link to associated lead if one exists
- Filter by: all, unread, YES responses, opt-outs

### SMS from Lead Detail Page

On any lead detail page, if the lead has a phone number:
- Show a "Send SMS" button
- Click opens a compose box with pre-filled template
- Send button sends via SignalWire and logs in activity history
- Show SMS conversation thread on the lead detail page

### SMS from Prospect Video Admin

On the prospect video edit page:
- Replace the "Copy SMS" clipboard button with actual "Send SMS" button
- Clicking sends the SMS via SignalWire with the prospect video link
- Marks sms_sent = True, records timestamp
- Shows delivery status

## Part 3: Voice — Browser Softphone

Build a browser-based phone at `/sales/phone/` that lets reps make and receive calls from their browser.

### WebRTC Softphone Page

Create a full-page phone interface at `/sales/phone/`:

**Layout:**
- Left panel: dial pad + active call controls
- Right panel: lead info card (auto-populates when calling a lead)

**Dial pad:**
- Number input field
- 0-9, *, # buttons
- Call button (green)
- Hang up button (red)
- Mute toggle
- Hold toggle
- Transfer button (enter number to transfer to)
- Speaker volume slider
- Mic volume slider

**Active call display:**
- Caller name (if matched to a lead)
- Phone number
- Call duration timer
- Call status: ringing, connected, on hold, ended

**Lead context panel (right side):**
When calling from a lead, show:
- Lead title and details
- Source (violation, permit, health inspection, etc.)
- Contact info
- Previous call history with this lead
- Notes field to type during the call
- Disposition buttons after call ends: "Interested", "Not Interested", "Callback", "Wrong Number", "No Answer", "Left Voicemail"

### WebRTC Implementation

Use SignalWire's JavaScript SDK for browser-based calling:

```html
<script src="https://unpkg.com/@signalwire/js@3"></script>
```

The softphone connects to SignalWire via WebSocket. When the rep clicks call:
1. Browser establishes WebRTC connection to SignalWire
2. SignalWire places outbound call to the lead's number
3. Caller ID shows the SalesSignal phone number
4. Audio streams through the browser

For inbound calls:
1. When someone calls the SalesSignal number
2. SignalWire hits the voice webhook
3. Webhook checks if any rep is online in the softphone
4. If yes — ring the rep's browser
5. If no — forward to SIGNALWIRE_FALLBACK_PHONE (Andrew's cell)

### Voice Webhooks

Create `/api/signalwire/voice-webhook/` for inbound call handling:

```python
# Inbound call flow:
# 1. Check if calling number matches any lead — if so, log it
# 2. Check if any rep is online in softphone
# 3. If rep online — ring browser via WebRTC
# 4. If no rep — forward to fallback number
# 5. If no answer after 30 seconds — go to voicemail
# 6. Voicemail transcription sent to admin email
```

Create `/api/signalwire/call-status-webhook/` for call status updates:
- Log call start, answer, end times
- Calculate call duration
- Store in CallLog model

## Part 4: Call Center Dashboard

### Call Center Admin Page (`/sales/call-center/`)

Dashboard showing:

**Today's Stats Cards:**
- Calls made today
- Calls answered
- Average call duration
- SMS sent today
- YES responses today
- Appointments booked today

**Active Reps:**
- List of logged-in reps with status: Available, On Call, Away
- Current call info if on a call

**Call Queue (for inbound):**
- Incoming calls waiting
- Time in queue
- Caller info if matched to lead

**Recent Activity Feed:**
- Last 50 calls and SMS with timestamps, duration, disposition
- Click any entry to see full details

### Rep Dashboard (`/sales/my-calls/`)

Each salesperson sees their own:
- Today's call list (leads assigned to them with click-to-call)
- Call history
- SMS conversations
- Stats: calls made, answered, appointments booked
- Leaderboard: compare to other reps (if multiple reps)

### Click-to-Call from Anywhere

Add a phone icon button next to any phone number displayed in:
- Lead repository
- Lead detail pages
- Sales CRM prospect pages
- Health inspection leads (they have phone numbers)
- Prospect video pages

Clicking the phone icon:
1. Opens the softphone in a slide-out panel (not a new page)
2. Auto-dials the number
3. Shows the lead context
4. Logs the call on the lead

## Part 5: Data Models

### CallLog Model
```python
class CallLog(models.Model):
    call_sid = models.CharField(max_length=100, unique=True)
    direction = models.CharField(choices=[('inbound','Inbound'),('outbound','Outbound')])
    from_number = models.CharField(max_length=20)
    to_number = models.CharField(max_length=20)
    status = models.CharField(max_length=20)  # initiated, ringing, answered, completed, no-answer, busy, failed
    duration = models.IntegerField(default=0)  # seconds
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)
    recording_url = models.URLField(blank=True)
    voicemail_transcription = models.TextField(blank=True)
    
    # Link to lead/rep
    lead = models.ForeignKey('Lead', null=True, on_delete=models.SET_NULL)
    salesperson = models.ForeignKey('SalesPerson', null=True, on_delete=models.SET_NULL)
    
    # After-call disposition
    disposition = models.CharField(max_length=30, blank=True, choices=[
        ('interested', 'Interested'),
        ('not_interested', 'Not Interested'),
        ('callback', 'Callback Requested'),
        ('wrong_number', 'Wrong Number'),
        ('no_answer', 'No Answer'),
        ('left_voicemail', 'Left Voicemail'),
        ('appointment_booked', 'Appointment Booked'),
    ])
    notes = models.TextField(blank=True)
```

### SMSMessage Model
```python
class SMSMessage(models.Model):
    direction = models.CharField(choices=[('inbound','Inbound'),('outbound','Outbound')])
    from_number = models.CharField(max_length=20)
    to_number = models.CharField(max_length=20)
    body = models.TextField()
    media_url = models.URLField(blank=True)
    status = models.CharField(max_length=20)  # sent, delivered, failed, received
    sent_at = models.DateTimeField(auto_now_add=True)
    
    # Link to lead
    lead = models.ForeignKey('Lead', null=True, on_delete=models.SET_NULL)
    salesperson = models.ForeignKey('SalesPerson', null=True, on_delete=models.SET_NULL)
    
    # For YES detection
    is_yes_response = models.BooleanField(default=False)
    is_opt_out = models.BooleanField(default=False)
```

### SMSOptOut Model
```python
class SMSOptOut(models.Model):
    phone_number = models.CharField(max_length=20, unique=True)
    opted_out_at = models.DateTimeField(auto_now_add=True)
```

Always check opt-out list before sending any SMS.

## Part 6: Call Recording & Compliance

- All outbound calls should be recorded (SignalWire supports this natively)
- Store recording URL in CallLog
- Play back recordings from the call log in dashboard
- Add a disclaimer at the start of each outbound call: brief tone/beep indicating recording (or configure per state laws)
- SMS opt-out handling (STOP/START) is required by TCPA

## Part 7: Notifications

When a hot event happens, notify admin immediately:

- Someone texts YES → SMS to Andrew's cell + email + dashboard notification
- Inbound call from a known lead → browser notification if on softphone page
- Missed call → SMS to Andrew's cell with caller info
- Voicemail received → email with transcription

Use Django signals or a simple notification system that sends to:
1. SMS (via SignalWire to fallback number)
2. Email (via existing email system)
3. Dashboard notification (store in DB, show in top nav bell icon)

## Design & Integration

- All new pages use the light theme with Exo 2 headings
- Softphone panel should feel like a native app — clean, fast, minimal
- SMS inbox should look like iMessage — clean thread view
- Add "Phone" and "SMS" to the sales team sidebar navigation
- Call center stats cards match the existing dashboard card style
- Mobile responsive — the softphone should work on tablet too

## Testing

After building, test with:
1. Send a test SMS: `python manage.py send_sms --to "+1YOURNUMBER" --message "Test from SalesSignal"`
2. Text back YES to the SalesSignal number and verify auto-reply + notification
3. Make a test call from the browser softphone
4. Call the SalesSignal number from your cell and verify it rings in browser or forwards
