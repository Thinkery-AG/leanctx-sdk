/** Stable, non-secret error taxonomy for the LeanCTX SDK. */

export type ErrorGuidance = Readonly<{
  abort_required: boolean;
  code: string;
  configuration_fix: boolean;
  degrade_allowed: boolean;
  guidance: string;
  retryable: boolean;
  version_change: boolean;
}>;

/** Base class for all SDK failures. */
export class SDKError extends Error {
  static readonly code: string = "sdk_error";
  static readonly guidance: string =
    "preserve original evidence and classify the concrete error";
  static readonly retryable: boolean = false;
  static readonly degradeAllowed: boolean = false;
  static readonly abortRequired: boolean = true;
  static readonly configurationFix: boolean = false;
  static readonly versionChange: boolean = false;

  readonly code: string;
  readonly guidance: string;
  retryable: boolean;
  readonly degradeAllowed: boolean;
  readonly abortRequired: boolean;
  readonly configurationFix: boolean;
  readonly versionChange: boolean;

  constructor(message?: string, options?: ErrorOptions) {
    super(message || (new.target as typeof SDKError).code);
    if (options?.cause !== undefined) this.cause = options.cause;
    this.name = new.target.name;
    const ctor = new.target as typeof SDKError;
    this.code = ctor.code;
    this.guidance = ctor.guidance;
    this.retryable = ctor.retryable;
    this.degradeAllowed = ctor.degradeAllowed;
    this.abortRequired = ctor.abortRequired;
    this.configurationFix = ctor.configurationFix;
    this.versionChange = ctor.versionChange;
    Object.setPrototypeOf(this, new.target.prototype);
  }

  /** Stable host guidance; never includes the potentially sensitive message. */
  asDict(): ErrorGuidance {
    return {
      abort_required: this.abortRequired,
      code: this.code,
      configuration_fix: this.configurationFix,
      degrade_allowed: this.degradeAllowed,
      guidance: this.guidance,
      retryable: this.retryable,
      version_change: this.versionChange,
    };
  }

  /** Python SDK spelling retained for cross-language host adapters. */
  as_dict(): ErrorGuidance {
    return this.asDict();
  }
}

export class ValidationError extends SDKError {
  static readonly code = "validation_error";
  static readonly guidance = "fix caller input before retrying";
}

export class ConfigurationError extends SDKError {
  static readonly code = "configuration_error";
  static readonly guidance = "fix SDK configuration before retrying";
  static readonly configurationFix = true;
}

export class SessionStateError extends SDKError {
  static readonly code = "session_state_error";
  static readonly guidance = "fix lifecycle ordering or create a new session";
}

export class InstanceError extends SDKError {
  static readonly code: string = "engine_error";
  static readonly guidance: string = "preserve instance evidence and classify the concrete error";

  constructor(message?: string, options?: ErrorOptions) {
    super(message);
    if (options?.cause !== undefined) this.cause = options.cause;
  }
}

export class InstanceUnavailable extends InstanceError {
  static readonly code = "engine_unavailable";
  static readonly guidance =
    "restore the configured instance binary or use explicit bounded fail-open";
  static readonly retryable = true;
  static readonly degradeAllowed = true;
  static readonly abortRequired = false;
  static readonly configurationFix = true;
}

export class InstanceTimeout extends InstanceError {
  static readonly code = "engine_timeout";
  static readonly guidance = "retry within host policy or use explicit bounded fail-open";
  static readonly retryable = true;
  static readonly degradeAllowed = true;
  static readonly abortRequired = false;
}

export class InstanceCrashed extends InstanceError {
  static readonly code = "engine_crashed";
  static readonly guidance =
    "create a new AgentContext; mutation and execution calls are never retried";
}

export class AgentPermissionError extends InstanceError {
  static readonly code = "agent_permission_denied";
  static readonly guidance =
    "create a new AgentContext with the required explicit permission";
  static readonly configurationFix = true;
}

export class UnsupportedCapabilityError extends InstanceError {
  static readonly code = "unsupported_capability";
  static readonly guidance =
    "install a compatible instance or choose a negotiated capability";
  static readonly versionChange = true;
}

export class InstanceProtocolError extends InstanceError {
  static readonly code: string = "engine_protocol_error";
  static readonly guidance: string =
    "fail closed and verify Engine interface, schema, and transport";
}

export class CompatibilityError extends InstanceProtocolError {
  static readonly code: string = "compatibility_error";
  static readonly guidance: string =
    "install a supported version from the compatibility matrix";
  static readonly versionChange = true;
}

export class UnsupportedInstanceError extends CompatibilityError {
  static readonly code = "unsupported_engine";
  static readonly guidance =
    "install a LeanCTX instance identity and capability supported by this SDK";
}

export type FailureLike = { readonly retryableByHost?: boolean };

export class InstanceRejected extends InstanceError {
  static readonly code: string = "engine_rejected";
  static readonly guidance: string = "fail closed and satisfy the reported instance policy";
  readonly failure?: FailureLike;
  readonly view?: unknown;

