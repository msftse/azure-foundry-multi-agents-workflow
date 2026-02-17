"""Agent factories for Azure AI Foundry deployment — parallel fan-out/fan-in workflow.

Creates all agents via AzureAIProjectAgentProvider, including the two
orchestrator phases (routing + synthesis) and the three sub-agents.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.azure import AzureAIProjectAgentProvider

from src.config import Config
from src.prompts.github import GITHUB_AGENT_DESCRIPTION, GITHUB_AGENT_INSTRUCTIONS, GITHUB_AGENT_NAME
from src.prompts.jira import JIRA_AGENT_DESCRIPTION, JIRA_AGENT_INSTRUCTIONS, JIRA_AGENT_NAME
from src.prompts.parallel_orchestrator import (
    PARALLEL_ORCHESTRATOR_AGENT_DESCRIPTION,
    PARALLEL_ORCHESTRATOR_AGENT_NAME,
    PARALLEL_ORCHESTRATOR_ROUTING_INSTRUCTIONS,
    PARALLEL_ORCHESTRATOR_SYNTHESIS_INSTRUCTIONS,
)
from src.prompts.slack import SLACK_AGENT_DESCRIPTION, SLACK_AGENT_INSTRUCTIONS, SLACK_AGENT_NAME

# Foundry project connection name that stores the GitHub PAT
GITHUB_CONNECTION_NAME = "github-mcp-pat"


def _mcp_tool(
    label: str,
    url: str,
    project_connection_id: str | None = None,
) -> dict:
    """Build a native MCP tool definition for Foundry-hosted agents."""
    d: dict = {
        "type": "mcp",
        "server_label": label,
        "server_url": url,
        "require_approval": "never",
    }
    if project_connection_id:
        d["project_connection_id"] = project_connection_id
    return d


async def create_all_parallel_agents(
    provider: AzureAIProjectAgentProvider,
    config: Config,
    model: str | None = None,
) -> tuple[
    list[Agent],  # [slack, jira, github] participants
    Agent,  # routing orchestrator
    Agent,  # synthesis orchestrator
    list,  # mcp_tools to close later (empty for hosted mode)
]:
    """Create all agents for the parallel workflow via the Foundry provider.

    Returns:
        (participants, routing_orchestrator, synthesis_orchestrator, mcp_tools)
    """
    # -- Sub-agents with native MCP tool definitions --
    slack_agent = await provider.create_agent(
        name=SLACK_AGENT_NAME,
        model=model,
        instructions=SLACK_AGENT_INSTRUCTIONS,
        description=SLACK_AGENT_DESCRIPTION,
        tools=[_mcp_tool("slack", config.slack.mcp_sse_url)],
    )

    jira_agent = await provider.create_agent(
        name=JIRA_AGENT_NAME,
        model=model,
        instructions=JIRA_AGENT_INSTRUCTIONS,
        description=JIRA_AGENT_DESCRIPTION,
        tools=[_mcp_tool("jira", config.jira.mcp_sse_url)],
    )

    github_agent = await provider.create_agent(
        name=GITHUB_AGENT_NAME,
        model=model,
        instructions=GITHUB_AGENT_INSTRUCTIONS,
        description=GITHUB_AGENT_DESCRIPTION,
        tools=[
            _mcp_tool(
                "github",
                config.github.mcp_url,
                project_connection_id=GITHUB_CONNECTION_NAME,
            )
        ],
    )

    # -- Routing orchestrator (decides which agents to fan-out to) --
    routing_orchestrator = await provider.create_agent(
        name=PARALLEL_ORCHESTRATOR_AGENT_NAME,
        model=model,
        instructions=PARALLEL_ORCHESTRATOR_ROUTING_INSTRUCTIONS,
        description=PARALLEL_ORCHESTRATOR_AGENT_DESCRIPTION,
    )

    # -- Synthesis orchestrator (combines all agent results) --
    synthesis_orchestrator = await provider.create_agent(
        name=f"{PARALLEL_ORCHESTRATOR_AGENT_NAME}Synthesizer",
        model=model,
        instructions=PARALLEL_ORCHESTRATOR_SYNTHESIS_INSTRUCTIONS,
        description="Synthesizes results from multiple agents into a final answer.",
    )

    participants = [slack_agent, jira_agent, github_agent]
    mcp_tools: list = []

    return participants, routing_orchestrator, synthesis_orchestrator, mcp_tools
