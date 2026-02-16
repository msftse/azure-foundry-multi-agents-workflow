"""Evaluation runner for the multi-agent group chat workflow.

Evaluates orchestrator routing accuracy and tool call accuracy using the
Azure AI Evaluation SDK's built-in agentic evaluators:
  - IntentResolutionEvaluator  — Does the orchestrator resolve user intent?
  - ToolCallAccuracyEvaluator  — Does the sub-agent pick the right tools?
  - TaskAdherenceEvaluator     — Does the agent follow its instructions?

Usage:
    # Run all evaluations (requires a deployed Foundry workflow or local GroupChat)
    python -m evaluation.run_evaluation

    # Evaluate orchestrator routing only (no live agent needed)
    python -m evaluation.run_evaluation --routing-only

    # Log results to Azure AI Foundry
    python -m evaluation.run_evaluation --log-to-foundry
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
EVAL_DIR = Path(__file__).parent
DATA_PATH = EVAL_DIR / "evaluation_data.jsonl"
TOOL_DEFS_PATH = EVAL_DIR / "tool_definitions.json"
OUTPUT_PATH = EVAL_DIR / "evaluation_results.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_dataset(path: Path) -> list[dict]:
    """Load JSONL evaluation dataset."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_tool_definitions(path: Path) -> dict[str, list[dict]]:
    """Load tool definitions keyed by agent name."""
    with open(path) as f:
        return json.load(f)


def get_all_tool_definitions(tool_defs: dict[str, list[dict]]) -> list[dict]:
    """Flatten all tool definitions into a single list."""
    all_tools = []
    for tools in tool_defs.values():
        all_tools.extend(tools)
    return all_tools


def get_model_config() -> dict:
    """Build model config dict for evaluators.

    Prefers AZURE_OPENAI_CHAT_DEPLOYMENT_NAME (the actual deployment in
    the Azure OpenAI resource), falling back to AZURE_AI_MODEL_DEPLOYMENT_NAME.
    """
    deployment = (
        os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
        or os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or "gpt-4o"
    )
    return {
        "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
        "api_key": os.environ["AZURE_OPENAI_API_KEY"],
        "azure_deployment": deployment,
        "api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
    }


def get_azure_ai_project() -> str | None:
    """Return the Azure AI project endpoint for logging results to Foundry.

    The ``evaluate()`` function in azure-ai-evaluation >= 1.5 accepts the
    project endpoint URL directly as a string, which avoids the need to
    resolve the underlying ML workspace triad (subscription / resource group
    / workspace name).  Format:
        https://{resource_name}.services.ai.azure.com/api/projects/{project_name}
    """
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    return endpoint or None


# ---------------------------------------------------------------------------
# Orchestrator routing evaluation (offline — no live agent needed)
# ---------------------------------------------------------------------------


def evaluate_routing(dataset: list[dict], model_config: dict) -> dict:
    """Evaluate orchestrator routing accuracy.

    For each test case, we simulate the orchestrator's expected behavior:
    the query should be routed to ``expected_agent``.  We use
    ``IntentResolutionEvaluator`` with the query and a synthetic response
    that states the routing decision, so the evaluator checks whether the
    intent was correctly identified.
    """
    from azure.ai.evaluation import IntentResolutionEvaluator

    evaluator = IntentResolutionEvaluator(model_config=model_config)

    results = []
    for i, row in enumerate(dataset):
        query = row["query"]
        expected = row["expected_agent"]

        # Simulate the orchestrator response (it replies with one word)
        response = expected

        try:
            result = evaluator(query=query, response=response)
            result["_query"] = query
            result["_expected_agent"] = expected
            result["_index"] = i
            results.append(result)
            status = result.get("intent_resolution_result", "?")
            score = result.get("intent_resolution", "?")
            print(f"  [{i + 1:02d}/{len(dataset)}] {status:4s} (score={score}) | {expected:12s} | {query[:60]}")
        except Exception as e:
            print(f"  [{i + 1:02d}/{len(dataset)}] ERROR | {expected:12s} | {query[:60]} — {e}")
            results.append({"_query": query, "_expected_agent": expected, "_index": i, "_error": str(e)})

    # Aggregate
    scores = [r.get("intent_resolution", 0) for r in results if "intent_resolution" in r]
    passes = [r for r in results if r.get("intent_resolution_result") == "pass"]
    total_evaluated = len(scores)

    summary = {
        "total": len(dataset),
        "evaluated": total_evaluated,
        "passed": len(passes),
        "failed": total_evaluated - len(passes),
        "pass_rate": round(len(passes) / total_evaluated, 3) if total_evaluated else 0,
        "avg_score": round(sum(scores) / total_evaluated, 2) if total_evaluated else 0,
    }
    return {"summary": summary, "rows": results}


