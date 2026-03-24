import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST

from core.models.workflows import WorkflowRule, WorkflowExecution
from core.models.sales import SalesPerson, SalesProspect
from core.models.outreach import OutreachCampaign


def _get_business(request):
    if hasattr(request.user, 'business_profile'):
        return request.user.business_profile
    return None


@login_required
def workflow_list(request):
    """List all workflow rules."""
    business = _get_business(request)
    rules = WorkflowRule.objects.filter(business=business) if business else WorkflowRule.objects.all()

    trigger_filter = request.GET.get('trigger', '')
    if trigger_filter:
        rules = rules.filter(trigger=trigger_filter)

    context = {
        'rules': rules,
        'trigger_choices': WorkflowRule.TRIGGER_CHOICES,
        'current_trigger': trigger_filter,
        'total': rules.count(),
        'active_count': rules.filter(is_active=True).count(),
    }
    return render(request, 'workflows/list.html', context)


@login_required
def workflow_builder(request, rule_id=None):
    """Create or edit a workflow rule."""
    business = _get_business(request)
    rule = None
    if rule_id:
        rule = get_object_or_404(WorkflowRule, pk=rule_id)

    if request.method == 'POST':
        data = json.loads(request.body)
        if not rule:
            rule = WorkflowRule(business=business)

        rule.name = data.get('name', 'Untitled Workflow')
        rule.description = data.get('description', '')
        rule.trigger = data.get('trigger', 'lead_status_changed')
        rule.trigger_conditions = data.get('conditions', {})
        rule.actions = data.get('actions', [])
        rule.is_active = data.get('is_active', True)
        rule.save()

        return JsonResponse({'ok': True, 'id': rule.id})

    # GET context
    campaigns = OutreachCampaign.objects.all().order_by('-created_at')[:20]
    reps = SalesPerson.objects.filter(status='active')
    stages = SalesProspect.PIPELINE_CHOICES

    context = {
        'rule': rule,
        'rule_json': json.dumps({
            'id': rule.id if rule else None,
            'name': rule.name if rule else '',
            'description': rule.description if rule else '',
            'trigger': rule.trigger if rule else '',
            'conditions': rule.trigger_conditions if rule else {},
            'actions': rule.actions if rule else [],
            'is_active': rule.is_active if rule else True,
        }),
        'trigger_choices': WorkflowRule.TRIGGER_CHOICES,
        'action_choices': WorkflowRule.ACTION_CHOICES,
        'campaigns': campaigns,
        'reps': reps,
        'stages': stages,
    }
    return render(request, 'workflows/builder.html', context)


@login_required
def workflow_detail(request, rule_id):
    """View workflow detail and execution log."""
    rule = get_object_or_404(WorkflowRule, pk=rule_id)
    executions = rule.executions.all()[:50]

    context = {
        'rule': rule,
        'executions': executions,
        'success_count': rule.executions.filter(status='completed').count(),
        'fail_count': rule.executions.filter(status='failed').count(),
        'waiting_count': rule.executions.filter(status='waiting').count(),
    }
    return render(request, 'workflows/detail.html', context)


@login_required
@require_POST
def workflow_toggle(request, rule_id):
    """Toggle workflow active state."""
    rule = get_object_or_404(WorkflowRule, pk=rule_id)
    rule.is_active = not rule.is_active
    rule.save(update_fields=['is_active'])
    return JsonResponse({'ok': True, 'is_active': rule.is_active})


@login_required
@require_POST
def workflow_delete(request, rule_id):
    """Delete a workflow rule."""
    rule = get_object_or_404(WorkflowRule, pk=rule_id)
    rule.delete()
    return JsonResponse({'ok': True})
