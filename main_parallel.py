"""Parallel workflow entrypoint — fan-out to multiple agents, fan-in to synthesize."""

import asyncio
import sys

from agent_framework.azure import AzureOpenAIChatClient

from src.agents.slack_agent import create_slack_agent
from src.agents.jira_agent import create_jira_agent
from src.agents.github_agent import create_github_agent
from src.agents.parallel_orchestrator import (
    create_parallel_orchestrator_routing_agent,
    create_parallel_orchestrator_synthesis_agent,
)
from src.config import Config, load_config
from src.parallel_workflow import run_parallel_workflow


def get_chat_client(config: Config) -> AzureOpenAIChatClient:
    """Create AzureOpenAI chat client from config."""
    return AzureOpenAIChatClient(
        endpoint=config.azure_openai.endpoint,
        api_key=config.azure_openai.api_key,
        deployment_name=config.azure_openai.deployment_name,
        api_version=config.azure_openai.api_version,
    )


async def run(task: str, config: Config):
    """Run the parallel multi-agent workflow with the given task."""
    chat_client = get_chat_client(config)

    # Create orchestrator agents (routing + synthesis)
    routing_orchestrator = create_parallel_orchestrator_routing_agent(chat_client)
    synthesis_orchestrator = create_parallel_orchestrator_synthesis_agent(chat_client)

    # Create sub-agents (same as the sequential workflow)
    slack_agent, slack_tool = await create_slack_agent(chat_client, config.slack)
    jira_agent, jira_tool = await create_jira_agent(chat_client, config.jira)
    github_agent, github_tool = await create_github_agent(chat_client, config.github)

    try:
        agents = {
            slack_agent.name: slack_agent,
            jira_agent.name: jira_agent,
            github_agent.name: github_agent,
        }

        print(f"\n{'=' * 60}")
        print(f"Task: {task}")
        print(f"Mode: Parallel fan-out / fan-in")
        print(f"{'=' * 60}\n")

        final_answer = await run_parallel_workflow(
            task=task,
            routing_orchestrator=routing_orchestrator,
            synthesis_orchestrator=synthesis_orchestrator,
            agents=agents,
        )

        print(f"\n{'=' * 60}")
        print("FINAL ANSWER:")
        print(f"{'=' * 60}")
        print(final_answer)
        print(f"{'=' * 60}")
    finally:
        await slack_tool.close()
        await jira_tool.close()
        await github_tool.close()


async def interactive(config: Config):
    """Run in interactive mode — read tasks from stdin."""
    print("Parallel Multi-Agent Workflow (Slack + Jira + GitHub)")
    print("Type a task and press Enter. Type 'quit' to exit.\n")

    while True:
        try:
            task = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not task or task.lower() in ("quit", "exit", "q"):
            print("Bye.")
            break

        await run(task, config)
        print()


def main():
    config = load_config()

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        asyncio.run(run(task, config))
    else:
        asyncio.run(interactive(config))


if __name__ == "__main__":
    main()
