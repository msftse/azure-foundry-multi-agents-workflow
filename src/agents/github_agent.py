"""GitHub agent with MCP tool connected to github-mcp-server.

Uses RawMCPStdioTool instead of MCPStdioTool to work around the
AsyncExitStack deadlock that affects the Go-based GitHub MCP server.

Because Agent splits tools by isinstance(tool, MCPTool), and our
RawMCPStdioTool is not an MCPTool subclass, we connect the tool first
and pass its .functions directly to the Agent.
"""

import os

from agent_framework import Agent

from src.config import GitHubConfig
from src.prompts.github import GITHUB_AGENT_DESCRIPTION, GITHUB_AGENT_INSTRUCTIONS, GITHUB_AGENT_NAME
from src.tools.raw_mcp_stdio import RawMCPStdioTool

# Prefer local Go binary; fall back to Docker if not found.
_GO_BIN = os.path.expanduser("~/go/bin/github-mcp-server")


def create_github_mcp_tool(config: GitHubConfig) -> RawMCPStdioTool:
    """Create RawMCPStdioTool for github-mcp-server."""
    if os.path.isfile(_GO_BIN):
        return RawMCPStdioTool(
            name="github_mcp",
            command=_GO_BIN,
            args=["stdio"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": config.personal_access_token},
        )

    # Fallback: Docker
    return RawMCPStdioTool(
        name="github_mcp",
        command="docker",
        args=[
            "run",
            "-i",
            "--rm",
            "-e",
            f"GITHUB_PERSONAL_ACCESS_TOKEN={config.personal_access_token}",
            "ghcr.io/github/github-mcp-server",
            "stdio",
        ],
        env={},
    )


async def create_github_agent(chat_client, config: GitHubConfig) -> tuple[Agent, RawMCPStdioTool]:
    """Create the GitHub agent with its MCP tool.

    This is async because we must connect the tool first and pass its
    .functions to the Agent (RawMCPStdioTool is not an MCPTool subclass,
    so the Agent can't auto-expand it).

    Returns (agent, mcp_tool) — caller must close mcp_tool when done.
    """
    mcp_tool = create_github_mcp_tool(config)
    await mcp_tool.connect()

    agent = Agent(
        client=chat_client,
        instructions=GITHUB_AGENT_INSTRUCTIONS,
        name=GITHUB_AGENT_NAME,
        description=GITHUB_AGENT_DESCRIPTION,
        tools=mcp_tool.functions,
    )
    return agent, mcp_tool
