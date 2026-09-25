// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** LeanCTX SDK Stable v1 and additive Agent Tools v1.1 surface. */

export const __version__ = "1.1.0" as const;

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
export {
  MAX_SOURCE_REQUEST_BYTES,
  MAX_SOURCE_RESPONSE_BYTES,
  SubprocessEngineClient,
} from "./engine.js";
export type { EngineClient, SourceOperation } from "./engine.js";
export {
  EnginePlanningRequest,
  EngineSource,
  EngineSourcePlanningClient,
  MAX_ENGINE_CONTEXT_PLAN_CANDIDATES,
  MAX_ENGINE_CONTEXT_PLAN_QUERY_BYTES,
  MAX_ENGINE_CONTEXT_PLAN_REQUEST_BYTES,
  MAX_ENGINE_CONTEXT_PLAN_TOKENS,
  MAX_ENGINE_SOURCE_CONTENT_BYTES,
  MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES,
  MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES,
  MAX_ENGINE_SOURCE_PLAN_SOURCES,
  parseMaterialization,
  parseSourcePlan,
} from "./planning.js";
export type {
  EngineContextPlan,
  EngineContextSourceMaterialization,
  EngineSourceDescriptor,
  EngineSourceDescriptorInput,
  EngineSourcePlan,
  EngineSourcePlanResult,
  EngineSourceSelection,
} from "./planning.js";
export { EngineContextClient } from "./context.js";
export type { EngineContextClientOptions, EngineContextReadResult } from "./context.js";
export { EnterpriseEngineClient } from "./enterprise.js";
export type {
  ContextExecuteV2Options,
  ContextMaterializeSourcesOptions,
  EnterpriseEngineClientOptions,
  EnterpriseSourceMaterialization,
} from "./enterprise.js";
export {
  MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_EXECUTION_V2_RESPONSE_BYTES,
  MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES,
  parseSourceExecutionV2Response,
} from "./source_execution.js";
export type { EngineSourceExecution, EngineSourceExecutionV2 } from "./source_execution.js";
export type { EngineOutcomeSignal, EngineOutcomeResponse } from "./outcome.js";
export {
  AgentPermissionError,
  ArtifactIntegrityError,
  CompatibilityError,
  ConfigurationError,
  EngineCrashed,
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
  UnsupportedCapabilityError,
  UnsupportedEngineError,
  ValidationError,
} from "./errors.js";
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
  EngineStatus,
  FailureCode,
  Freshness,
  HostOutcome,
  Integrity,
  RecoveredSource,
  SessionState,
} from "./protocol.js";
export { ContextReceipt } from "./receipt.js";
export { ContextSession } from "./session.js";
