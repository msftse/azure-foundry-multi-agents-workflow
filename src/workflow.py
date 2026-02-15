"""GroupChat workflow — builds and configures the multi-agent group chat."""

from agent_framework import Agent, Message, Workflow
from agent_framework.orchestrations import GroupChatBuilder
from agent_framework_orchestrations._group_chat import (
    AgentBasedGroupChatOrchestrator,
    AgentExecutor,
    ParticipantRegistry,
)


def _termination_condition(messages: list[Message]) -> bool:
    """Terminate when the last message contains 'DONE'."""
    if not messages:
        return False
    last = messages[-1]
    return "DONE" in (last.text or "")


def build_group_chat(
    participants: list[Agent],
    orchestrator: Agent,
    max_rounds: int = 10,
    retry_attempts: int = 3,
) -> Workflow:
    """Build a GroupChat workflow with the given participants and orchestrator.

    Args:
        participants: List of specialist agents (Slack, Jira, GitHub).
        orchestrator: The orchestrator agent for intelligent speaker selection.
        max_rounds: Maximum conversation rounds before termination.
        retry_attempts: Retry count for orchestrator JSON parsing failures
            (needed when AzureAIClient wraps JSON in markdown fences).

    Returns:
        A Workflow ready to run.
    """
    # Build participant executors (same as GroupChatBuilder._resolve_participants)
    executors = [AgentExecutor(p) for p in participants]
    registry = ParticipantRegistry(executors)

    # Create the orchestrator with retry_attempts to handle JSON parsing issues
    orch = AgentBasedGroupChatOrchestrator(
        agent=orchestrator,
        participant_registry=registry,
        max_rounds=max_rounds,
        termination_condition=_termination_condition,
        retry_attempts=retry_attempts,
    )

    builder = GroupChatBuilder(
        participants=participants,
        orchestrator=orch,
    )
    return builder.build()
