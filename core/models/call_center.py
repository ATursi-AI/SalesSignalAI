from django.db import models


class CallLog(models.Model):
    DIRECTION_CHOICES = [('inbound', 'Inbound'), ('outbound', 'Outbound')]
    STATUS_CHOICES = [
        ('initiated', 'Initiated'), ('ringing', 'Ringing'),
        ('answered', 'Answered'), ('completed', 'Completed'),
        ('no-answer', 'No Answer'), ('busy', 'Busy'), ('failed', 'Failed'),
    ]
    DISPOSITION_CHOICES = [
        ('interested', 'Interested'),
        ('not_interested', 'Not Interested'),
        ('callback', 'Callback Requested'),
        ('wrong_number', 'Wrong Number'),
        ('no_answer', 'No Answer'),
        ('left_voicemail', 'Left Voicemail'),
        ('appointment_booked', 'Appointment Booked'),
    ]

    call_sid = models.CharField(max_length=100, unique=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    from_number = models.CharField(max_length=20)
    to_number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='initiated')
    duration = models.IntegerField(default=0)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    recording_url = models.URLField(blank=True, max_length=500)
    voicemail_transcription = models.TextField(blank=True)

    lead = models.ForeignKey('Lead', null=True, blank=True, on_delete=models.SET_NULL, related_name='call_logs')
    salesperson = models.ForeignKey('SalesPerson', null=True, blank=True, on_delete=models.SET_NULL, related_name='call_logs')

    disposition = models.CharField(max_length=30, blank=True, choices=DISPOSITION_CHOICES)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.direction} {self.from_number} → {self.to_number} ({self.status})"


class SMSMessage(models.Model):
    DIRECTION_CHOICES = [('inbound', 'Inbound'), ('outbound', 'Outbound')]
    STATUS_CHOICES = [
        ('sent', 'Sent'), ('delivered', 'Delivered'),
        ('failed', 'Failed'), ('received', 'Received'),
    ]

    message_sid = models.CharField(max_length=100, blank=True, db_index=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    from_number = models.CharField(max_length=20, db_index=True)
    to_number = models.CharField(max_length=20, db_index=True)
    body = models.TextField()
    media_url = models.URLField(blank=True, max_length=500)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='sent')
    sent_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    lead = models.ForeignKey('Lead', null=True, blank=True, on_delete=models.SET_NULL, related_name='sms_messages')
    salesperson = models.ForeignKey('SalesPerson', null=True, blank=True, on_delete=models.SET_NULL, related_name='sms_messages')

    is_yes_response = models.BooleanField(default=False)
    is_opt_out = models.BooleanField(default=False)

    class Meta:
        ordering = ['sent_at']

    def __str__(self):
        return f"{self.direction} {self.from_number}: {self.body[:50]}"


class SMSOptOut(models.Model):
    phone_number = models.CharField(max_length=20, unique=True)
    opted_out_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Opt-out: {self.phone_number}"
