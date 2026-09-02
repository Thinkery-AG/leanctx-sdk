package leanctx

import "fmt"

// ErrorGuidance is the stable, non-secret host guidance projection.
type ErrorGuidance struct {
	AbortRequired    bool   `json:"abort_required"`
	Code             string `json:"code"`
	ConfigurationFix bool   `json:"configuration_fix"`
	DegradeAllowed   bool   `json:"degrade_allowed"`
	Guidance         string `json:"guidance"`
	Retryable        bool   `json:"retryable"`
	VersionChange    bool   `json:"version_change"`
}

type errorMeta struct {
	Message          string
	Code             string
	Guidance         string
	Retryable        bool
	DegradeAllowed   bool
	AbortRequired    bool
	ConfigurationFix bool
	VersionChange    bool
	Cause            error
}

func (e *errorMeta) Error() string {
	if e == nil {
		return "sdk_error"
	}
	if e.Message != "" {
		return e.Message
	}
	return e.Code
}

func (e *errorMeta) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Cause
}

func (e *errorMeta) AsDict() ErrorGuidance {
	return ErrorGuidance{
		AbortRequired:    e.AbortRequired,
		Code:             e.Code,
		ConfigurationFix: e.ConfigurationFix,
		DegradeAllowed:   e.DegradeAllowed,
		Guidance:         e.Guidance,
		Retryable:        e.Retryable,
		VersionChange:    e.VersionChange,
	}
}

func makeMeta(message, code, guidance string) errorMeta {
	if message == "" {
		message = code
	}
	return errorMeta{
		Message:       message,
		Code:          code,
		Guidance:      guidance,
		AbortRequired: true,
	}
}

// SDKError is the base for every public SDK failure.
type SDKError struct{ errorMeta }

func (e *SDKError) Error() string               { return e.errorMeta.Error() }
func (e *SDKError) Unwrap() error               { return e.errorMeta.Unwrap() }
func (e *SDKError) AsDict() ErrorGuidance       { return e.errorMeta.AsDict() }
func (e *SDKError) CodeValue() string           { return e.Code }
func (e *SDKError) GuidanceValue() string       { return e.Guidance }
func (e *SDKError) RetryableValue() bool        { return e.Retryable }
func (e *SDKError) DegradeAllowedValue() bool   { return e.DegradeAllowed }
func (e *SDKError) AbortRequiredValue() bool    { return e.AbortRequired }
func (e *SDKError) ConfigurationFixValue() bool { return e.ConfigurationFix }
func (e *SDKError) VersionChangeValue() bool    { return e.VersionChange }

func newSDKError(message, code, guidance string) SDKError {
	return SDKError{errorMeta: makeMeta(message, code, guidance)}
}

// ValidationError means caller input is invalid.
type ValidationError struct{ SDKError }

func NewValidationError(message string) *ValidationError {
	return &ValidationError{SDKError: newSDKError(message, "validation_error", "fix caller input before retrying")}
}

// ConfigurationError means SDK configuration must be corrected.
type ConfigurationError struct{ SDKError }

func NewConfigurationError(message string) *ConfigurationError {
	e := &ConfigurationError{SDKError: newSDKError(message, "configuration_error", "fix SDK configuration before retrying")}
	e.ConfigurationFix = true
	return e
}

// SessionStateError means an operation is illegal in the current lifecycle state.
type SessionStateError struct{ SDKError }

func NewSessionStateError(message string) *SessionStateError {
	return &SessionStateError{SDKError: newSDKError(message, "session_state_error", "fix lifecycle ordering or create a new session")}
}

// EngineError is the base for failures at the Engine process boundary.
type EngineError struct{ SDKError }

func newEngineError(message, code, guidance string) EngineError {
	return EngineError{SDKError: SDKError{errorMeta: makeMeta(message, code, guidance)}}
}

// EngineUnavailable means the configured Engine could not be started.
type EngineUnavailable struct{ EngineError }

func NewEngineUnavailable(message string) *EngineUnavailable {
	e := &EngineUnavailable{EngineError: newEngineError(message, "engine_unavailable", "restore the configured Engine binary or use explicit bounded fail-open")}
	e.Retryable, e.DegradeAllowed, e.AbortRequired, e.ConfigurationFix = true, true, false, true
	return e
}

// EngineTimeout means the Engine exceeded its bounded deadline.
type EngineTimeout struct{ EngineError }

func NewEngineTimeout(message string) *EngineTimeout {
	e := &EngineTimeout{EngineError: newEngineError(message, "engine_timeout", "retry within host policy or use explicit bounded fail-open")}
	e.Retryable, e.DegradeAllowed, e.AbortRequired = true, true, false
	return e
}

// EngineCrashed means a persistent Agent Tools Engine exited unexpectedly.
type EngineCrashed struct{ EngineError }

func NewEngineCrashed(message string) *EngineCrashed {
	return &EngineCrashed{EngineError: newEngineError(message, "engine_crashed", "create a new AgentContext; mutation and execution calls are never retried")}
}

// AgentPermissionError means the immutable AgentContext policy denied a call.
type AgentPermissionError struct{ EngineError }

func NewAgentPermissionError(message string) *AgentPermissionError {
	e := &AgentPermissionError{EngineError: newEngineError(message, "agent_permission_denied", "create a new AgentContext with the required explicit permission")}
	e.ConfigurationFix = true
	return e
}

// UnsupportedCapabilityError means the negotiated Engine lacks a requested tool.
type UnsupportedCapabilityError struct{ EngineError }

