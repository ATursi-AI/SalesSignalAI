"""
Sales Sequence Engine — SalesSequence, SequenceStep, SequenceEnrollment, SequenceStepLog
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0043_merge_20260404_2124'),
    ]

    operations = [
        # SalesSequence
        migrations.CreateModel(
            name='SalesSequence',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='E.g. "Video Drip - Plumbers" or "High Value Target Sequence"', max_length=200)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('paused', 'Paused'), ('archived', 'Archived')], default='draft', max_length=10)),
                ('target_trade', models.CharField(blank=True, help_text='E.g. "plumber", "electrician" — or blank for any trade', max_length=100)),
                ('target_region', models.CharField(blank=True, help_text='E.g. "Austin TX", "Nassau County NY"', max_length=200)),
                ('send_from_name', models.CharField(default='SalesSignal AI', max_length=100)),
                ('send_from_email', models.EmailField(default='outreach@salessignalai.com', max_length=254)),
                ('daily_send_limit', models.IntegerField(default=50, help_text='Max emails per day across all enrollments in this sequence')),
                ('total_enrolled', models.IntegerField(default=0)),
                ('total_completed', models.IntegerField(default=0)),
                ('total_replied', models.IntegerField(default=0)),
                ('total_converted', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'Sales Sequences',
                'ordering': ['-updated_at'],
            },
        ),

        # SequenceStep
        migrations.CreateModel(
            name='SequenceStep',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('step_number', models.IntegerField(help_text='Order of execution. Step 1 runs first.')),
                ('step_type', models.CharField(choices=[('email', 'Send Email'), ('video_email', 'Send Video Email'), ('call', 'Phone Call Task'), ('sms', 'Send SMS'), ('wait', 'Wait Period'), ('linkedin', 'LinkedIn Touch')], max_length=20)),
                ('name', models.CharField(blank=True, help_text='E.g. "Intro Video Email", "Follow-up Call"', max_length=200)),
                ('delay_days', models.IntegerField(default=0, help_text='Days to wait after previous step completes. 0 = same day as previous.')),
                ('email_subject', models.CharField(blank=True, help_text='Supports {business_name}, {owner_name}, {trade}, {city} placeholders', max_length=300)),
                ('email_body', models.TextField(blank=True, help_text='HTML email body. Supports same placeholders + {video_link}, {video_thumbnail}')),
                ('use_ai_personalization', models.BooleanField(default=False, help_text='Let AI rewrite the email for each prospect')),
                ('call_script_notes', models.TextField(blank=True, help_text='Talking points for the call task')),
                ('call_priority', models.CharField(choices=[('high', 'High'), ('normal', 'Normal'), ('low', 'Low')], default='normal', max_length=10)),
                ('sms_body', models.CharField(blank=True, help_text='SMS text. Supports {business_name}, {first_name}, {video_link}', max_length=320)),
                ('skip_if_replied', models.BooleanField(default=True, help_text='Skip this step if prospect already replied')),
                ('skip_if_opened', models.BooleanField(default=False, help_text='Skip this step if prospect opened a previous email')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sequence', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='core.salessequence')),
            ],
            options={
                'ordering': ['sequence', 'step_number'],
                'unique_together': {('sequence', 'step_number')},
            },
        ),

        # SequenceEnrollment
        migrations.CreateModel(
            name='SequenceEnrollment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('active', 'Active'), ('paused', 'Paused'), ('completed', 'Completed'), ('replied', 'Replied'), ('converted', 'Converted'), ('bounced', 'Bounced'), ('opted_out', 'Opted Out'), ('removed', 'Removed')], default='active', max_length=12)),
                ('current_step', models.IntegerField(default=0, help_text='The step_number they are currently on. 0 = not started.')),
                ('next_action_date', models.DateField(blank=True, help_text='When the next step should fire', null=True)),
                ('emails_sent', models.IntegerField(default=0)),
                ('emails_opened', models.IntegerField(default=0)),
                ('emails_clicked', models.IntegerField(default=0)),
                ('calls_made', models.IntegerField(default=0)),
                ('replied', models.BooleanField(default=False)),
                ('replied_at', models.DateTimeField(blank=True, null=True)),
                ('reply_sentiment', models.CharField(blank=True, choices=[('interested', 'Interested'), ('not_interested', 'Not Interested'), ('question', 'Question'), ('out_of_office', 'Out of Office')], max_length=20)),
                ('batch_tag', models.CharField(blank=True, help_text='Tag for grouping batch enrollments. E.g. "austin-plumbers-apr-2026"', max_length=100)),
                ('enrolled_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('enrolled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('prospect', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sequence_enrollments', to='core.salesprospect')),
                ('sequence', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enrollments', to='core.salessequence')),
                ('video_page', models.ForeignKey(blank=True, help_text='Personalized video landing page for this prospect', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='enrollments', to='core.prospectvideo')),
            ],
            options={
                'ordering': ['next_action_date', '-enrolled_at'],
                'unique_together': {('sequence', 'prospect')},
            },
        ),

        # SequenceStepLog
        migrations.CreateModel(
            name='SequenceStepLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('result', models.CharField(choices=[('sent', 'Sent'), ('delivered', 'Delivered'), ('opened', 'Opened'), ('clicked', 'Clicked'), ('replied', 'Replied'), ('bounced', 'Bounced'), ('skipped', 'Skipped'), ('failed', 'Failed'), ('task_created', 'Task Created'), ('task_completed', 'Task Completed')], max_length=20)),
                ('sendgrid_message_id', models.CharField(blank=True, max_length=200)),
                ('email_subject_sent', models.CharField(blank=True, max_length=300)),
                ('email_opened_at', models.DateTimeField(blank=True, null=True)),
                ('email_clicked_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('executed_at', models.DateTimeField(auto_now_add=True)),
                ('enrollment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_logs', to='core.sequenceenrollment')),
                ('sales_activity', models.ForeignKey(blank=True, help_text='The SalesActivity task created for call steps', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sequence_step_logs', to='core.salesactivity')),
                ('step', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logs', to='core.sequencestep')),
                ('video_page', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.prospectvideo')),
            ],
            options={
                'ordering': ['-executed_at'],
            },
        ),
    ]
