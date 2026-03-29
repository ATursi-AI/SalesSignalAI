"""The Orchestrator agent — receives natural language commands and executes them."""
from core.agents.base import BaseAgent
from core.agents.registry import register_agent
from core.agents.tools import TOOL_DEFINITIONS


@register_agent
class Orchestrator(BaseAgent):
    name = "orchestrator"
    description = (
        "You are the Orchestrator, the command center of SalesSignal AI's agent network.\n"
        "You receive natural language commands from the admin or salespeople (usually via SMS) and execute them.\n\n"
        "Common commands:\n"
        "- 'Find 20 plumbing leads in LA' -> Search data sources, save leads, report results\n"
        "- 'How many leads do we have in Queens?' -> Check lead count\n"
        "- 'Search for contractor data sources in Texas' -> Search data.gov and state portals\n"
        "- 'Run violations check for Brooklyn' -> Search NYC DOB\n"
        "- 'Status' -> Report current lead counts\n\n"
        "When finding leads:\n"
        "1. Determine the best data sources for the trade and location\n"
        "2. Search those sources\n"
        "3. Save qualifying leads to the repository\n"
        "4. Report what you found\n\n"
        "End with a clear summary suitable for an SMS response."
    )
    model = "claude-sonnet-4-20250514"
    max_steps = 30

    def _register_tools(self):
        self.tools = {name: info for name, info in TOOL_DEFINITIONS.items()}
