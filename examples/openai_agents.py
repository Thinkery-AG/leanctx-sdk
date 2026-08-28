"""Optional OpenAI Agents SDK 0.8.4 adapter example."""

from leanctx_sdk import ContextSession, ContextSource
from leanctx_sdk.integrations.openai_agents import OpenAIAgentsAdapter


def run(agent, project_root: str, path: str):
    session = ContextSession("example task")
    session.plan(ContextSource(path, project_root=project_root))
    return OpenAIAgentsAdapter(agent, session).run_sync()
