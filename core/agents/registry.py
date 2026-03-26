"""Central registry of all available agents."""

_AGENTS = {}


def register_agent(agent_class):
    _AGENTS[agent_class.name] = agent_class
    return agent_class


def get_agent(name):
    if name not in _AGENTS:
        raise ValueError(f"Unknown agent: {name}. Available: {list(_AGENTS.keys())}")
    return _AGENTS[name]()


def list_agents():
    return {name: cls.description for name, cls in _AGENTS.items()}
