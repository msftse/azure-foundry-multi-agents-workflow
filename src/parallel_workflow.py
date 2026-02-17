"""Parallel fan-out/fan-in workflow — orchestrator dispatches to multiple agents
simultaneously, collects results, then synthesizes a final answer."""

import asyncio
import logging

from agent_framework import Agent, Message

logger = logging.getLogger(__name__)

# Valid agent names the routing orchestrator can return
VALID_AGENT_NAMES = {"SlackAgent", "JiraAgent", "GitHubAgent"}


async def _invoke_agent(agent: Agent, task: str) -> tuple[str, str]:
    """Invoke a single agent and return (agent_name, response_text).

    Runs the agent with stream=False and extracts the final text.
    """
    name = agent.name or "Unknown"
    try:
        result = await agent.run(task, stream=False)
        # Collect all output messages from the result events
        texts: list[str] = []
        for event in result:
            if event.type == "output" and isinstance(event.data, list):
                for msg in event.data:
                    if isinstance(msg, Message) and msg.text:
                        texts.append(msg.text)
        response = "\n".join(texts) if texts else "(no response)"
        return name, response
    except Exception as e:
        logger.error("Agent %s failed: %s", name, e)
        return name, f"(error: {e})"


def _parse_routing_response(response_text: str) -> list[str]:
    """Parse comma-separated agent names from the routing orchestrator's response.

    Returns list of valid agent names, ignoring any invalid ones.
    """
    raw = response_text.strip().replace(" ", "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    valid = [n for n in names if n in VALID_AGENT_NAMES]
    if not valid:
        logger.warning("Routing returned no valid agents from: %r", response_text)
    return valid


async def run_parallel_workflow(
    task: str,
    routing_orchestrator: Agent,
    synthesis_orchestrator: Agent,
    agents: dict[str, Agent],
) -> str:
    """Execute the parallel fan-out/fan-in workflow.

    Flow:
        1. Routing orchestrator decides which agents to invoke (can be multiple)
        2. Selected agents run in parallel via asyncio.gather
        3. Synthesis orchestrator combines all results into a final answer

    Args:
        task: The user's request.
        routing_orchestrator: Agent that decides which sub-agents to invoke.
        synthesis_orchestrator: Agent that synthesizes the combined results.
        agents: Dict mapping agent names to Agent instances
                (e.g. {"SlackAgent": ..., "JiraAgent": ..., "GitHubAgent": ...}).

    Returns:
        The final synthesized answer string.
    """
    # --- Phase 1: Routing (fan-out decision) ---
    print("\n--- Phase 1: Routing orchestrator deciding which agents to invoke ---")
    routing_result = await routing_orchestrator.run(task, stream=False)

    routing_text = ""
    for event in routing_result:
        if event.type == "output" and isinstance(event.data, list):
            for msg in event.data:
                if isinstance(msg, Message) and msg.text:
                    routing_text = msg.text
                    break

    selected_names = _parse_routing_response(routing_text)
    if not selected_names:
        return "Orchestrator could not determine which agents to invoke for this task."

    print(f"--- Routing decision: {', '.join(selected_names)} ---\n")

    # --- Phase 2: Fan-out (parallel agent execution) ---
    print(f"--- Phase 2: Invoking {len(selected_names)} agent(s) in parallel ---")
    selected_agents = []
    for name in selected_names:
        if name in agents:
            selected_agents.append(agents[name])
        else:
            logger.warning("Agent %r not found in registry, skipping", name)

    if not selected_agents:
        return "No valid agents available for the selected tasks."

    # Run all selected agents concurrently
    results = await asyncio.gather(*[_invoke_agent(agent, task) for agent in selected_agents])

    # Format results for the synthesis phase
    for agent_name, response in results:
        print(f"  [{agent_name}] responded ({len(response)} chars)")

    print(f"\n--- Phase 3: Synthesis orchestrator combining results ---")

    # --- Phase 3: Fan-in (synthesis) ---
    synthesis_prompt = _build_synthesis_prompt(task, results)
    synthesis_result = await synthesis_orchestrator.run(synthesis_prompt, stream=False)

    final_text = ""
    for event in synthesis_result:
        if event.type == "output" and isinstance(event.data, list):
            for msg in event.data:
                if isinstance(msg, Message) and msg.text:
                    final_text = msg.text
                    break

    return final_text or "(synthesis produced no output)"


def _build_synthesis_prompt(
    original_task: str,
    agent_results: list[tuple[str, str]],
) -> str:
    """Build the prompt for the synthesis orchestrator, including all agent results."""
    sections = [f"ORIGINAL USER REQUEST:\n{original_task}\n"]
    sections.append("AGENT RESULTS:")
    for agent_name, response in agent_results:
        sections.append(f"\n--- {agent_name} ---\n{response}")
    sections.append("\nPlease synthesize the above results into a single, coherent response for the user.")
    return "\n".join(sections)
