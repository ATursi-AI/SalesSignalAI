"""
Add intent classification, curation fields, deal value scoring,
and two-tier service model.

- Lead: intent_classification, intent_confidence, intent_service_detected,
        intent_classified_at, intent_classified_by, is_curated, curated_by,
        curated_at, reach_score, reach_scored_at
- ServiceCategory: avg_deal_value, deal_value_tier
- LeadAssignment: assignment_type, service_tier
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0040_lead_dismissed_status'),
    ]

    operations = [
        # ── Lead intent classification fields ──
        migrations.AddField(
            model_name='lead',
            name='intent_classification',
            field=models.CharField(
                choices=[
                    ('not_classified', 'Not Classified'),
                    ('real_lead', 'Real Lead'),
                    ('mention_only', 'Mention Only'),
                    ('false_positive', 'False Positive'),
                    ('job_posting', 'Job Posting'),
                    ('advice_giving', 'Advice/Discussion'),
                ],
                db_index=True,
                default='not_classified',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='intent_confidence',
            field=models.FloatField(
                default=0.0,
                help_text='AI confidence in intent classification (0.0-1.0)',
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='intent_service_detected',
            field=models.CharField(
                blank=True,
                help_text='Service type detected by AI classifier',
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='intent_classified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='lead',
            name='intent_classified_by',
            field=models.CharField(
                blank=True,
                default='',
                help_text='ai or staff username who classified',
                max_length=20,
            ),
        ),

        # ── Curation fields ──
        migrations.AddField(
            model_name='lead',
            name='is_curated',
            field=models.BooleanField(
                default=False,
                help_text='Manually placed into a customer feed by staff',
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='curated_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='curated_leads',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='curated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ── REACH scoring ──
        migrations.AddField(
            model_name='lead',
            name='reach_score',
            field=models.IntegerField(
                db_index=True,
                default=0,
                help_text='REACH priority score (0-100)',
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='reach_scored_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ── ServiceCategory deal value ──
        migrations.AddField(
            model_name='servicecategory',
            name='avg_deal_value',
            field=models.IntegerField(
                default=0,
                help_text='Average deal value in dollars for REACH scoring (e.g. 8000 for roofing)',
            ),
        ),
        migrations.AddField(
            model_name='servicecategory',
            name='deal_value_tier',
            field=models.CharField(
                choices=[
                    ('low', 'Low ($0-500)'),
                    ('medium', 'Medium ($500-5K)'),
                    ('high', 'High ($5K+)'),
                ],
                default='medium',
                help_text='Used by REACH to prioritize high-value leads',
                max_length=10,
            ),
        ),

        # ── LeadAssignment service tier ──
        migrations.AddField(
            model_name='leadassignment',
            name='assignment_type',
            field=models.CharField(
                choices=[
                    ('auto', 'Auto-Assigned'),
                    ('curated', 'Staff Curated'),
                ],
                default='auto',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='leadassignment',
            name='service_tier',
            field=models.CharField(
                choices=[
                    ('self_service', 'Self-Service (Customer contacts lead)'),
                    ('managed', 'Managed (SalesSignalAI contacts lead)'),
                    ('unset', 'Not Selected'),
                ],
                default='unset',
                help_text='How this lead will be worked: customer contacts or we contact for them',
                max_length=15,
            ),
        ),
    ]