  constructor(message?: string, options?: { failure?: FailureLike; view?: unknown }) {
    super(message);
    this.failure = options?.failure;
    this.view = options?.view;
    if (options?.failure?.retryableByHost !== undefined) {
      this.retryable = Boolean(options.failure.retryableByHost);
    }
  }
}

export class PolicyAdmissionError extends InstanceRejected {
  static readonly code = "policy_admission_rejected";
  static readonly guidance =
    "abort or change the request to satisfy the reported instance policy";
  static readonly configurationFix = true;
}

export class InstanceExecutionError extends InstanceError {
  static readonly code: string = "engine_execution_error";
  static readonly guidance: string = "fail closed and retain the factual instance failure evidence";
  readonly failure?: FailureLike;
  readonly view?: unknown;

  constructor(message?: string, options?: { failure?: FailureLike; view?: unknown }) {
    super(message);
    this.failure = options?.failure;
    this.view = options?.view;
    if (options?.failure?.retryableByHost !== undefined) {
      this.retryable = Boolean(options.failure.retryableByHost);
    }
  }
}

export class SourceUnavailableError extends InstanceExecutionError {
  static readonly code = "source_unavailable";
  static readonly guidance =
    "restore source access or select another source before retrying";
}

export class RecoveryUnavailableError extends InstanceExecutionError {
  static readonly code = "recovery_unavailable";
  static readonly guidance = "abort and restore the exact source and recovery binding";
}

export class FrameworkIntegrationError extends SDKError {
  static readonly code: string = "framework_integration_error";
  static readonly guidance: string =
    "fix the framework installation or adapter lifecycle before retrying";
  static readonly configurationFix = true;
}

export class FrameworkCompatibilityError extends FrameworkIntegrationError {
  static readonly code = "framework_compatibility_error";
  static readonly guidance = "install the exact certified framework version";
  static readonly versionChange = true;
}

export class ArtifactIntegrityError extends InstanceExecutionError {
  static readonly code = "artifact_integrity_error";
  static readonly guidance =
    "abort and replace the artifact with a digest-verified copy";
}

// Explicit-suffix aliases ease migration from adapters that use that spelling.
export const InstanceUnavailableError = InstanceUnavailable;
export const InstanceTimeoutError = InstanceTimeout;
export const InstanceProtocolErrorError = InstanceProtocolError;
export const InstanceRejectedError = InstanceRejected;
export const InstanceExecutionErrorError = InstanceExecutionError;

// Deprecated Engine names (removed in 2.0). Each is the same class object, so
// `instanceof`, `catch` and static fields behave exactly as before.
/** @deprecated since 1.3.0, use InstanceError */
export const EngineError = InstanceError;
/** @deprecated since 1.3.0, use InstanceError */
export type EngineError = InstanceError;
/** @deprecated since 1.3.0, use InstanceUnavailable */
export const EngineUnavailable = InstanceUnavailable;
/** @deprecated since 1.3.0, use InstanceUnavailable */
export type EngineUnavailable = InstanceUnavailable;
/** @deprecated since 1.3.0, use InstanceTimeout */
export const EngineTimeout = InstanceTimeout;
/** @deprecated since 1.3.0, use InstanceTimeout */
export type EngineTimeout = InstanceTimeout;
/** @deprecated since 1.3.0, use InstanceCrashed */
export const EngineCrashed = InstanceCrashed;
/** @deprecated since 1.3.0, use InstanceCrashed */
export type EngineCrashed = InstanceCrashed;
/** @deprecated since 1.3.0, use InstanceProtocolError */
export const EngineProtocolError = InstanceProtocolError;
/** @deprecated since 1.3.0, use InstanceProtocolError */
export type EngineProtocolError = InstanceProtocolError;
/** @deprecated since 1.3.0, use UnsupportedInstanceError */
export const UnsupportedEngineError = UnsupportedInstanceError;
/** @deprecated since 1.3.0, use UnsupportedInstanceError */
export type UnsupportedEngineError = UnsupportedInstanceError;
/** @deprecated since 1.3.0, use InstanceRejected */
export const EngineRejected = InstanceRejected;
/** @deprecated since 1.3.0, use InstanceRejected */
export type EngineRejected = InstanceRejected;
/** @deprecated since 1.3.0, use InstanceExecutionError */
export const EngineExecutionError = InstanceExecutionError;
/** @deprecated since 1.3.0, use InstanceExecutionError */
export type EngineExecutionError = InstanceExecutionError;
/** @deprecated since 1.3.0, use InstanceUnavailableError */
export const EngineUnavailableError = InstanceUnavailable;
/** @deprecated since 1.3.0, use InstanceTimeoutError */
export const EngineTimeoutError = InstanceTimeout;
/** @deprecated since 1.3.0, use InstanceProtocolErrorError */
export const EngineProtocolErrorError = InstanceProtocolError;
/** @deprecated since 1.3.0, use InstanceRejectedError */
export const EngineRejectedError = InstanceRejected;
/** @deprecated since 1.3.0, use InstanceExecutionErrorError */
export const EngineExecutionErrorError = InstanceExecutionError;
