"""Certified lazy adapter for the OpenAI Agents SDK reference version."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
import sys
from typing import List, Optional

from ..agent import AgentContext
from ..errors import (
    EngineExecutionError,
    FrameworkCompatibilityError,
    FrameworkIntegrationError,
)
from ..receipt import ContextReceipt
from ..session import ContextSession


SUPPORTED_OPENAI_AGENTS_VERSION = "0.8.4"


def _runner_type():
    if sys.version_info < (3, 10):
        raise FrameworkCompatibilityError(
            "OpenAIAgentsAdapter requires Python 3.10 or later"
        )
    try:
        installed = package_version("openai-agents")
    except PackageNotFoundError as exc:
        raise FrameworkIntegrationError(
            f"install openai-agents=={SUPPORTED_OPENAI_AGENTS_VERSION} to use this adapter"
        ) from exc
    if installed != SUPPORTED_OPENAI_AGENTS_VERSION:
        raise FrameworkCompatibilityError(
            "unsupported openai-agents version: "
            f"expected {SUPPORTED_OPENAI_AGENTS_VERSION}, found {installed}"
        )
    try:
        from agents import Runner
    except ImportError as exc:
        raise FrameworkIntegrationError(
            "openai-agents is installed but its Runner cannot be imported"
        ) from exc
    return Runner


class OpenAIAgentsAdapter:
    """Preserve the public Runner result and never own the host loop."""

    def __init__(self, agent: object, session: ContextSession):
        self._agent = agent
        self._session = session
        self._receipt: Optional[ContextReceipt] = None

    @property
    def receipt(self) -> Optional[ContextReceipt]:
        return self._receipt

    def run_sync(self, input: object = None, *, outcome: str = "unknown") -> object:
        Runner = _runner_type()
        current_plan = self._session.current_plan
        source = current_plan.source if current_plan is not None else None
        if source is None:
            raise FrameworkIntegrationError(
                "OpenAIAgentsAdapter requires a planned session source"
            )
        view = self._session.prepare(source)
        payload = input
        if view is not None:
            try:
                payload = view.require_text()
            except EngineExecutionError:
                payload = input if input is not None else self._session.task
        if payload is None:
            payload = self._session.task
        try:
            result = Runner.run_sync(self._agent, input=payload)
        except BaseException as exc:
            self._receipt = self._session.abort(exc)
            raise
        self._receipt = self._session.complete(result, outcome=outcome)
        return result


def openai_tools(context: AgentContext) -> list:
    """Create certified OpenAI function tools for negotiated SDK capabilities."""

    if not isinstance(context, AgentContext):
        raise FrameworkIntegrationError("openai_tools requires an AgentContext")
    _runner_type()
    try:
        from agents import function_tool
    except ImportError as exc:
        raise FrameworkIntegrationError(
            "openai-agents is installed but function_tool cannot be imported"
        ) from exc

    tools = []

    if "ctx_read" in context.capabilities:

        @function_tool
        def leanctx_read(path: str, mode: str = "auto") -> str:
            """Read one project file with LeanCTX compression and cache reuse."""
            return context.read(path, mode).text

        tools.append(leanctx_read)

    if "ctx_search" in context.capabilities:

        @function_tool
        def leanctx_search(pattern: str, path: str = ".", max_results: int = 50) -> str:
            """Search project code without loading every matching file."""
            return context.search(pattern, path=path, max_results=max_results).text

        tools.append(leanctx_search)

    if "ctx_tree" in context.capabilities:

        @function_tool
        def leanctx_tree(path: str = ".", depth: int = 3) -> str:
            """Return a bounded project tree."""
            return context.tree(path, depth=depth).text

        tools.append(leanctx_tree)

    if "ctx_patch" in context.capabilities and context.permissions.write:

        @function_tool
        def leanctx_replace_unique(path: str, old_text: str, new_text: str) -> str:
            """Replace one unique text occurrence inside the project root."""
            return context.replace_unique(path, old_text, new_text).text

        @function_tool
        def leanctx_create_file(path: str, text: str) -> str:
            """Create one new file inside the project root."""
            return context.create_file(path, text).text

        tools.extend((leanctx_replace_unique, leanctx_create_file))

    if "ctx_shell" in context.capabilities and context.permissions.execute:

        @function_tool
        def leanctx_run(argv: List[str], cwd: str = ".", timeout: float = 30.0) -> str:
            """Run an allowlisted argv command and return compressed output."""
            return context.run(argv, cwd=cwd, timeout=timeout).text

        tools.append(leanctx_run)

    return tools


__all__ = [
    "OpenAIAgentsAdapter",
    "SUPPORTED_OPENAI_AGENTS_VERSION",
    "openai_tools",
]
