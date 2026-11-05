"""Deprecated since 1.3.0, removed in 2.0: use ``leanctx_sdk.instance``."""

import warnings

from . import _deprecation, instance as _instance

warnings.warn(
    "leanctx_sdk.engine is deprecated since LeanCTX SDK 1.3.0 and will be removed "
    "in 2.0; use leanctx_sdk.instance",
    DeprecationWarning,
    stacklevel=2,
)

_OLD = {"EngineClient", "EnginePlanningClient", "SubprocessEngineClient"}


def __getattr__(name):
    if name in _OLD:
        new = _deprecation.DEPRECATED_NAMES[name]
        _deprecation.warn_renamed(name, new)
        return getattr(_instance, new)
    return getattr(_instance, name)
