"""Orchestrator agent for group chat speaker selection."""

from agent_framework import Agent

from src.prompts.orchestrator import (
    ORCHESTRATOR_AGENT_DESCRIPTION,
    ORCHESTRATOR_AGENT_INSTRUCTIONS,
    ORCHESTRATOR_AGENT_NAME,
)


def create_orchestrator_agent(chat_client) -> Agent:
    """Create the orchestrator agent for intelligent speaker selection in group chat."""
    return Agent(
        client=chat_client,
        instructions=ORCHESTRATOR_AGENT_INSTRUCTIONS,
        name=ORCHESTRATOR_AGENT_NAME,
        description=ORCHESTRATOR_AGENT_DESCRIPTION,
    )