# ---------------------------------------------------------------------------
# Tool call accuracy evaluation (offline — uses expected tool calls)
# ---------------------------------------------------------------------------


def evaluate_tool_calls(dataset: list[dict], tool_defs: dict[str, list[dict]], model_config: dict) -> dict:
    """Evaluate tool call accuracy.

    For each test case, we construct a synthetic ``tool_calls`` list from
    the ``expected_tools`` field and evaluate against the full tool
    definitions for the expected agent.
    """
    from azure.ai.evaluation import ToolCallAccuracyEvaluator

    evaluator = ToolCallAccuracyEvaluator(model_config=model_config)
    all_tool_defs = get_all_tool_definitions(tool_defs)

    results = []
    for i, row in enumerate(dataset):
        query = row["query"]
        expected_agent = row["expected_agent"]
        expected_tools = row.get("expected_tools", [])

        if not expected_tools:
            continue

        # Build synthetic tool_calls in the format the evaluator expects
        expected_args = row.get("expected_arguments", [])
        tool_calls = [
            {
                "type": "tool_call",
                "tool_call_id": f"call_{j}",
                "name": tool_name,
                "arguments": expected_args[j] if j < len(expected_args) else {},
            }
            for j, tool_name in enumerate(expected_tools)
        ]

        # Use the agent's tool definitions
        agent_tool_defs = tool_defs.get(expected_agent, all_tool_defs)

        try:
            result = evaluator(
                query=query,
                tool_calls=tool_calls,
                tool_definitions=agent_tool_defs,
            )
            result["_query"] = query
            result["_expected_agent"] = expected_agent
            result["_expected_tools"] = expected_tools
            result["_index"] = i
            results.append(result)
            status = result.get("tool_call_accuracy_result", "?")
            score = result.get("tool_call_accuracy", "?")
            print(f"  [{i + 1:02d}/{len(dataset)}] {status:4s} (score={score}) | {expected_agent:12s} | {query[:60]}")
        except Exception as e:
            print(f"  [{i + 1:02d}/{len(dataset)}] ERROR | {expected_agent:12s} | {query[:60]} — {e}")
            results.append(
                {
                    "_query": query,
                    "_expected_agent": expected_agent,
                    "_expected_tools": expected_tools,
                    "_index": i,
                    "_error": str(e),
                }
            )
        # Brief pause between rows to avoid rate-limit (429) errors
        time.sleep(1)

    # Aggregate
    scores = [r.get("tool_call_accuracy", 0) for r in results if "tool_call_accuracy" in r]
    passes = [r for r in results if r.get("tool_call_accuracy_result") == "pass"]
    total_evaluated = len(scores)

    summary = {
        "total": len(dataset),
        "evaluated": total_evaluated,
        "passed": len(passes),
        "failed": total_evaluated - len(passes),
        "pass_rate": round(len(passes) / total_evaluated, 3) if total_evaluated else 0,
        "avg_score": round(sum(scores) / total_evaluated, 2) if total_evaluated else 0,
    }
    return {"summary": summary, "rows": results}


# ---------------------------------------------------------------------------
# Batch evaluation using the SDK evaluate() function
# ---------------------------------------------------------------------------


