"""The Discovery agent — finds NEW public data sources for lead generation."""
from core.agents.base import BaseAgent
from core.agents.registry import register_agent
from core.agents.tools import TOOL_DEFINITIONS

SEARCH_TOOLS = {"search_data_gov", "search_socrata_portal", "fetch_socrata_data", "fetch_webpage", "check_lead_count"}


@register_agent
class DiscoveryAgent(BaseAgent):
    name = "discovery"
    description = (
        "You are the Discovery Agent for SalesSignal AI.\n\n"
        "Your job is to find NEW public data sources that could provide business leads.\n"
        "Search data.gov, state/city open data portals, and government websites for:\n"
        "- Building permits and violations\n"
        "- Business licenses and filings\n"
        "- Health inspections\n"
        "- Contractor licenses\n"
        "- Code enforcement actions\n"
        "- Property transactions\n\n"
        "Known Socrata portals:\n"
        "- data.cityofnewyork.us, data.lacity.org, data.cityofchicago.org\n"
        "- data.sfgov.org, data.texas.gov, data.ca.gov, data.ny.gov\n\n"
        "For each source found, report:\n"
        "1. URL and format (API, CSV, JSON)\n"
        "2. Whether the data is accessible\n"
        "3. Available fields (names, addresses, phones, dates)\n"
        "4. Relevance for lead generation (HIGH/MEDIUM/LOW)\n"
        "5. Estimated record count\n\n"
        "Do NOT create monitors — just report findings for the admin to decide."
    )
    model = "claude-sonnet-4-20250514"
    max_steps = 25

    def _register_tools(self):
        self.tools = {n: i for n, i in TOOL_DEFINITIONS.items() if n in SEARCH_TOOLS}
