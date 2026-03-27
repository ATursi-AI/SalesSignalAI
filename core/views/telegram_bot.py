"""
Telegram bot webhook — routes messages to AI agents.
"""
import json
import logging
import threading
import requests
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('telegram')


def _allowed_users():
    ids = getattr(settings, 'TELEGRAM_ALLOWED_USERS', '')
    if isinstance(ids, str):
        return {int(x.strip()) for x in ids.split(',') if x.strip().isdigit()}
    return set()


def _send(chat_id, text, parse_mode='HTML'):
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        try:
            requests.post(url, json={'chat_id': chat_id, 'text': text[i:i+4000], 'parse_mode': parse_mode}, timeout=10)
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")


def _typing(chat_id):
    try:
        requests.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendChatAction",
                       json={'chat_id': chat_id, 'action': 'typing'}, timeout=5)
    except Exception:
        pass


def _run_agent(chat_id, goal, agent_name, user_info):
    from core.models.leads import AgentMission
    from core.agents import get_agent

    _typing(chat_id)

    mission = AgentMission.objects.create(
        agent_name=agent_name, goal=goal, status='running',
        triggered_by=f'telegram:{user_info}', triggered_from=str(chat_id),
        started_at=timezone.now(),
    )

    try:
        agent = get_agent(agent_name)
        result = agent.run(goal, mission_id=mission.id)
        mission.status = 'complete'
        mission.result = result or ''
        mission.mission_log = agent.mission_log
        mission.steps_taken = len(agent.mission_log)
        mission.completed_at = timezone.now()
        mission.save()
        _send(chat_id, f"<b>{agent_name.title()} Complete</b>\n\n{result}")
    except Exception as e:
        mission.status = 'error'
        mission.result = str(e)
        mission.completed_at = timezone.now()
        mission.save()
        _send(chat_id, f"Agent Error\n\n{str(e)[:500]}")


@csrf_exempt
@require_POST
def telegram_webhook(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse('ok')

    message = data.get('message', {})
    if not message:
        return HttpResponse('ok')

    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    username = message.get('from', {}).get('username', '')
    first_name = message.get('from', {}).get('first_name', '')
    text = (message.get('text') or '').strip()

    if not text or not chat_id:
        return HttpResponse('ok')

    allowed = _allowed_users()
    if allowed and user_id not in allowed:
        _send(chat_id, "You are not authorized. Ask the admin to add your Telegram user ID.")
        logger.warning(f"Unauthorized Telegram: {user_id} ({username})")
        return HttpResponse('ok')

    logger.info(f"Telegram from {user_id} ({username}): {text[:100]}")
    lo = text.lower()

    # /start
    if lo == '/start':
        _send(chat_id, (
            "<b>SalesSignal HQ</b>\n\n"
            "I'm your AI agent command center.\n\n"
            "<b>Find leads:</b>\n<code>Find 20 plumbing leads in Queens</code>\n\n"
            "<b>Discover sources:</b>\n<code>Discover data sources for contractors in Texas</code>\n\n"
            "<b>Quick commands:</b>\n"
            "/status /leads /agents /missions /help /id\n\n"
            f"Your user ID: <code>{user_id}</code>"
        ))
        return HttpResponse('ok')

    if lo == '/help':
        _send(chat_id, (
            "<b>Commands:</b>\n\n"
            "Natural language:\n"
            "- <code>Find [N] [trade] leads in [location]</code>\n"
            "- <code>Discover data sources in [state]</code>\n"
            "- <code>Search NYC DOB violations in [borough]</code>\n\n"
            "Quick:\n/status /leads /agents /missions /id"
        ))
        return HttpResponse('ok')

    if lo == '/id':
        _send(chat_id, f"Your user ID: <code>{user_id}</code>")
        return HttpResponse('ok')

    if lo in ('/status', 'status'):
        from core.models.leads import Lead, AgentMission
        lc = Lead.objects.count()
        recent = AgentMission.objects.all()[:5]
        msg = f"<b>Status</b>\n\nTotal leads: <b>{lc}</b>\n"
        if recent:
            msg += "\n<b>Recent:</b>\n"
            for m in recent:
                e = {'complete': 'OK', 'running': '...', 'error': 'ERR'}.get(m.status, '?')
                msg += f"[{e}] {m.agent_name}: {m.goal[:50]}\n"
        _send(chat_id, msg)
        return HttpResponse('ok')

    if lo == '/leads':
        from core.models.leads import Lead
        from django.db.models import Count
        total = Lead.objects.count()
        by_r = Lead.objects.values('region').annotate(c=Count('id')).order_by('-c')[:5]
        msg = f"<b>Leads: {total}</b>\n\nTop regions:\n"
        for r in by_r:
            msg += f"- {r['region'] or '?'}: {r['c']}\n"
        _send(chat_id, msg)
        return HttpResponse('ok')

    if lo == '/agents':
        from core.agents import list_agents
        agents = list_agents()
        msg = "<b>Agents:</b>\n\n"
        for n, d in agents.items():
            msg += f"- <b>{n}</b>: {d[:80]}\n"
        _send(chat_id, msg)
        return HttpResponse('ok')

    if lo == '/missions':
        from core.models.leads import AgentMission
        missions = AgentMission.objects.all()[:10]
        msg = "<b>Recent Missions:</b>\n\n"
        for m in missions:
            e = {'complete': 'OK', 'running': '...', 'error': 'ERR'}.get(m.status, '?')
            msg += f"[{e}] <b>{m.agent_name}</b> {m.goal[:60]}\n"
        if not missions:
            msg += "No missions yet."
        _send(chat_id, msg)
        return HttpResponse('ok')

    # Route to agent
    agent_name = 'orchestrator'
    if lo.startswith('discover') or 'find source' in lo or 'data source' in lo:
        agent_name = 'discovery'

    _send(chat_id, f"Deploying <b>{agent_name}</b>...\n\n<i>{text}</i>\n\nResults coming soon.")

    threading.Thread(
        target=_run_agent,
        args=(chat_id, text, agent_name, f"{username or first_name}:{user_id}"),
        daemon=True,
    ).start()

    return HttpResponse('ok')