def run_batch_evaluation(
    dataset: list[dict],
    tool_defs: dict[str, list[dict]],
    model_config: dict,
    log_to_foundry: bool = False,
) -> dict:
    """Run both evaluators and optionally log to Foundry.

    IntentResolutionEvaluator runs via the SDK ``evaluate()`` function
    (supports JSONL with string columns and Foundry logging).

    ToolCallAccuracyEvaluator runs per-row because it requires complex
    types (lists of dicts) that cannot be serialized through JSONL
    column mapping.
    """
    from azure.ai.evaluation import (
        IntentResolutionEvaluator,
        ToolCallAccuracyEvaluator,
        evaluate,
    )

    all_tool_defs = get_all_tool_definitions(tool_defs)

    # --- Intent Resolution via evaluate() (supports Foundry logging) ---
    batch_data = []
    for row in dataset:
        batch_data.append(
            {
                "query": row["query"],
                "response": row.get("expected_agent", ""),
            }
        )

    batch_path = EVAL_DIR / "_batch_data.jsonl"
    with open(batch_path, "w") as f:
        for item in batch_data:
            f.write(json.dumps(item) + "\n")

    evaluate_kwargs: dict = {
        "data": str(batch_path),
        "evaluators": {
            "intent_resolution": IntentResolutionEvaluator(model_config=model_config),
        },
        "evaluator_config": {
            "intent_resolution": {
                "column_mapping": {
                    "query": "${data.query}",
                    "response": "${data.response}",
                },
            },
        },
        "output_path": str(OUTPUT_PATH),
    }

    if log_to_foundry:
        project = get_azure_ai_project()
        if project:
            evaluate_kwargs["azure_ai_project"] = project
            print("  Logging results to Azure AI Foundry project.")
        else:
            print("  Warning: AZURE_AI_PROJECT_ENDPOINT not set; skipping Foundry logging.")

    print("\n[1/2] Running IntentResolutionEvaluator (batch)...")
    intent_result = evaluate(**evaluate_kwargs)
    batch_path.unlink(missing_ok=True)

    # --- Tool Call Accuracy (per-row) ---
    print("\n[2/2] Running ToolCallAccuracyEvaluator (per-row)...")
    tool_evaluator = ToolCallAccuracyEvaluator(model_config=model_config)
    tool_results = []

    for i, row in enumerate(dataset):
        expected_tools = row.get("expected_tools", [])
        expected_agent = row.get("expected_agent", "")
        if not expected_tools:
            continue

        expected_args = row.get("expected_arguments", [])
        tool_calls = [
            {
                "type": "tool_call",
                "tool_call_id": f"call_{j}",
                "name": t,
                "arguments": expected_args[j] if j < len(expected_args) else {},
            }
            for j, t in enumerate(expected_tools)
        ]
        agent_tool_defs = tool_defs.get(expected_agent, all_tool_defs)

        try:
            result = tool_evaluator(
                query=row["query"],
                tool_calls=tool_calls,
                tool_definitions=agent_tool_defs,
            )
            status = result.get("tool_call_accuracy_result", "?")
            score = result.get("tool_call_accuracy", "?")
            print(
                f"  [{i + 1:02d}/{len(dataset)}] {status:4s} (score={score}) | {expected_agent:12s} | {row['query'][:60]}"
            )
            tool_results.append(result)
        except Exception as e:
            print(f"  [{i + 1:02d}/{len(dataset)}] ERROR | {expected_agent:12s} | {row['query'][:60]} — {e}")
            tool_results.append({"_error": str(e)})
        # Brief pause between rows to avoid rate-limit (429) errors
        time.sleep(1)

    # Aggregate tool call results
    tool_scores = [r.get("tool_call_accuracy", 0) for r in tool_results if "tool_call_accuracy" in r]
    tool_passes = sum(1 for r in tool_results if r.get("tool_call_accuracy_result") == "pass")
    tool_total = len(tool_scores)

    return {
        "intent_resolution": intent_result,
        "tool_call_accuracy": {
            "total": len(dataset),
            "evaluated": tool_total,
            "passed": tool_passes,
            "failed": tool_total - tool_passes,
            "pass_rate": round(tool_passes / tool_total, 3) if tool_total else 0,
            "avg_score": round(sum(tool_scores) / tool_total, 2) if tool_total else 0,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Evaluate multi-agent workflow")
    parser.add_argument(
        "--routing-only",
        action="store_true",
        help="Only evaluate orchestrator routing (IntentResolution), no tool call eval.",
    )
    parser.add_argument(
        "--tool-calls-only",
        action="store_true",
        help="Only evaluate tool call accuracy.",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Use the SDK evaluate() function for batch evaluation.",
    )
    parser.add_argument(
        "--log-to-foundry",
        action="store_true",
        help="Log evaluation results to Azure AI Foundry (requires project config).",
    )
    args = parser.parse_args()

    # Validate env
    required_vars = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}")
        print("Set them in .env or export them before running.")
        sys.exit(1)

    # Load data
    print(f"Loading dataset from {DATA_PATH}")
    dataset = load_dataset(DATA_PATH)
    print(f"  {len(dataset)} test cases loaded")

    print(f"Loading tool definitions from {TOOL_DEFS_PATH}")
    tool_defs = load_tool_definitions(TOOL_DEFS_PATH)
    for agent, tools in tool_defs.items():
        print(f"  {agent}: {len(tools)} tools")

    model_config = get_model_config()
    print(f"  Judge model: {model_config['azure_deployment']}\n")

    # Run batch mode
    if args.batch:
        result = run_batch_evaluation(dataset, tool_defs, model_config, args.log_to_foundry)
        print("\n" + "=" * 60)
        print("EVALUATION RESULTS")
        print("=" * 60)

        # Intent resolution (from evaluate())
        intent = result.get("intent_resolution", {})
        if hasattr(intent, "get"):
            metrics = intent.get("metrics", {})
            if metrics:
                print("\nIntent Resolution:")
                pprint(metrics)
            if "studio_url" in intent:
                print(f"\nView in Foundry: {intent['studio_url']}")

        # Tool call accuracy (aggregated)
        tool = result.get("tool_call_accuracy", {})
        if tool:
            print("\nTool Call Accuracy:")
            pprint(tool)
        return

    # Run individual evaluators
    all_results = {}

    if not args.tool_calls_only:
        print("=" * 60)
        print("ORCHESTRATOR ROUTING — IntentResolutionEvaluator")
        print("=" * 60)
        routing_results = evaluate_routing(dataset, model_config)
        all_results["routing"] = routing_results
        print(f"\n  Summary: {routing_results['summary']}\n")

    if not args.routing_only:
        print("=" * 60)
        print("TOOL CALL ACCURACY — ToolCallAccuracyEvaluator")
        print("=" * 60)
        tool_results = evaluate_tool_calls(dataset, tool_defs, model_config)
        all_results["tool_calls"] = tool_results
        print(f"\n  Summary: {tool_results['summary']}\n")

    # Save results
    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"Results saved to {OUTPUT_PATH}")

    # Print category breakdown
    for eval_name, eval_data in all_results.items():
        rows = eval_data.get("rows", [])
        if not rows:
            continue

        # Group by agent
        by_agent: dict[str, list] = {}
        for r in rows:
            agent = r.get("_expected_agent", "unknown")
            by_agent.setdefault(agent, []).append(r)

        score_key = "intent_resolution" if eval_name == "routing" else "tool_call_accuracy"
        result_key = f"{score_key}_result"

        print(f"\n  {eval_name.upper()} — Breakdown by agent:")
        for agent, agent_rows in sorted(by_agent.items()):
            scores = [r.get(score_key, 0) for r in agent_rows if score_key in r]
            passes = sum(1 for r in agent_rows if r.get(result_key) == "pass")
            total = len(scores)
            avg = round(sum(scores) / total, 2) if total else 0
            rate = round(passes / total, 3) if total else 0
            print(f"    {agent:12s}: {passes}/{total} passed (rate={rate}, avg_score={avg})")


if __name__ == "__main__":
    import multiprocessing
    import contextlib

    with contextlib.suppress(RuntimeError):
        multiprocessing.set_start_method("spawn", force=True)

    main()
