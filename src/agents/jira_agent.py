"""Jira agent with MCP tool connected to APIM-hosted Jira MCP server via SSE."""

from agent_framework import Agent

from src.config import JiraConfig
from src.prompts.jira import JIRA_AGENT_DESCRIPTION, JIRA_AGENT_INSTRUCTIONS, JIRA_AGENT_NAME
from src.tools.raw_mcp_sse import RawMCPSseTool


def create_jira_mcp_tool(config: JiraConfig) -> RawMCPSseTool:
    """Create RawMCPSseTool for the APIM-hosted Jira MCP server."""
    return RawMCPSseTool(
        name="jira_mcp",
        url=config.mcp_sse_url,
    )


async def create_jira_agent(chat_client, config: JiraConfig) -> tuple[Agent, RawMCPSseTool]:
    """Create the Jira agent with its MCP tool.

    This is async because RawMCPSseTool must be connected first and its
    .functions passed directly to the Agent (it's not an MCPTool subclass).

    Returns (agent, mcp_tool) — caller must close mcp_tool when done.
    """
    mcp_tool = create_jira_mcp_tool(config)
    await mcp_tool.connect()

    agent = Agent(
        client=chat_client,
        instructions=JIRA_AGENT_INSTRUCTIONS,
        name=JIRA_AGENT_NAME,
        description=JIRA_AGENT_DESCRIPTION,
        tools=mcp_tool.functions,
    )
    return agent, mcp_tool
