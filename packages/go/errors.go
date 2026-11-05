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

// InstanceError is the base for failures at the Engine process boundary.
type InstanceError struct{ SDKError }

func newInstanceError(message, code, guidance string) InstanceError {
	return InstanceError{SDKError: SDKError{errorMeta: makeMeta(message, code, guidance)}}
}

// InstanceUnavailable means the configured Engine could not be started.
type InstanceUnavailable struct{ InstanceError }

func NewInstanceUnavailable(message string) *InstanceUnavailable {
	e := &InstanceUnavailable{InstanceError: newInstanceError(message, "engine_unavailable", "restore the configured instance binary or use explicit bounded fail-open")}
	e.Retryable, e.DegradeAllowed, e.AbortRequired, e.ConfigurationFix = true, true, false, true
	return e
}

// InstanceTimeout means the Engine exceeded its bounded deadline.
type InstanceTimeout struct{ InstanceError }

func NewInstanceTimeout(message string) *InstanceTimeout {
	e := &InstanceTimeout{InstanceError: newInstanceError(message, "engine_timeout", "retry within host policy or use explicit bounded fail-open")}
	e.Retryable, e.DegradeAllowed, e.AbortRequired = true, true, false
	return e
}

// InstanceCrashed means a persistent Agent Tools Engine exited unexpectedly.
type InstanceCrashed struct{ InstanceError }

func NewInstanceCrashed(message string) *InstanceCrashed {
	return &InstanceCrashed{InstanceError: newInstanceError(message, "engine_crashed", "create a new AgentContext; mutation and execution calls are never retried")}
}

// AgentPermissionError means the immutable AgentContext policy denied a call.
type AgentPermissionError struct{ InstanceError }

func NewAgentPermissionError(message string) *AgentPermissionError {
	e := &AgentPermissionError{InstanceError: newInstanceError(message, "agent_permission_denied", "create a new AgentContext with the required explicit permission")}
	e.ConfigurationFix = true
	return e
}

// UnsupportedCapabilityError means the negotiated Engine lacks a requested tool.
type UnsupportedCapabilityError struct{ InstanceError }

func NewUnsupportedCapabilityError(message string) *UnsupportedCapabilityError {
	e := &UnsupportedCapabilityError{InstanceError: newInstanceError(message, "unsupported_capability", "install a compatible instance or choose a negotiated capability")}
	e.VersionChange = true
	return e
}

// InstanceProtocolError means a wire or process-boundary contract was violated.
type InstanceProtocolError struct{ InstanceError }

func NewInstanceProtocolError(message string) *InstanceProtocolError {
	return &InstanceProtocolError{InstanceError: newInstanceError(message, "engine_protocol_error", "fail closed and verify instance interface, schema, and transport")}
}

// CompatibilityError means versioned dependencies are incompatible.
type CompatibilityError struct{ InstanceProtocolError }

func NewCompatibilityError(message string) *CompatibilityError {
	e := &CompatibilityError{InstanceProtocolError: InstanceProtocolError{InstanceError: newInstanceError(message, "compatibility_error", "install a supported version from the compatibility matrix")}}
	e.VersionChange = true
	return e
}

// UnsupportedInstanceError means Engine identity or capability is unsupported.
type UnsupportedInstanceError struct{ CompatibilityError }

func NewUnsupportedInstanceError(message string) *UnsupportedInstanceError {
	e := &UnsupportedInstanceError{CompatibilityError: CompatibilityError{InstanceProtocolError: InstanceProtocolError{InstanceError: newInstanceError(message, "unsupported_engine", "install an instance identity and capability supported by this SDK")}}}
	e.VersionChange = true
	return e
}

// InstanceRejected means the Engine validly rejected a request.
type InstanceRejected struct {
	InstanceError
	Failure *ContextFailure
	View    *ContextView
}

func newInstanceRejected(message, code, guidance string, failure *ContextFailure, view *ContextView) InstanceRejected {
	e := InstanceRejected{InstanceError: newInstanceError(message, code, guidance), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

func NewInstanceRejected(message string, failure *ContextFailure, view *ContextView) *InstanceRejected {
	e := &InstanceRejected{InstanceError: newInstanceError(message, "engine_rejected", "fail closed and satisfy the reported instance policy"), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

// PolicyAdmissionError is the typed Engine policy rejection.
type PolicyAdmissionError struct{ InstanceRejected }

func NewPolicyAdmissionError(message string, failure *ContextFailure, view *ContextView) *PolicyAdmissionError {
	e := &PolicyAdmissionError{InstanceRejected: newInstanceRejected(message, "policy_admission_rejected", "abort or change the request to satisfy the reported instance policy", failure, view)}
	e.ConfigurationFix = true
	return e
}

// InstanceExecutionError means the Engine returned a valid failed observation.
type InstanceExecutionError struct {
	InstanceError
	Failure *ContextFailure
	View    *ContextView
}

func newInstanceExecution(message, code, guidance string, failure *ContextFailure, view *ContextView) InstanceExecutionError {
	e := InstanceExecutionError{InstanceError: newInstanceError(message, code, guidance), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

func NewInstanceExecutionError(message string, failure *ContextFailure, view *ContextView) *InstanceExecutionError {
	e := &InstanceExecutionError{InstanceError: newInstanceError(message, "engine_execution_error", "fail closed and retain the factual instance failure evidence"), Failure: failure, View: view}
	if failure != nil {
		e.Retryable = failure.RetryableByHost
	}
	return e
}

// SourceUnavailableError means the selected source is unavailable.
type SourceUnavailableError struct{ InstanceExecutionError }

func NewSourceUnavailableError(message string, failure *ContextFailure, view *ContextView) *SourceUnavailableError {
	return &SourceUnavailableError{InstanceExecutionError: newInstanceExecution(message, "source_unavailable", "restore source access or select another source before retrying", failure, view)}
}

// RecoveryUnavailableError means exact source recovery is not available.
type RecoveryUnavailableError struct{ InstanceExecutionError }

func NewRecoveryUnavailableError(message string) *RecoveryUnavailableError {
	return &RecoveryUnavailableError{InstanceExecutionError: newInstanceExecution(message, "recovery_unavailable", "abort and restore the exact source and recovery binding", nil, nil)}
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
type ArtifactIntegrityError struct{ InstanceExecutionError }

func NewArtifactIntegrityError(message string, failure *ContextFailure, view *ContextView) *ArtifactIntegrityError {
	return &ArtifactIntegrityError{InstanceExecutionError: newInstanceExecution(message, "artifact_integrity_error", "abort and replace the artifact with a digest-verified copy", failure, view)}
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
