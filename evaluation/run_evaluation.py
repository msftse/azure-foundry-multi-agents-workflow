"""Agent target evaluation for the multi-agent group chat workflow.

Sends queries to the live MultiAgentGroupChat workflow agent and evaluates
the responses using the new Foundry cloud evaluation API.  Results appear
under the workflow's **Evaluation tab** in the new Foundry portal (not the
project-level Evaluations sidebar).

Evaluators:
  - builtin.intent_resolution  — Does the agent correctly resolve user intent?
  - builtin.tool_call_accuracy — Does the agent pick the right MCP tools?
  - builtin.task_adherence     — Does the response adhere to the agent's tasks?

Usage:
    python -m evaluation.run_evaluation
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
EVAL_DIR = Path(__file__).parent
AGENT_EVAL_DATA_PATH = EVAL_DIR / "agent_eval_data.jsonl"
TOOL_DEFINITIONS_PATH = EVAL_DIR / "tool_definitions.json"


def _load_tool_definitions() -> list[dict]:
    """Flatten per-agent tool defs into a single OpenAI function-calling list.

    The cloud `builtin.tool_call_accuracy` evaluator requires a
    `tool_definitions` data mapping. The schema is the OpenAI
    function-calling shape: `[{"type": "function", "function": {...}}, ...]`.
    Our `tool_definitions.json` stores raw function descriptors grouped by
    agent name, so wrap each one before returning.
    """
    with open(TOOL_DEFINITIONS_PATH) as f:
        per_agent = json.load(f)
    tools: list[dict] = []
    for _, descriptors in per_agent.items():
        for descriptor in descriptors:
            tools.append({"type": "function", "function": descriptor})
    return tools


def _build_dataset_with_tools(source_path: Path, tools: list[dict], out_path: Path) -> int:
    """Copy `source_path` to `out_path`, injecting a `tool_definitions` field
    into every row. Returns the number of rows written."""
    count = 0
    with open(source_path) as f_in, open(out_path, "w") as f_out:
        for line in f_in:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            row["tool_definitions"] = tools
            f_out.write(json.dumps(row) + "\n")
            count += 1
    return count


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AGENT_NAME = "MultiAgentGroupChat"
# Omit version to use latest


def get_deployment_name() -> str:
    """Get the model deployment name for the LLM judge."""
    return os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "gpt-4o")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_agent_target_evaluation():
    """Run agent target evaluation using the new Foundry cloud API.

    This sends each query to the live MultiAgentGroupChat workflow agent,
    collects its responses (including tool calls), and evaluates them with
    built-in evaluators.  Results are linked to the agent/workflow so they
    appear under the workflow's Evaluation tab in the portal.
    """
    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    from openai.types.eval_create_params import DataSourceConfigCustom

    endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
    deployment_name = get_deployment_name()

    print(f"Endpoint : {endpoint}")
    print(f"Judge    : {deployment_name}")
    print(f"Agent    : {AGENT_NAME}")
    print(f"Data     : {AGENT_EVAL_DATA_PATH}")

    # ── Load query-only data ─────────────────────────────────────
    with open(AGENT_EVAL_DATA_PATH) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    print(f"Queries  : {len(rows)}")

    # ── Load + flatten tool definitions ──────────────────────────
    tools = _load_tool_definitions()
    print(f"Tools    : {len(tools)} (OpenAI function-calling format)")

    # ── Connect ──────────────────────────────────────────────────
    print("\nConnecting to Azure AI Foundry...")
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=endpoint, credential=credential)
    client = project_client.get_openai_client()
    print("  Connected.")

    # ── Build + upload augmented dataset (query + tool_definitions) ──
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    augmented_path = EVAL_DIR / f"agent_eval_data_with_tools_{ts}.jsonl"
    row_count = _build_dataset_with_tools(AGENT_EVAL_DATA_PATH, tools, augmented_path)
    print(f"  Wrote augmented dataset: {augmented_path.name} ({row_count} rows)")

    dataset = project_client.datasets.upload_file(
        name=f"agent-eval-{ts}",
        version="1",
        file_path=str(augmented_path),
    )
    data_id = dataset.id
    print(f"  Uploaded dataset: {data_id}")

    # ── Data source config ───────────────────────────────────────
    # Only 'query' is in the input data.  Agent generates responses at runtime.
    # include_sample_schema=True enables {{sample.*}} references for agent output.
    data_source_config = DataSourceConfigCustom(
        type="custom",
        item_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tool_definitions": {"type": "array"},
            },
            "required": ["query"],
        },
        include_sample_schema=True,
    )

    # ── Testing criteria (evaluators) ────────────────────────────
    # {{sample.output_text}}  = agent's plain-text response
    # {{sample.output_items}} = structured JSON output incl. tool calls
    testing_criteria = [
        {
            "type": "azure_ai_evaluator",
            "name": "intent_resolution",
            "evaluator_name": "builtin.intent_resolution",
            "initialization_parameters": {"deployment_name": deployment_name},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{sample.output_text}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "tool_call_accuracy",
            "evaluator_name": "builtin.tool_call_accuracy",
            "initialization_parameters": {"deployment_name": deployment_name},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{sample.output_items}}",
                "tool_definitions": "{{item.tool_definitions}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "task_adherence",
            "evaluator_name": "builtin.task_adherence",
            "initialization_parameters": {"deployment_name": deployment_name},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{sample.output_items}}",
            },
        },
    ]

    # ── Create eval ──────────────────────────────────────────────
    print("\nCreating evaluation...")
    eval_object = client.evals.create(
        name=f"MultiAgent Workflow Evaluation ({ts})",
        data_source_config=data_source_config,
        testing_criteria=testing_criteria,  # type: ignore[arg-type]
    )
    print(f"  Eval ID: {eval_object.id}")

    # ── Data source with agent target ────────────────────────────
    # input_messages: template that sends each query to the agent
    # target: the workflow agent to evaluate
    data_source = {
        "type": "azure_ai_target_completions",
        "source": {
            "type": "file_id",
            "id": data_id,
        },
        "input_messages": {
            "type": "template",
            "template": [
                {
                    "type": "message",
                    "role": "user",
                    "content": {
                        "type": "input_text",
                        "text": "{{item.query}}",
                    },
                },
            ],
        },
        "target": {
            "type": "azure_ai_agent",
            "name": AGENT_NAME,
            # version omitted → uses latest
        },
    }

    # ── Create run ───────────────────────────────────────────────
    print("Creating evaluation run (agent will process each query)...")
    eval_run = client.evals.runs.create(
        eval_id=eval_object.id,
        name=f"agent-eval-run-{ts}",
        data_source=data_source,  # type: ignore[arg-type]
    )
    print(f"  Run ID: {eval_run.id}")

    # ── Poll for completion ──────────────────────────────────────
    print("  Waiting for completion (this may take several minutes)...")
    while True:
        run = client.evals.runs.retrieve(run_id=eval_run.id, eval_id=eval_object.id)
        if run.status in ("completed", "failed"):
            break
        print(f"    Status: {run.status}...")
        time.sleep(10)

    # ── Results ──────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"STATUS: {run.status}")
    print(f"{'=' * 60}")

    if run.status == "completed":
        print(f"Result counts: {run.result_counts}")

        output_items = list(client.evals.runs.output_items.list(run_id=run.id, eval_id=eval_object.id))
        print(f"\nTotal output items: {len(output_items)}")

        # Summarize per-evaluator pass rates
        evaluator_stats: dict[str, dict[str, int]] = {}
        for item in output_items:
            results = getattr(item, "results", None) or []
            for r in results:
                r_dict = r if isinstance(r, dict) else getattr(r, "__dict__", {})
                name = r_dict.get("name", "unknown")
                passed = r_dict.get("passed", False)
                if name not in evaluator_stats:
                    evaluator_stats[name] = {"passed": 0, "failed": 0}
                if passed:
                    evaluator_stats[name]["passed"] += 1
                else:
                    evaluator_stats[name]["failed"] += 1

        if evaluator_stats:
            print("\nPer-evaluator results:")
            for name, stats in evaluator_stats.items():
                total = stats["passed"] + stats["failed"]
                pct = stats["passed"] / total * 100 if total else 0
                print(f"  {name}: {stats['passed']}/{total} passed ({pct:.1f}%)")

        print(f"\nReport URL: {run.report_url}")

        # Save detailed results
        results_path = EVAL_DIR / "agent_eval_results.json"
        results_data = []
        for item in output_items:
            item_dict = item if isinstance(item, dict) else item.__dict__
            # Convert to serializable form
            try:
                results_data.append(json.loads(json.dumps(item_dict, default=str)))
            except (TypeError, ValueError):
                results_data.append(str(item_dict))
        with open(results_path, "w") as f:
            json.dump(results_data, f, indent=2, default=str)
        print(f"Detailed results saved to {results_path.name}")
    else:
        print(f"Run failed. Error: {getattr(run, 'error', 'unknown')}")
        # Print whatever details are available
        pprint(run.__dict__ if hasattr(run, "__dict__") else run)

    print(f"\n{'=' * 60}")
    print("DONE — Check the workflow's Evaluation tab in the Foundry portal")
    print(f"{'=' * 60}")


def main():
    required_vars = ["AZURE_AI_PROJECT_ENDPOINT"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    run_agent_target_evaluation()


if __name__ == "__main__":
    main()
