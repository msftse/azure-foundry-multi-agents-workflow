"""Parallel orchestrator agent — two-phase: routing (fan-out) and synthesis (fan-in)."""

from agent_framework import Agent

from src.prompts.parallel_orchestrator import (
    PARALLEL_ORCHESTRATOR_AGENT_DESCRIPTION,
    PARALLEL_ORCHESTRATOR_AGENT_NAME,
    PARALLEL_ORCHESTRATOR_ROUTING_INSTRUCTIONS,
    PARALLEL_ORCHESTRATOR_SYNTHESIS_INSTRUCTIONS,
)


def create_parallel_orchestrator_routing_agent(chat_client) -> Agent:
    """Create the routing-phase orchestrator (decides which agents to fan-out to)."""
    return Agent(
        client=chat_client,
        instructions=PARALLEL_ORCHESTRATOR_ROUTING_INSTRUCTIONS,
        name=PARALLEL_ORCHESTRATOR_AGENT_NAME,
        description=PARALLEL_ORCHESTRATOR_AGENT_DESCRIPTION,
    )


def create_parallel_orchestrator_synthesis_agent(chat_client) -> Agent:
    """Create the synthesis-phase orchestrator (combines all agent results)."""
    return Agent(
        client=chat_client,
        instructions=PARALLEL_ORCHESTRATOR_SYNTHESIS_INSTRUCTIONS,
        name=f"{PARALLEL_ORCHESTRATOR_AGENT_NAME}Synthesizer",
        description="Synthesizes results from multiple agents into a final answer.",
    )