func NewUnsupportedCapabilityError(message string) *UnsupportedCapabilityError {
	e := &UnsupportedCapabilityError{EngineError: newEngineError(message, "unsupported_capability", "install a compatible Engine or choose a negotiated capability")}
	e.VersionChange = true
	return e
}

// EngineProtocolError means a wire or process-boundary contract was violated.
type EngineProtocolError struct{ EngineError }

func NewEngineProtocolError(message string) *EngineProtocolError {
	return &EngineProtocolError{EngineError: newEngineError(message, "engine_protocol_error", "fail closed and verify Engine interface, schema, and transport")}
}

// CompatibilityError means versioned dependencies are incompatible.
type CompatibilityError struct{ EngineProtocolError }

func NewCompatibilityError(message string) *CompatibilityError {
	e := &CompatibilityError{EngineProtocolError: EngineProtocolError{EngineError: newEngineError(message, "compatibility_error", "install a supported version from the compatibility matrix")}}
	e.VersionChange = true
	return e
}

// UnsupportedEngineError means Engine identity or capability is unsupported.
type UnsupportedEngineError struct{ CompatibilityError }

func NewUnsupportedEngineError(message string) *UnsupportedEngineError {
	e := &UnsupportedEngineError{CompatibilityError: CompatibilityError{EngineProtocolError: EngineProtocolError{EngineError: newEngineError(message, "unsupported_engine", "install an Engine identity and capability supported by this SDK")}}}
	e.VersionChange = true
	return e
}

// EngineRejected means the Engine validly rejected a request.
type EngineRejected struct {
	EngineError
	Failure *ContextFailure
	View    *ContextView
}

func newEngineRejected(message, code, guidance string, failure *ContextFailure, view *ContextView) EngineRejected {
	e := EngineRejected{EngineError: newEngineError(message, code, guidance), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

func NewEngineRejected(message string, failure *ContextFailure, view *ContextView) *EngineRejected {
	e := &EngineRejected{EngineError: newEngineError(message, "engine_rejected", "fail closed and satisfy the reported Engine policy"), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

// PolicyAdmissionError is the typed Engine policy rejection.
type PolicyAdmissionError struct{ EngineRejected }

func NewPolicyAdmissionError(message string, failure *ContextFailure, view *ContextView) *PolicyAdmissionError {
	e := &PolicyAdmissionError{EngineRejected: newEngineRejected(message, "policy_admission_rejected", "abort or change the request to satisfy the reported Engine policy", failure, view)}
	e.ConfigurationFix = true
	return e
}

// EngineExecutionError means the Engine returned a valid failed observation.
type EngineExecutionError struct {
	EngineError
	Failure *ContextFailure
	View    *ContextView
}

func newEngineExecution(message, code, guidance string, failure *ContextFailure, view *ContextView) EngineExecutionError {
	e := EngineExecutionError{EngineError: newEngineError(message, code, guidance), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

func NewEngineExecutionError(message string, failure *ContextFailure, view *ContextView) *EngineExecutionError {
	e := &EngineExecutionError{EngineError: newEngineError(message, "engine_execution_error", "fail closed and retain the factual Engine failure evidence"), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

// SourceUnavailableError means the selected source is unavailable.
type SourceUnavailableError struct{ EngineExecutionError }

func NewSourceUnavailableError(message string, failure *ContextFailure, view *ContextView) *SourceUnavailableError {
	return &SourceUnavailableError{EngineExecutionError: newEngineExecution(message, "source_unavailable", "restore source access or select another source before retrying", failure, view)}
}

// RecoveryUnavailableError means exact source recovery is not available.
type RecoveryUnavailableError struct{ EngineExecutionError }

func NewRecoveryUnavailableError(message string) *RecoveryUnavailableError {
	return &RecoveryUnavailableError{EngineExecutionError: newEngineExecution(message, "recovery_unavailable", "abort and restore the exact source and recovery binding", nil, nil)}
}

// FrameworkIntegrationError is reserved for host-framework adapters.
type FrameworkIntegrationError struct{ SDKError }

func NewFrameworkIntegrationError(message string) *FrameworkIntegrationError {
	e := &FrameworkIntegrationError{SDKError: newSDKError(message, "framework_integration_error", "fix the framework installation or adapter lifecycle before retrying")}
	e.ConfigurationFix = true
	return e
}

// FrameworkCompatibilityError means a host framework version is unsupported.
type FrameworkCompatibilityError struct{ FrameworkIntegrationError }

func NewFrameworkCompatibilityError(message string) *FrameworkCompatibilityError {
	e := &FrameworkCompatibilityError{FrameworkIntegrationError: FrameworkIntegrationError{SDKError: newSDKError(message, "framework_compatibility_error", "install the exact certified framework version")}}
	e.VersionChange = true
	return e
}

// ArtifactIntegrityError means an evidence artifact failed verification.
type ArtifactIntegrityError struct{ EngineExecutionError }

func NewArtifactIntegrityError(message string, failure *ContextFailure, view *ContextView) *ArtifactIntegrityError {
	return &ArtifactIntegrityError{EngineExecutionError: newEngineExecution(message, "artifact_integrity_error", "abort and replace the artifact with a digest-verified copy", failure, view)}
}

// WithCause returns a typed error carrying an underlying cause without exposing
// it through the deterministic guidance projection.
func WithCause(err error, cause error) error {
	if err == nil {
		return cause
	}
	switch e := err.(type) {
	case *SDKError:
		e.Cause = cause
	case *ValidationError:
		e.Cause = cause
	case *ConfigurationError:
		e.Cause = cause
	case *SessionStateError:
		e.Cause = cause
	default:
		return fmt.Errorf("%w: %v", err, cause)
	}
	return err
}
