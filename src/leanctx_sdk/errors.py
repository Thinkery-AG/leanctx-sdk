"""Small, stable error taxonomy for the local Product SDK."""


class SDKError(Exception):
    """Base class for Product SDK failures."""

    code = "sdk_error"
    guidance = "inspect the stable error code and preserve original evidence"
    retryable = False
    degrade_allowed = False
    abort_required = True
    configuration_fix = False
    version_change = False

    def as_dict(self):
        """Return non-secret, deterministic host guidance."""
        return {
            "abort_required": self.abort_required,
            "code": self.code,
            "configuration_fix": self.configuration_fix,
            "degrade_allowed": self.degrade_allowed,
            "guidance": self.guidance,
            "retryable": self.retryable,
            "version_change": self.version_change,
        }


class ValidationError(SDKError, ValueError):
    """A caller supplied an invalid Product value."""

    code = "validation_error"
    guidance = "fix caller input before retrying"


class ConfigurationError(SDKError):
    """SDK or adapter configuration must be corrected before retrying."""

    code = "configuration_error"
    guidance = "fix SDK configuration before retrying"
    configuration_fix = True


class SessionStateError(SDKError):
    """An operation is not legal in the current session state."""

    code = "session_state_error"
    guidance = "fix lifecycle ordering or create a new session"


class WorkspaceError(SDKError):
    """Base class for durable Product workspace failures."""

    code = "workspace_error"

    def __init__(self, message=None, *, status=None):
        self.status = status
        super().__init__(message or self.code)


class WorkspaceNotFoundError(WorkspaceError):
    code = "workspace_not_found"


class WorkspaceAlreadyExistsError(WorkspaceError):
    code = "workspace_already_exists"


class WorkspaceValidationError(WorkspaceError, ValueError):
    code = "workspace_invalid"


class WorkspacePolicyError(WorkspaceError):
    code = "workspace_policy_rejected"


class WorkspaceSensitiveDataError(WorkspaceError):
    code = "workspace_sensitive_data"

    def __init__(self, field_name="value"):
        if (
            not isinstance(field_name, str)
            or not field_name
            or any(not (char.isalnum() or char in "._-") for char in field_name)
        ):
            field_name = "value"
        self.field_name = field_name
        super().__init__(f"{self.code}:{field_name}")


class WorkspaceLifecycleError(WorkspaceError):
    code = "workspace_lifecycle"


class WorkspaceCorruptError(WorkspaceError):
    code = "workspace_corrupt"


class WorkspaceIncompatibleError(WorkspaceError):
    code = "workspace_incompatible"


class WorkspaceConflictError(WorkspaceError):
    code = "workspace_conflict"


class WorkspaceLockError(WorkspaceError):
    code = "workspace_lock"


class WorkspaceIOError(WorkspaceError):
    code = "workspace_io"


class EngineError(SDKError):
    """Base class for failures at the public Engine process boundary."""

    code = "engine_error"
    guidance = "preserve Engine evidence and classify the concrete error"

    def __init__(self, message=""):
        super().__init__(message or self.code)


class EngineUnavailable(EngineError):
    """The configured Engine could not be started."""

    code = "engine_unavailable"
    guidance = "restore the configured Engine binary or use explicit bounded fail-open"
    retryable = True
    degrade_allowed = True
    abort_required = False
    configuration_fix = True


class EngineTimeout(EngineError):
    """The Engine exceeded its bounded deadline."""

    code = "engine_timeout"
    guidance = "retry within host policy or use explicit bounded fail-open"
    retryable = True
    degrade_allowed = True
    abort_required = False


class EngineCrashed(EngineError):
    """The persistent Agent Tools Engine process exited unexpectedly."""

    code = "engine_crashed"
    guidance = (
        "create a new AgentContext; mutation and execution calls are never retried"
    )


class AgentPermissionError(EngineError):
    """The immutable AgentContext policy rejected a tool call."""

    code = "agent_permission_denied"
    guidance = "create a new AgentContext with the required explicit permission"
    configuration_fix = True


class UnsupportedCapabilityError(EngineError):
    """The connected Engine did not negotiate the requested capability."""

    code = "unsupported_capability"
    guidance = "install a compatible Engine or choose a negotiated capability"
    version_change = True


class EngineProtocolError(EngineError):
    """The Engine response or process boundary violated the wire contract."""

    code = "engine_protocol_error"
    guidance = "fail closed and verify Engine interface, schema, and transport"


class CompatibilityError(EngineProtocolError):
    """The SDK and a dependency expose incompatible versioned contracts."""

    code = "compatibility_error"
    guidance = "install a supported version from the compatibility matrix"
    version_change = True


class UnsupportedEngineError(CompatibilityError):
    """The configured Engine identity or capability is unsupported."""

    code = "unsupported_engine"
    guidance = "install an Engine identity and capability supported by this SDK"


class EngineRejected(EngineError):
    """The Engine validly rejected a request for policy or security reasons."""

    code = "engine_rejected"
    guidance = "fail closed and satisfy the reported Engine policy"

    def __init__(self, message="", *, failure=None, view=None):
        super().__init__(message or self.code)
        self.failure = failure
        self.view = view
        if failure is not None:
            self.retryable = bool(getattr(failure, "retryable_by_host", False))


class PolicyAdmissionError(EngineRejected):
    """The Engine rejected the request at policy admission."""

    code = "policy_admission_rejected"
    guidance = "abort or change the request to satisfy the reported Engine policy"
    configuration_fix = True


class EngineExecutionError(EngineError):
    """The Engine returned a valid failed observation."""

    code = "engine_execution_error"
    guidance = "fail closed and retain the factual Engine failure evidence"

    def __init__(self, message="", *, failure=None, view=None):
        super().__init__(message or self.code)
        self.failure = failure
        self.view = view
        if failure is not None:
            self.retryable = bool(getattr(failure, "retryable_by_host", False))


class SourceUnavailableError(EngineExecutionError):
    """The selected source cannot currently be read by the Engine."""

    code = "source_unavailable"
    guidance = "restore source access or select another source before retrying"


class RecoveryUnavailableError(EngineExecutionError):
    """Exact recovery cannot be completed from the available binding."""

    code = "recovery_unavailable"
    guidance = "abort and restore the exact source and recovery binding"


class FrameworkIntegrationError(SDKError):
    """A supported host framework is missing or cannot satisfy its adapter contract."""

    code = "framework_integration_error"
    guidance = "fix the framework installation or adapter lifecycle before retrying"
    configuration_fix = True


class FrameworkCompatibilityError(FrameworkIntegrationError):
    """The installed host framework version is outside the certified matrix."""

    code = "framework_compatibility_error"
    guidance = "install the exact certified framework version"
    version_change = True


class ArtifactIntegrityError(EngineExecutionError):
    """Artifact, receipt, or source evidence failed an integrity check."""

    code = "artifact_integrity_error"
    guidance = "abort and replace the artifact with a digest-verified copy"


# Friendly aliases retained inside the private staging API for callers that
# prefer the explicit ``Error`` suffix.
EngineUnavailableError = EngineUnavailable
EngineTimeoutError = EngineTimeout
EngineProtocolErrorError = EngineProtocolError
EngineRejectedError = EngineRejected
EngineExecutionErrorError = EngineExecutionError
