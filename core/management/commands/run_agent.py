"""
Run an AI agent from the command line.
Usage:
  python manage.py run_agent "Find 20 plumbing leads in Queens NY"
  python manage.py run_agent --agent discovery "Find data sources for Texas contractor licenses"
  python manage.py run_agent --list
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Run a SalesSignal AI agent'

    def add_arguments(self, parser):
        parser.add_argument('goal', nargs='?', default='', help='The goal/command for the agent')
        parser.add_argument('--agent', type=str, default='orchestrator', help='Agent name (orchestrator, discovery)')
        parser.add_argument('--list', action='store_true', help='List available agents')

    def handle(self, *args, **options):
        from core.agents import list_agents, get_agent
        from core.models.leads import AgentMission

        if options['list']:
            self.stdout.write("\n=== Available Agents ===\n")
            for name, desc in list_agents().items():
                self.stdout.write(f"  {name}: {desc[:80]}")
            return

        goal = options['goal']
        if not goal:
            self.stdout.write(self.style.ERROR("Please provide a goal. Usage: python manage.py run_agent \"Find leads in Queens\""))
            return

        agent_name = options['agent']
        self.stdout.write(f"\nDeploying {agent_name} agent...")
        self.stdout.write(f"Goal: {goal}\n")

        mission = AgentMission.objects.create(
            agent_name=agent_name, goal=goal, status='running',
            triggered_by='cli', started_at=timezone.now(),
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

            self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
            self.stdout.write(self.style.SUCCESS("  MISSION COMPLETE"))
            self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))
            self.stdout.write(result or "(no result)")
            self.stdout.write(f"\nSteps: {mission.steps_taken} | Mission ID: {mission.id}")

        except Exception as e:
            mission.status = 'error'
            mission.result = str(e)
            mission.completed_at = timezone.now()
            mission.save()
            self.stdout.write(self.style.ERROR(f"\nAgent error: {e}"))
