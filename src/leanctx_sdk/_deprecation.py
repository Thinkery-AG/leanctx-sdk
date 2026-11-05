"""Deprecated Engine names kept as aliases of the Instance names until SDK 2.0."""

from __future__ import annotations

import os
import threading
import warnings
from typing import Any, Mapping, Optional

REMOVAL = "2.0"

#: Old public name -> new public name. Every old name resolves to the very same
#: object, so ``except EngineUnavailable`` still catches what the SDK raises.
DEPRECATED_NAMES: Mapping[str, str] = {
    "EngineError": "InstanceError",
    "EngineUnavailable": "InstanceUnavailable",
    "EngineProtocolError": "InstanceProtocolError",
    "EngineExecutionError": "InstanceExecutionError",
    "EngineTimeout": "InstanceTimeout",
    "EngineCrashed": "InstanceCrashed",
    "EngineRejected": "InstanceRejected",
    "UnsupportedEngineError": "UnsupportedInstanceError",
    "EngineUnavailableError": "InstanceUnavailableError",
    "EngineTimeoutError": "InstanceTimeoutError",
    "EngineProtocolErrorError": "InstanceProtocolErrorError",
    "EngineRejectedError": "InstanceRejectedError",
    "EngineExecutionErrorError": "InstanceExecutionErrorError",
    "EngineStatus": "InstanceStatus",
    "EngineClient": "InstanceClient",
    "SubprocessEngineClient": "SubprocessInstanceClient",
    "EnterpriseEngineClient": "EnterpriseInstanceClient",
    "EngineContextClient": "InstanceContextClient",
    "EnginePlanningClient": "InstancePlanningClient",
    "EnginePlanningRequest": "InstancePlanningRequest",
    "EngineSource": "InstanceSource",
    "EngineContextReadResult": "InstanceContextReadResult",
    "LocalCheckpointPackageEngine": "LocalCheckpointPackageInstance",
}


def _warn(old: str, new: str, stacklevel: int) -> None:
    warnings.warn(
        f"{old} is deprecated since LeanCTX SDK 1.3.0 and will be removed in "
        f"{REMOVAL}; use {new}",
        DeprecationWarning,
        stacklevel=stacklevel,
    )


def warn_renamed(old: str, new: str) -> None:
    """Warn that attribute ``old`` was renamed to ``new``, pointing at the caller."""
    _warn(old, new, 4)


def module_getattr(module_name: str, namespace: Mapping[str, Any], names):
    """Build a module ``__getattr__`` resolving the deprecated names in ``names``."""

    def __getattr__(name: str) -> Any:
        if name in names:
            new = DEPRECATED_NAMES[name]
            _warn(name, new, 3)
            return namespace[new]
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")

    return __getattr__


def renamed_kwarg(
    old_name: str, old_value: Any, new_name: str, new_value: Any, default: Any
) -> Any:
    """Resolve a renamed keyword argument; the old one warns and still works."""
    if old_value is None:
        return default if new_value is None else new_value
    if new_value is not None:
        raise TypeError(f"pass {new_name} only; {old_name} is its deprecated alias")
    _warn(old_name, new_name, 4)
    return old_value


_ENV_WARNED: set = set()
_ENV_LOCK = threading.Lock()

#: New environment variable -> deprecated predecessor.
DEPRECATED_ENV: Mapping[str, str] = {
    "LEANCTX_INSTANCE_BIN": "LEANCTX_ENGINE_BIN",
    "LEANCTX_INSTANCE_BINARY": "LEANCTX_ENGINE_BINARY",
    "LEANCTX_INSTANCE_PROOF_FILE": "LEANCTX_ENGINE_PROOF_FILE",
    "LEANCTX_REQUIRE_INSTANCE": "LEANCTX_REQUIRE_ENGINE",
    "LEANCTX_TEST_INSTANCE_BINARY": "LEANCTX_TEST_ENGINE_BINARY",
}


def instance_env(name: str, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Read ``name``; fall back to its deprecated Engine variable once-warned."""
    env = os.environ if environ is None else environ
    value = env.get(name)
    if value:
        return value
    old = DEPRECATED_ENV.get(name)
    if old is None:
        return value
    value = env.get(old)
    if value:
        with _ENV_LOCK:
            first = old not in _ENV_WARNED
            _ENV_WARNED.add(old)
        if first:
            warnings.warn(
                f"environment variable {old} is deprecated since LeanCTX SDK 1.3.0 "
                f"and will be removed in {REMOVAL}; set {name}",
                DeprecationWarning,
                stacklevel=2,
            )
        return value
    return None
