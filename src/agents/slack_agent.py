"""Slack agent with MCP tool connected to APIM-hosted Slack MCP server via SSE."""

from agent_framework import Agent

from src.config import SlackConfig
from src.prompts.slack import SLACK_AGENT_DESCRIPTION, SLACK_AGENT_INSTRUCTIONS, SLACK_AGENT_NAME
from src.tools.raw_mcp_sse import RawMCPSseTool


def create_slack_mcp_tool(config: SlackConfig) -> RawMCPSseTool:
    """Create RawMCPSseTool for the APIM-hosted Slack MCP server."""
    return RawMCPSseTool(
        name="slack_mcp",
        url=config.mcp_sse_url,
    )


async def create_slack_agent(chat_client, config: SlackConfig) -> tuple[Agent, RawMCPSseTool]:
    """Create the Slack agent with its MCP tool.

    This is async because RawMCPSseTool must be connected first and its
    .functions passed directly to the Agent (it's not an MCPTool subclass).

    Returns (agent, mcp_tool) — caller must close mcp_tool when done.
    """
    mcp_tool = create_slack_mcp_tool(config)
    await mcp_tool.connect()

    agent = Agent(
        client=chat_client,
        instructions=SLACK_AGENT_INSTRUCTIONS,
        name=SLACK_AGENT_NAME,
        description=SLACK_AGENT_DESCRIPTION,
        tools=mcp_tool.functions,
    )
    return agent, mcp_tool
