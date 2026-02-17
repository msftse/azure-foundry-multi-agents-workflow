"""Agent definitions for the multi-agent group chat."""

from src.agents.slack_agent import create_slack_agent
from src.agents.jira_agent import create_jira_agent
from src.agents.github_agent import create_github_agent
from src.agents.orchestrator import create_orchestrator_agent
from src.agents.parallel_orchestrator import (
    create_parallel_orchestrator_routing_agent,
    create_parallel_orchestrator_synthesis_agent,
)

__all__ = [
    "create_slack_agent",
    "create_jira_agent",
    "create_github_agent",
    "create_orchestrator_agent",
    "create_parallel_orchestrator_routing_agent",
    "create_parallel_orchestrator_synthesis_agent",
]
