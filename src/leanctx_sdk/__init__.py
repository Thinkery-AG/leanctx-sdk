"""LeanCTX SDK Stable v1 public surface.

The five Product primitives are ContextSession, ContextSource, ContextView,
ContextPlan, and ContextReceipt. Experimental local-context APIs live under
``leanctx_sdk.preview`` and are outside the Stable compatibility guarantee.
"""

__version__ = "1.0.0"

from .engine import EngineClient, SubprocessEngineClient
from .errors import (
    ArtifactIntegrityError,
    CompatibilityError,
    ConfigurationError,
    EngineError,
    EngineExecutionError,
    EngineProtocolError,
    EngineRejected,
    EngineTimeout,
    EngineUnavailable,
    FrameworkCompatibilityError,
    FrameworkIntegrationError,
    PolicyAdmissionError,
    RecoveryUnavailableError,
    SDKError,
    SessionStateError,
    SourceUnavailableError,
    UnsupportedEngineError,
    ValidationError,
)
from .protocol import (
    ENGINE_INTERFACE_VERSION,
    SCHEMA_VERSION,
    TRANSPORT_VERSION,
    ContextFailure,
    ContextMeasurement,
    ContextPlan,
    ContextReceiptLink,
    ContextSource,
    ContextView,
    EngineStatus,
    FailureCode,
    Freshness,
    HostOutcome,
    Integrity,
    RecoveredSource,
    SessionState,
)
from .receipt import ContextReceipt
from .session import ContextSession

__all__ = [
    "__version__",
    "ArtifactIntegrityError",
    "CompatibilityError",
    "ConfigurationError",
    "ContextFailure",
    "ContextMeasurement",
    "ContextPlan",
    "ContextReceipt",
    "ContextReceiptLink",
    "ContextSession",
    "ContextSource",
    "ContextView",
    "ENGINE_INTERFACE_VERSION",
    "EngineClient",
    "EngineError",
    "EngineExecutionError",
    "EngineProtocolError",
    "EngineRejected",
    "EngineStatus",
    "EngineTimeout",
    "EngineUnavailable",
    "FailureCode",
    "Freshness",
    "FrameworkCompatibilityError",
    "FrameworkIntegrationError",
    "HostOutcome",
    "Integrity",
    "PolicyAdmissionError",
    "RecoveredSource",
    "RecoveryUnavailableError",
    "SCHEMA_VERSION",
    "SDKError",
    "SessionState",
    "SessionStateError",
    "SourceUnavailableError",
    "SubprocessEngineClient",
    "TRANSPORT_VERSION",
    "UnsupportedEngineError",
    "ValidationError",
]
