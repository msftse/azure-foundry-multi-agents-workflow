"""Agent factories for Azure AI Foundry deployment.

Creates all agents via AzureAIProjectAgentProvider, registering them as
persistent versioned resources in Foundry.

For *hosted* deployment, Slack, Jira, and GitHub agents use native MCP tool
definitions (type: "mcp") so the Foundry runtime connects to the remote
MCP servers directly.  GitHub uses a project connection for PAT auth.

For *local* runs (main.py), the original RawMCPSseTool / RawMCPStdioTool
wrappers are used instead — see src/agents/*.py.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.azure import AzureAIProjectAgentProvider

from src.config import Config
from src.prompts.github import GITHUB_AGENT_DESCRIPTION, GITHUB_AGENT_INSTRUCTIONS, GITHUB_AGENT_NAME
from src.prompts.jira import JIRA_AGENT_DESCRIPTION, JIRA_AGENT_INSTRUCTIONS, JIRA_AGENT_NAME
from src.prompts.orchestrator import (
    ORCHESTRATOR_AGENT_DESCRIPTION,
    ORCHESTRATOR_AGENT_INSTRUCTIONS,
    ORCHESTRATOR_AGENT_NAME,
)
from src.prompts.slack import SLACK_AGENT_DESCRIPTION, SLACK_AGENT_INSTRUCTIONS, SLACK_AGENT_NAME

# Foundry project connection name that stores the GitHub PAT
# (created via ARM API — stores Authorization: Bearer <PAT> as a custom key)
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


async def create_all_agents(
    provider: AzureAIProjectAgentProvider,
    config: Config,
    model: str | None = None,
) -> tuple[
    list[Agent],  # [slack, jira] participants
    Agent,  # orchestrator
    list,  # mcp_tools to close later (empty for hosted mode)
]:
    """Create agents via the Foundry provider using native MCP tools.

    Returns:
        (participants, orchestrator, mcp_tools)
    """
    # -- Register agents with native MCP tool definitions --
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

    orchestrator = await provider.create_agent(
        name=ORCHESTRATOR_AGENT_NAME,
        model=model,
        instructions=ORCHESTRATOR_AGENT_INSTRUCTIONS,
        description=ORCHESTRATOR_AGENT_DESCRIPTION,
    )

    participants = [slack_agent, jira_agent, github_agent]
    mcp_tools: list = []  # No local MCP connections needed for hosted mode

    return participants, orchestrator, mcp_tools
