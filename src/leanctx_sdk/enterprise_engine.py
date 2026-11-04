"""Deprecated since 1.3.0, removed in 2.0: use ``leanctx_sdk.enterprise_instance``."""

import warnings

from . import _deprecation, enterprise_instance as _enterprise_instance

warnings.warn(
    "leanctx_sdk.enterprise_engine is deprecated since LeanCTX SDK 1.3.0 and will be "
    "removed in 2.0; use leanctx_sdk.enterprise_instance",
    DeprecationWarning,
    stacklevel=2,
)

_OLD = {"EnterpriseEngineClient", "EngineContextClient", "EngineContextReadResult"}


def __getattr__(name):
    if name in _OLD:
        new = _deprecation.DEPRECATED_NAMES[name]
        _deprecation.warn_renamed(name, new)
        return getattr(_enterprise_instance, new)
    return getattr(_enterprise_instance, name)
