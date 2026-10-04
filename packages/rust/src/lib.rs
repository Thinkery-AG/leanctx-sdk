// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

mod agent;
mod agent_io;
mod async_agent;
mod context_store;
mod engine;
mod enterprise;
mod errors;
mod gateway_preview;
mod planning;
mod process;
mod protocol;
mod receipt;
mod session;

#[allow(non_upper_case_globals)]
pub const __version__: &str = "1.2.0";

pub use agent::{
    AgentContext, AgentContextBuilder, AgentMetrics, AgentPermissions, ExecutionPolicy,
    GitLabSource, ReadMode, ToolResult, AGENT_TOOLS_INTERFACE_VERSION, AGENT_TOOLS_SCHEMA_VERSION,
    AGENT_TOOLS_TRANSPORT_VERSION, SUPPORTED_AGENT_TOOLS_ENGINE_VERSION,
};
pub use async_agent::AsyncAgentContext;
pub use engine::{EngineClient, SubprocessEngineClient};
pub use enterprise::{
    EngineSourceMaterializationResponse, EngineSourcePlanResponse, EnterpriseEngineClient,
};
pub use errors::{
    AgentPermissionError, ArtifactIntegrityError, CompatibilityError, ConfigurationError,
    EngineCrashed, EngineError, EngineExecutionError, EngineProtocolError, EngineRejected,
    EngineTimeout, EngineUnavailable, FrameworkCompatibilityError, FrameworkIntegrationError,
    PolicyAdmissionError, RecoveryUnavailableError, SDKError, SessionStateError,
    SourceUnavailableError, UnsupportedCapabilityError, UnsupportedEngineError, ValidationError,
};
pub use planning::EnginePlanningRequest;
pub use protocol::{
    ContextFailure, ContextMeasurement, ContextPlan, ContextReceiptLink, ContextSource,
    ContextView, EngineStatus, FailureCode, Freshness, HostOutcome, Integrity, RecoveredSource,
    SessionState, ENGINE_INTERFACE_VERSION, SCHEMA_VERSION, TRANSPORT_VERSION,
};
pub use receipt::ContextReceipt;
pub use session::ContextSession;

/// Preview APIs; they may change in minor releases.
pub mod preview {
    pub use crate::context_store::{
        parse_policy_evidence, parse_task_lineage, ContextPolicyEvidence, ContextStoreScope,
        DeliverySummary, LineageDelivery, LineageStep, QualityEvidence, SecurityEvidence,
        StrategyEvaluation, StrategyOutcomeRecord, TaskLineage, Workload,
        CONTEXT_STORE_PREVIEW_CONTRACT, CONTEXT_STORE_PREVIEW_VERSION, MAX_STORE_REQUEST_BYTES,
        MAX_STORE_RESPONSE_BYTES,
    };
    pub use crate::gateway_preview::{
        parse_decision_receipt, parse_egress_admission, ContextDecision, ContextDecisionReceipt,
        ContextDestination, ContextPrincipal, DetectorCoverage, EgressAdmission, EgressRequest,
        SecuritySignal, EGRESS_SCHEMA_VERSION, GATEWAY_PREVIEW_CONTRACT, GATEWAY_PREVIEW_VERSION,
        MAX_EGRESS_REQUEST_BYTES,
    };
}
