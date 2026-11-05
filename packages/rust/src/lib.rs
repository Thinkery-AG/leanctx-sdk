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
pub const __version__: &str = "1.3.0";

pub use agent::{
    AgentContext, AgentContextBuilder, AgentMetrics, AgentPermissions, ExecutionPolicy,
    GitLabSource, ReadMode, SessionContainment, ToolResult, AGENT_TOOLS_INTERFACE_VERSION,
    AGENT_TOOLS_SCHEMA_VERSION, AGENT_TOOLS_TRANSPORT_VERSION,
    SUPPORTED_AGENT_TOOLS_INSTANCE_VERSION,
};
pub use async_agent::AsyncAgentContext;
pub use engine::{InstanceClient, SubprocessInstanceClient};
pub use enterprise::{
    EnterpriseInstanceClient, InstancePresenterGrant, InstanceSourceMaterializationResponse,
    InstanceSourceMaterializationResponseV2, InstanceSourcePlanResponse, PresentedUse,
    PresentedUseResponse, PresenterCapability, PresenterUse,
    INSTANCE_PRESENTER_CAPABILITY_MIN_INSTANCE_VERSION, MAX_INSTANCE_PRESENTER_TTL_SECONDS,
    PRESENTER_CAPABILITY_HEADER,
};
pub use errors::{
    AgentPermissionError, ArtifactIntegrityError, CompatibilityError, ConfigurationError,
    FrameworkCompatibilityError, FrameworkIntegrationError, InstanceCrashed, InstanceError,
    InstanceExecutionError, InstanceProtocolError, InstanceRejected, InstanceTimeout,
    InstanceUnavailable, PolicyAdmissionError, RecoveryUnavailableError, SDKError,
    SessionStateError, SourceUnavailableError, UnsupportedCapabilityError,
    UnsupportedInstanceError, ValidationError,
};
pub use planning::InstancePlanningRequest;
pub use protocol::{
    ContextFailure, ContextMeasurement, ContextPlan, ContextReceiptLink, ContextSource,
    ContextView, FailureCode, Freshness, HostOutcome, InstanceStatus, Integrity, RecoveredSource,
    SessionState, ENGINE_INTERFACE_VERSION, SCHEMA_VERSION, TRANSPORT_VERSION,
};
pub use receipt::ContextReceipt;
pub use session::ContextSession;

/// Deprecated since 1.3.0, use [`InstanceClient`]; removed in 2.0.
///
/// Stable Rust cannot attach a deprecation warning to a trait alias, so this
/// is a plain re-export of the same trait: `impl EngineClient for T`,
/// `dyn EngineClient` and method calls keep compiling unchanged.
pub use engine::InstanceClient as EngineClient;

// Deprecated Engine names (until 2.0). Each is the same type or value as its
// Instance name, so `downcast_ref`, pattern matching and comparisons keep working.
#[deprecated(since = "1.3.0", note = "use SubprocessInstanceClient")]
pub type SubprocessEngineClient = SubprocessInstanceClient;
#[deprecated(since = "1.3.0", note = "use EnterpriseInstanceClient")]
pub type EnterpriseEngineClient = EnterpriseInstanceClient;
#[deprecated(since = "1.3.0", note = "use InstancePresenterGrant")]
pub type EnginePresenterGrant = InstancePresenterGrant;
#[deprecated(since = "1.3.0", note = "use InstanceSourceMaterializationResponse")]
pub type EngineSourceMaterializationResponse = InstanceSourceMaterializationResponse;
#[deprecated(since = "1.3.0", note = "use InstanceSourceMaterializationResponseV2")]
pub type EngineSourceMaterializationResponseV2 = InstanceSourceMaterializationResponseV2;
#[deprecated(since = "1.3.0", note = "use InstanceSourcePlanResponse")]
pub type EngineSourcePlanResponse = InstanceSourcePlanResponse;
#[deprecated(since = "1.3.0", note = "use InstancePlanningRequest")]
pub type EnginePlanningRequest = InstancePlanningRequest;
#[deprecated(since = "1.3.0", note = "use InstanceStatus")]
pub type EngineStatus = InstanceStatus;
#[deprecated(since = "1.3.0", note = "use InstanceError")]
pub type EngineError = InstanceError;
#[deprecated(since = "1.3.0", note = "use InstanceUnavailable")]
pub type EngineUnavailable = InstanceUnavailable;
#[deprecated(since = "1.3.0", note = "use InstanceTimeout")]
pub type EngineTimeout = InstanceTimeout;
#[deprecated(since = "1.3.0", note = "use InstanceCrashed")]
pub type EngineCrashed = InstanceCrashed;
#[deprecated(since = "1.3.0", note = "use InstanceProtocolError")]
pub type EngineProtocolError = InstanceProtocolError;
#[deprecated(since = "1.3.0", note = "use InstanceRejected")]
pub type EngineRejected = InstanceRejected;
#[deprecated(since = "1.3.0", note = "use InstanceExecutionError")]
pub type EngineExecutionError = InstanceExecutionError;
#[deprecated(since = "1.3.0", note = "use UnsupportedInstanceError")]
pub type UnsupportedEngineError = UnsupportedInstanceError;
#[deprecated(since = "1.3.0", note = "use SUPPORTED_AGENT_TOOLS_INSTANCE_VERSION")]
pub const SUPPORTED_AGENT_TOOLS_ENGINE_VERSION: &str = SUPPORTED_AGENT_TOOLS_INSTANCE_VERSION;
#[deprecated(
    since = "1.3.0",
    note = "use INSTANCE_PRESENTER_CAPABILITY_MIN_INSTANCE_VERSION"
)]
pub const ENGINE_PRESENTER_CAPABILITY_MIN_ENGINE_VERSION: &str =
    INSTANCE_PRESENTER_CAPABILITY_MIN_INSTANCE_VERSION;
#[deprecated(since = "1.3.0", note = "use MAX_INSTANCE_PRESENTER_TTL_SECONDS")]
pub const MAX_ENGINE_PRESENTER_TTL_SECONDS: u32 = MAX_INSTANCE_PRESENTER_TTL_SECONDS;

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
        keyed_object_digest, parse_decision_receipt, parse_egress_admission,
        parse_egress_host_report, parse_egress_preparation, receipt_key_id, ContextDecision,
        ContextDecisionReceipt, ContextDestination, ContextPrincipal, DetectorCoverage,
        EgressAdmission, EgressHostReportRequest, EgressHostReportResponse, EgressPreparation,
        EgressRequest, ReceiptObjectDigest, SecuritySignal, EGRESS_PREPARE_SCHEMA_VERSION,
        EGRESS_SCHEMA_VERSION, GATEWAY_PREVIEW_CONTRACT, GATEWAY_PREVIEW_VERSION,
        MAX_EGRESS_ATTEMPTS, MAX_EGRESS_REPORT_BYTES, MAX_EGRESS_REQUEST_BYTES,
    };
}
