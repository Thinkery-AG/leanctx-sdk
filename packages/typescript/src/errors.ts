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

export class EngineError extends SDKError {
  static readonly code: string = "engine_error";
  static readonly guidance: string = "preserve Engine evidence and classify the concrete error";

  constructor(message?: string, options?: ErrorOptions) {
    super(message);
    if (options?.cause !== undefined) this.cause = options.cause;
  }
}

export class EngineUnavailable extends EngineError {
  static readonly code = "engine_unavailable";
  static readonly guidance =
    "restore the configured Engine binary or use explicit bounded fail-open";
  static readonly retryable = true;
  static readonly degradeAllowed = true;
  static readonly abortRequired = false;
  static readonly configurationFix = true;
}

export class EngineTimeout extends EngineError {
  static readonly code = "engine_timeout";
  static readonly guidance = "retry within host policy or use explicit bounded fail-open";
  static readonly retryable = true;
  static readonly degradeAllowed = true;
  static readonly abortRequired = false;
}

export class EngineCrashed extends EngineError {
  static readonly code = "engine_crashed";
  static readonly guidance =
    "create a new AgentContext; mutation and execution calls are never retried";
}

export class AgentPermissionError extends EngineError {
  static readonly code = "agent_permission_denied";
  static readonly guidance =
    "create a new AgentContext with the required explicit permission";
  static readonly configurationFix = true;
}

export class UnsupportedCapabilityError extends EngineError {
  static readonly code = "unsupported_capability";
  static readonly guidance =
    "install a compatible Engine or choose a negotiated capability";
  static readonly versionChange = true;
}

export class EngineProtocolError extends EngineError {
  static readonly code: string = "engine_protocol_error";
  static readonly guidance: string =
    "fail closed and verify Engine interface, schema, and transport";
}

export class CompatibilityError extends EngineProtocolError {
  static readonly code: string = "compatibility_error";
  static readonly guidance: string =
    "install a supported version from the compatibility matrix";
  static readonly versionChange = true;
}

export class UnsupportedEngineError extends CompatibilityError {
  static readonly code = "unsupported_engine";
  static readonly guidance =
    "install an Engine identity and capability supported by this SDK";
}

export type FailureLike = { readonly retryableByHost?: boolean };

export class EngineRejected extends EngineError {
  static readonly code: string = "engine_rejected";
  static readonly guidance: string = "fail closed and satisfy the reported Engine policy";
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

export class PolicyAdmissionError extends EngineRejected {
  static readonly code = "policy_admission_rejected";
  static readonly guidance =
    "abort or change the request to satisfy the reported Engine policy";
  static readonly configurationFix = true;
}

export class EngineExecutionError extends EngineError {
  static readonly code: string = "engine_execution_error";
  static readonly guidance: string = "fail closed and retain the factual Engine failure evidence";
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

export class SourceUnavailableError extends EngineExecutionError {
  static readonly code = "source_unavailable";
  static readonly guidance =
    "restore source access or select another source before retrying";
}

export class RecoveryUnavailableError extends EngineExecutionError {
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

export class ArtifactIntegrityError extends EngineExecutionError {
  static readonly code = "artifact_integrity_error";
  static readonly guidance =
    "abort and replace the artifact with a digest-verified copy";
}

// Explicit-suffix aliases ease migration from adapters that use that spelling.
export const EngineUnavailableError = EngineUnavailable;
export const EngineTimeoutError = EngineTimeout;
export const EngineProtocolErrorError = EngineProtocolError;
export const EngineRejectedError = EngineRejected;
export const EngineExecutionErrorError = EngineExecutionError;
