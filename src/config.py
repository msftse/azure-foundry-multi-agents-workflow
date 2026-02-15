"""Centralized configuration — loads env vars and exposes typed settings."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class AzureOpenAIConfig:
    endpoint: str
    api_key: str
    deployment_name: str = "gpt-4o-mini"
    api_version: str = "2025-01-01-preview"


@dataclass(frozen=True)
class JiraConfig:
    mcp_sse_url: str


@dataclass(frozen=True)
class SlackConfig:
    mcp_sse_url: str


@dataclass(frozen=True)
class GitHubConfig:
    personal_access_token: str
    mcp_url: str = "https://api.githubcopilot.com/mcp/"


@dataclass(frozen=True)
class EvaluationConfig:
    azure_subscription_id: str = ""
    azure_resource_group: str = ""
    azure_ai_project_name: str = ""
    azure_ai_project_endpoint: str = ""
    azure_ai_model_deployment_name: str = "gpt-4o-mini"


@dataclass(frozen=True)
class WorkflowConfig:
    max_rounds: int = 10


@dataclass(frozen=True)
class Config:
    azure_openai: AzureOpenAIConfig
    jira: JiraConfig
    slack: SlackConfig
    github: GitHubConfig
    evaluation: EvaluationConfig
    workflow: WorkflowConfig


def load_config() -> Config:
    """Load configuration from environment variables.

    Call after load_dotenv() or ensure env vars are set.
    """
    load_dotenv()

    # Azure OpenAI (required)
    azure_openai = AzureOpenAIConfig(
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        deployment_name=os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "gpt-4o-mini"),
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
    )

    # Jira — APIM MCP SSE endpoint (required)
    jira_mcp_url = os.environ.get("JIRA_MCP_SSE_URL", "")
    if not jira_mcp_url:
        raise ValueError("JIRA_MCP_SSE_URL must be set (e.g. https://apim-xxx.azure-api.net/jira-mcp-1/sse)")
    jira = JiraConfig(mcp_sse_url=jira_mcp_url)

    # Slack — APIM MCP SSE endpoint (required)
    slack_mcp_url = os.environ.get("SLACK_MCP_SSE_URL", "")
    if not slack_mcp_url:
        raise ValueError("SLACK_MCP_SSE_URL must be set (e.g. https://apim-xxx.azure-api.net/slack-mcp-1/sse)")
    slack = SlackConfig(mcp_sse_url=slack_mcp_url)

    # GitHub — remote MCP server with PAT auth
    github_pat = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not github_pat:
        raise ValueError("GITHUB_PERSONAL_ACCESS_TOKEN must be set.")
    github_mcp_url = os.environ.get("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/")
    github = GitHubConfig(personal_access_token=github_pat, mcp_url=github_mcp_url)

    # Evaluation (optional)
    evaluation = EvaluationConfig(
        azure_subscription_id=os.environ.get("AZURE_SUBSCRIPTION_ID", ""),
        azure_resource_group=os.environ.get("AZURE_RESOURCE_GROUP", ""),
        azure_ai_project_name=os.environ.get("AZURE_AI_PROJECT_NAME", ""),
        azure_ai_project_endpoint=os.environ.get("AZURE_AI_PROJECT_ENDPOINT", ""),
        azure_ai_model_deployment_name=os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini"),
    )

    # Workflow
    workflow = WorkflowConfig(
        max_rounds=int(os.environ.get("WORKFLOW_MAX_ROUNDS", "10")),
    )

    return Config(
        azure_openai=azure_openai,
        jira=jira,
        slack=slack,
        github=github,
        evaluation=evaluation,
        workflow=workflow,
    )
