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
export { SubprocessEngineClient } from "./engine.js";
export type { EngineClient } from "./engine.js";
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
