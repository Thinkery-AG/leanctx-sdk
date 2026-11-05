// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** LeanCTX SDK Stable v1 and additive Agent Tools v1.1 surface. */

export const __version__ = "1.3.0" as const;

export {
  AGENT_TOOLS_INTERFACE_VERSION,
  AGENT_TOOLS_SCHEMA_VERSION,
  AGENT_TOOLS_TRANSPORT_VERSION,
  SUPPORTED_AGENT_TOOLS_ENGINE_VERSION,
  AgentContext,
  AgentMetrics,
  AgentPermissions,
  AsyncAgentContext,
  ExecutionPolicy,
  ReadMode,
  ToolResult,
} from "./agent.js";
export type { GitLabSource, SessionContainment } from "./agent.js";
export {
  MAX_SOURCE_REQUEST_BYTES,
  MAX_SOURCE_RESPONSE_BYTES,
  SubprocessInstanceClient,
} from "./engine.js";
export type { InstanceClient, SourceOperation } from "./engine.js";
export {
  InstancePlanningRequest,
  InstanceSource,
  InstanceSourcePlanningClient,
  MAX_ENGINE_CONTEXT_PLAN_CANDIDATES,
  MAX_ENGINE_CONTEXT_PLAN_QUERY_BYTES,
  MAX_ENGINE_CONTEXT_PLAN_REQUEST_BYTES,
  MAX_ENGINE_CONTEXT_PLAN_TOKENS,
  MAX_ENGINE_SOURCE_CONTENT_BYTES,
  MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES,
  MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES,
  MAX_ENGINE_SOURCE_PLAN_SOURCES,
  PresenterCapability,
  parseMaterialization,
  parseSourcePlan,
} from "./planning.js";
export type {
  InstanceContextPlan,
  InstanceContextSourceMaterialization,
  InstanceContextSourceMaterializationV2,
  InstancePresenterGrant,
  PresentedUse,
  PresentedUseAnswer,
  PresenterRevocation,
  InstanceSourceDescriptor,
  InstanceSourceDescriptorInput,
  InstanceSourcePlan,
  InstanceSourcePlanResult,
  InstanceSourceSelection,
} from "./planning.js";
export { InstanceContextClient } from "./context.js";
export type { InstanceContextClientOptions, InstanceContextReadResult } from "./context.js";
export { EnterpriseInstanceClient } from "./enterprise.js";
export type {
  ContextExecuteV2Options,
  ContextMaterializeSourcesOptions,
  EnterpriseInstanceClientOptions,
  EnterpriseSourceMaterialization,
} from "./enterprise.js";
export {
  MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_EXECUTION_V2_RESPONSE_BYTES,
  MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES,
  parseSourceExecutionV2Response,
} from "./source_execution.js";
export type { InstanceSourceExecution, InstanceSourceExecutionV2 } from "./source_execution.js";
export type { InstanceOutcomeSignal, InstanceOutcomeResponse } from "./outcome.js";
export {
  AgentPermissionError,
  ArtifactIntegrityError,
  CompatibilityError,
  ConfigurationError,
  InstanceCrashed,
  InstanceError,
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
  UnsupportedCapabilityError,
  UnsupportedInstanceError,
  ValidationError,
  // Deprecated since 1.3.0 (removed in 2.0): same classes under their Engine names.
  EngineCrashed,
  EngineError,
  EngineExecutionError,
  EngineProtocolError,
  EngineRejected,
  EngineTimeout,
  EngineUnavailable,
  UnsupportedEngineError,
} from "./errors.js";
export {
  EngineContextClient,
  EnginePlanningRequest,
  EngineSource,
  EngineSourcePlanningClient,
  EngineStatus,
  EnterpriseEngineClient,
  SubprocessEngineClient,
} from "./deprecated.js";
export type {
  EngineClient,
  EngineContextClientOptions,
  EngineContextPlan,
  EngineContextReadResult,
  EngineContextSourceMaterialization,
  EngineContextSourceMaterializationV2,
  EngineOutcomeResponse,
  EngineOutcomeSignal,
  EnginePresenterGrant,
  EngineSourceDescriptor,
  EngineSourceDescriptorInput,
  EngineSourceExecution,
  EngineSourceExecutionV2,
  EngineSourcePlan,
  EngineSourcePlanResult,
  EngineSourceSelection,
  EnterpriseEngineClientOptions,
  SubprocessEngineClientOptions,
} from "./deprecated.js";
export type { SubprocessInstanceClientOptions } from "./engine.js";
export {
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
} from "./protocol.js";
export { ContextReceipt } from "./receipt.js";
export { ContextSession } from "./session.js";

// Material change: union public context-store names with the private surface.
export {
  CONTEXT_STORE_PREVIEW_CONTRACT,
  CONTEXT_STORE_PREVIEW_VERSION,
  MAX_STORE_REQUEST_BYTES,
  MAX_STORE_RESPONSE_BYTES,
  TASK_CLASSES,
  LANGUAGES,
  SIZES,
  STRATEGIES,
  parsePolicyEvidence,
  parseTaskLineage,
  readPolicyEvidence,
  readTaskLineage,
} from "./context_store.js";
export type {
  Workload,
  QualityEvidence,
  SecurityEvidence,
  StrategyOutcomeRecord,
  StrategyEvaluation,
  ContextPolicyEvidence,
  LineageStep,
  DeliverySummary,
  LineageDelivery,
  TaskLineage,
  ContextStoreScope,
} from "./context_store.js";
