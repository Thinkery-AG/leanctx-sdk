"""Framework-neutral reference adapter."""

from __future__ import annotations

from typing import Mapping, Optional

from ..protocol import ContextSource, ContextView
from ..receipt import ContextReceipt
from ..session import ContextSession


def prepare(
    session: ContextSession,
    source: ContextSource,
    *,
    mode: str = "aggressive",
    freshness: str = "reuse",
) -> Optional[ContextView]:
    return session.prepare(source, mode=mode, freshness=freshness)


def complete(
    session: ContextSession,
    host_result: object = None,
    *,
    outcome: str = "unknown",
    usage: Optional[Mapping[str, object]] = None,
) -> ContextReceipt:
    return session.complete(host_result, outcome=outcome, usage=usage)


def abort(session: ContextSession, error: BaseException) -> ContextReceipt:
    return session.abort(error)


__all__ = ["abort", "complete", "prepare"]
