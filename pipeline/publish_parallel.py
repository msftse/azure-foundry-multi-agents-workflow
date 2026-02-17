"""Publish & run parallel fan-out/fan-in workflow on Azure AI Foundry.

Usage:
    python -m pipeline.publish_parallel --register     # Register agents + workflow
    python -m pipeline.publish_parallel --deploy       # Register + deploy via ARM API
    python -m pipeline.publish_parallel --verify       # Verify deployed workflow responds
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential as SyncCredential
from azure.identity.aio import DefaultAzureCredential as AsyncCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import WorkflowAgentDefinition
from agent_framework.azure import AzureAIProjectAgentProvider

from src.config import load_config
from pipeline.parallel_agents import create_all_parallel_agents

# Paths & constants
WORKFLOW_YAML = Path(__file__).parent / "parallel_workflow.yaml"
WORKFLOW_AGENT = "MultiAgentParallelWorkflow"
APP_NAME = "multi-agent-parallel-app"
DEPLOY_NAME = "multi-agent-parallel-deployment"
ARM_API_VERSION = "2025-10-01-preview"
AI_API_VERSION = "2025-11-15-preview"


def _build_arm_base(config) -> tuple[str, str, str, str, str]:
    """Extract ARM path components from config/env."""
    ep = config.evaluation.azure_ai_project_endpoint.rstrip("/")
    host = ep.split("/")[2]
    sub = config.evaluation.azure_subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    rg = (
        os.environ.get("AZURE_AI_RESOURCE_GROUP")
        or config.evaluation.azure_resource_group
        or os.environ.get("AZURE_RESOURCE_GROUP", "")
    )
    acct = os.environ.get("AZURE_ACCOUNT_NAME") or host.split(".")[0]
    proj = config.evaluation.azure_ai_project_name or os.environ.get("AZURE_AI_PROJECT_NAME") or ep.split("/")[-1]
    assert sub and rg, "AZURE_SUBSCRIPTION_ID and AZURE_AI_RESOURCE_GROUP are required."
    arm = (
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{acct}/projects/{proj}"
    )
    ai = f"https://{acct}.services.ai.azure.com/api/projects/{proj}"
    return arm, ai, sub, rg, acct


async def _register_workflow(config) -> str:
    """Register the parallel workflow YAML as a WorkflowAgentDefinition in Foundry."""
    ep = config.evaluation.azure_ai_project_endpoint
    cred = SyncCredential()
    client = AIProjectClient(endpoint=ep, credential=cred)
    v = client.agents.create_version(
        agent_name=WORKFLOW_AGENT,
        definition=WorkflowAgentDefinition(workflow=WORKFLOW_YAML.read_text()),
    )
    vid = f"{v.name}:{v.version}"
    print(f"Registered parallel workflow {vid}")
    return vid


async def _deploy_via_arm(config, vid: str) -> None:
    """Publish the parallel workflow via ARM API (Application + AgentDeployment)."""
    arm, ai, *_ = _build_arm_base(config)
    name, ver = vid.rsplit(":", 1)
    cred = SyncCredential()
    hdr = {
        "Authorization": f"Bearer {cred.get_token('https://management.azure.com/.default').token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as c:
        # 1. Create Application
        app_url = f"{arm}/applications/{APP_NAME}?api-version={ARM_API_VERSION}"
        app_body = {
            "properties": {
                "displayName": "Multi-Agent Parallel Workflow",
                "agents": [{"agentName": name}],
            }
        }
        r = await c.put(app_url, headers=hdr, json=app_body)
        assert r.status_code in (200, 201), f"Application PUT failed {r.status_code}: {r.text[:300]}"
        print(f"Application '{APP_NAME}' created/updated.")

        # 2. Create AgentDeployment
        dep_url = f"{arm}/applications/{APP_NAME}/agentdeployments/{DEPLOY_NAME}?api-version={ARM_API_VERSION}"
        dep_body = {
            "properties": {
                "deploymentType": "Managed",
                "protocols": [{"protocol": "responses", "version": "1.0"}],
                "agents": [{"agentName": name, "agentVersion": ver}],
            }
        }
        r = await c.put(dep_url, headers=hdr, json=dep_body)
        assert r.status_code in (200, 201), f"Deployment PUT failed {r.status_code}: {r.text[:300]}"

    print(f"Deployed -> {ai}/applications/{APP_NAME}/protocols/openai/responses?api-version={AI_API_VERSION}")


async def _verify(config) -> None:
    """Quick smoke test: send a message to the deployed parallel workflow."""
    _, ai, *_ = _build_arm_base(config)
    cred = SyncCredential()
    hdr = {
        "Authorization": f"Bearer {cred.get_token('https://ai.azure.com/.default').token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=300) as c:
        conv = (
            await c.post(
                f"{ai}/openai/conversations?api-version={AI_API_VERSION}",
                headers=hdr,
                json={},
            )
        ).json()["id"]
        r = await c.post(
            f"{ai}/openai/responses?api-version={AI_API_VERSION}",
            headers=hdr,
            json={
                "input": "List Slack channels and Jira projects",
                "agent": {"name": WORKFLOW_AGENT, "type": "agent_reference"},
                "store": True,
                "conversation": {"id": conv},
            },
        )
    assert r.status_code == 200, f"Verify failed {r.status_code}: {r.text[:300]}"
    print(json.dumps(r.json(), indent=2)[:2000])


async def main():
    ap = argparse.ArgumentParser(description="Publish parallel workflow on Azure AI Foundry")
    ap.add_argument("--register", action="store_true", help="Register agents + workflow in Foundry only")
    ap.add_argument("--deploy", action="store_true", help="Register + deploy via ARM API")
    ap.add_argument("--verify", action="store_true", help="Verify deployed workflow responds")
    args = ap.parse_args()

    config = load_config()

    # -- verify only --
    if args.verify:
        await _verify(config)
        return

    # -- register agents in Foundry --
    credential = AsyncCredential()

    provider = AzureAIProjectAgentProvider(
        project_endpoint=config.evaluation.azure_ai_project_endpoint,
        credential=credential,
        model=config.azure_openai.deployment_name,
    )

    mcp_tools = []
    try:
        participants, routing_orch, synthesis_orch, mcp_tools = await create_all_parallel_agents(
            provider, config, model=config.azure_openai.deployment_name
        )

        agent_names = [a.name or "?" for a in participants] + [routing_orch.name or "?"] + [synthesis_orch.name or "?"]
        print(f"Registered agents: {', '.join(agent_names)}")

        # -- register workflow YAML --
        vid = await _register_workflow(config)

        if args.register:
            return

        # -- deploy via ARM --
        if args.deploy:
            await _deploy_via_arm(config, vid)
            return

    finally:
        for tool in mcp_tools:
            await tool.close()
        await provider.close()
        await credential.close()


if __name__ == "__main__":
    asyncio.run(main())
