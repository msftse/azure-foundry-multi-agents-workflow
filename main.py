"""Main entrypoint for the multi-agent group chat POC."""

import asyncio
import sys

from agent_framework import Message
from agent_framework.azure import AzureOpenAIChatClient

from src.agents.slack_agent import create_slack_agent
from src.agents.jira_agent import create_jira_agent
from src.agents.github_agent import create_github_agent
from src.agents.orchestrator import create_orchestrator_agent
from src.config import Config, load_config
from src.workflow import build_group_chat


def get_chat_client(config: Config) -> AzureOpenAIChatClient:
    """Create AzureOpenAI chat client from config."""
    return AzureOpenAIChatClient(
        endpoint=config.azure_openai.endpoint,
        api_key=config.azure_openai.api_key,
        deployment_name=config.azure_openai.deployment_name,
        api_version=config.azure_openai.api_version,
    )


def _print_message(msg: Message) -> None:
    """Pretty-print a single workflow message."""
    author = msg.author_name or msg.role or "?"
    text = msg.text or ""
    if text:
        print(f"  [{author}]: {text}")


async def run(task: str, config: Config):
    """Run the multi-agent group chat with the given task."""
    chat_client = get_chat_client(config)

    # All three agents are now async — Slack & Jira use SSE, GitHub uses stdio
    orchestrator = create_orchestrator_agent(chat_client)
    slack_agent, slack_tool = await create_slack_agent(chat_client, config.slack)
    jira_agent, jira_tool = await create_jira_agent(chat_client, config.jira)
    github_agent, github_tool = await create_github_agent(chat_client, config.github)

    try:
        workflow = build_group_chat(
            participants=[slack_agent, jira_agent, github_agent],
            orchestrator=orchestrator,
            max_rounds=config.workflow.max_rounds,
        )

        print(f"\n{'=' * 60}")
        print(f"Task: {task}")
        print(f"{'=' * 60}\n")

        # Use non-streaming — GroupChat doesn't emit token-level updates
        result = await workflow.run(task, stream=False)

        # Print conversation from the output events
        for event in result:
            if event.type == "output" and isinstance(event.data, list):
                for msg in event.data:
                    if isinstance(msg, Message):
                        _print_message(msg)
            elif event.type == "group_chat":
                data = event.data
                name = getattr(data, "participant_name", None)
                ridx = getattr(data, "round_index", None)
                dtype = type(data).__name__
                if "Request" in dtype and name:
                    print(f"\n--- Round {ridx}: routing to {name} ---")
                elif "Response" in dtype and name:
                    print(f"--- Round {ridx}: {name} responded ---\n")

        print(f"\n{'=' * 60}")
        print("Workflow complete.")
        print(f"{'=' * 60}")
    finally:
        await slack_tool.close()
        await jira_tool.close()
        await github_tool.close()


async def interactive(config: Config):
    """Run in interactive mode — read tasks from stdin."""
    print("Multi-Agent Group Chat (Slack + Jira + GitHub)")
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
