# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""LeanCTX SDK Stable v1 public surface.

The five Product primitives are ContextSession, ContextSource, ContextView,
ContextPlan, and ContextReceipt. Experimental local-context APIs live under
``leanctx_sdk.preview`` and are outside the Stable compatibility guarantee.
SDK 1.1 adds the separate Agent Tools contract for host-owned agent loops.
The v4 integration adds planning and guarded context-read types documented in
docs/ENGINE-PLANNING.md. SDK 1.3 renames the Engine names to Instance names; the
Engine names stay importable as deprecated aliases until SDK 2.0.
"""

__version__ = "1.3.0"

from .agent import (
    AGENT_TOOLS_INTERFACE_VERSION,
    AGENT_TOOLS_SCHEMA_VERSION,
    AGENT_TOOLS_TRANSPORT_VERSION,
    AgentContext,
    AgentMetrics,
    AgentPermissions,
    AsyncAgentContext,
    ExecutionPolicy,
    GitLabSource,
    ReadMode,
    SUPPORTED_AGENT_TOOLS_ENGINE_VERSION,
    ToolResult,
)
from .instance import InstanceClient, InstancePlanningClient, SubprocessInstanceClient
from .enterprise_instance import (
    InstanceContextClient,
    InstanceContextReadResult,
    EnterpriseInstanceClient,
)
from .planning import InstancePlanningRequest, InstanceSource
from .presenter import PresenterCapability
from .errors import (
    AgentPermissionError,
    ArtifactIntegrityError,
    CompatibilityError,
    ConfigurationError,
    InstanceError,
    InstanceCrashed,
    InstanceExecutionError,
    InstanceProtocolError,
    InstanceRejected,
    InstanceTimeout,
    InstanceUnavailable,
    FrameworkCompatibilityError,
    FrameworkIntegrationError,
    PolicyAdmissionError,
    RecoveryUnavailableError,
    SDKError,
    SessionStateError,
    SourceUnavailableError,
    UnsupportedInstanceError,
    UnsupportedCapabilityError,
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
    InstanceStatus,
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
    "AGENT_TOOLS_INTERFACE_VERSION",
    "AGENT_TOOLS_SCHEMA_VERSION",
    "AGENT_TOOLS_TRANSPORT_VERSION",
    "AgentContext",
    "AgentMetrics",
    "AgentPermissionError",
    "AgentPermissions",
    "AsyncAgentContext",
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
    "InstanceContextClient",
    "InstanceContextReadResult",
    "InstanceClient",
    "InstancePlanningClient",
    "InstancePlanningRequest",
    "InstanceSource",
    "EnterpriseInstanceClient",
    "InstanceCrashed",
    "InstanceError",
    "InstanceExecutionError",
    "InstanceProtocolError",
    "InstanceRejected",
    "InstanceStatus",
    "InstanceTimeout",
    "InstanceUnavailable",
    "ExecutionPolicy",
    "GitLabSource",
    "FailureCode",
    "Freshness",
    "FrameworkCompatibilityError",
    "FrameworkIntegrationError",
    "HostOutcome",
    "Integrity",
    "PolicyAdmissionError",
    "PresenterCapability",
    "RecoveredSource",
    "ReadMode",
    "RecoveryUnavailableError",
    "SCHEMA_VERSION",
    "SDKError",
    "SessionState",
    "SessionStateError",
    "SourceUnavailableError",
    "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
    "SubprocessInstanceClient",
    "ToolResult",
    "TRANSPORT_VERSION",
    "UnsupportedInstanceError",
    "UnsupportedCapabilityError",
    "ValidationError",
]

from . import _deprecation  # noqa: E402

# Engine names are deprecated aliases of the same objects until SDK 2.0.
__getattr__ = _deprecation.module_getattr(
    __name__,
    globals(),
    {
        "EngineClient",
        "EngineContextClient",
        "EngineContextReadResult",
        "EngineCrashed",
        "EngineError",
        "EngineExecutionError",
        "EnginePlanningClient",
        "EnginePlanningRequest",
        "EngineProtocolError",
        "EngineRejected",
        "EngineSource",
        "EngineStatus",
        "EngineTimeout",
        "EngineUnavailable",
        "EnterpriseEngineClient",
        "SubprocessEngineClient",
        "UnsupportedEngineError",
    },
)
