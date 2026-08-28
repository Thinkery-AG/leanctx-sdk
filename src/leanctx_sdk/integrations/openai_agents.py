"""Certified lazy adapter for the OpenAI Agents SDK reference version."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
import sys
from typing import Optional

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


__all__ = ["OpenAIAgentsAdapter", "SUPPORTED_OPENAI_AGENTS_VERSION"]
