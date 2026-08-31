#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

mod agent;
mod engine;
mod errors;
mod protocol;
mod receipt;
mod session;

#[allow(non_upper_case_globals)]
pub const __version__: &str = "1.1.0";

pub use agent::{
    AgentContext, AgentMetrics, AgentPermissions, AsyncAgentContext, ExecutionPolicy, ReadMode,
    ToolResult, AGENT_TOOLS_INTERFACE_VERSION, AGENT_TOOLS_SCHEMA_VERSION,
    AGENT_TOOLS_TRANSPORT_VERSION, SUPPORTED_AGENT_TOOLS_ENGINE_VERSION,
};
pub use engine::{EngineClient, SubprocessEngineClient};
pub use errors::{
    AgentPermissionError, ArtifactIntegrityError, CompatibilityError, ConfigurationError,
    EngineCrashed, EngineError, EngineExecutionError, EngineProtocolError, EngineRejected,
    EngineTimeout, EngineUnavailable, FrameworkCompatibilityError, FrameworkIntegrationError,
    PolicyAdmissionError, RecoveryUnavailableError, SDKError, SessionStateError,
    SourceUnavailableError, UnsupportedCapabilityError, UnsupportedEngineError, ValidationError,
};
pub use protocol::{
    ContextFailure, ContextMeasurement, ContextPlan, ContextReceiptLink, ContextSource,
    ContextView, EngineStatus, FailureCode, Freshness, HostOutcome, Integrity, RecoveredSource,
    SessionState, ENGINE_INTERFACE_VERSION, SCHEMA_VERSION, TRANSPORT_VERSION,
};
pub use receipt::ContextReceipt;
pub use session::ContextSession;
