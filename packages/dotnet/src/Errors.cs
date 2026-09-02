namespace Thinkery.LeanCtx;

/// <summary>Stable, non-secret guidance attached to every SDK failure.</summary>
public sealed record ErrorGuidance(
    string Code,
    string Guidance,
    bool Retryable,
    bool DegradeAllowed,
    bool AbortRequired,
    bool ConfigurationFix,
    bool VersionChange);

/// <summary>Base class for all Thinkery.LeanCtx failures.</summary>
public class SDKError : Exception
{
    public SDKError(string? message = null, Exception? innerException = null)
        : this(message, "sdk_error", "preserve original evidence and classify the concrete error",
            innerException: innerException)
    { }

    protected SDKError(
        string? message,
        string code,
        string guidance,
        bool retryable = false,
        bool degradeAllowed = false,
        bool abortRequired = true,
        bool configurationFix = false,
        bool versionChange = false,
        Exception? innerException = null)
        : base(message ?? code, innerException)
    {
        Code = code;
        Guidance = guidance;
        Retryable = retryable;
        DegradeAllowed = degradeAllowed;
        AbortRequired = abortRequired;
        ConfigurationFix = configurationFix;
        VersionChange = versionChange;
    }

    public string Code { get; protected set; }
    public string Guidance { get; protected set; }
    public bool Retryable { get; protected set; }
    public bool DegradeAllowed { get; protected set; }
    public bool AbortRequired { get; protected set; }
    public bool ConfigurationFix { get; protected set; }
    public bool VersionChange { get; protected set; }

    public ErrorGuidance AsGuidance() => new(
        Code,
        Guidance,
        Retryable,
        DegradeAllowed,
        AbortRequired,
        ConfigurationFix,
        VersionChange);

    public IReadOnlyDictionary<string, object> AsDictionary() =>
        new Dictionary<string, object>(StringComparer.Ordinal)
        {
            ["abort_required"] = AbortRequired,
            ["code"] = Code,
            ["configuration_fix"] = ConfigurationFix,
            ["degrade_allowed"] = DegradeAllowed,
            ["guidance"] = Guidance,
            ["retryable"] = Retryable,
            ["version_change"] = VersionChange,
        };
}

public class ValidationError : SDKError
{
    public ValidationError(string? message = null, Exception? innerException = null)
        : base(message, "validation_error", "fix caller input before retrying", innerException: innerException) { }
}

public class ConfigurationError : SDKError
{
    public ConfigurationError(string? message = null, Exception? innerException = null)
        : base(message, "configuration_error", "fix SDK configuration before retrying", configurationFix: true, innerException: innerException) { }
}

public class SessionStateError : SDKError
{
    public SessionStateError(string? message = null, Exception? innerException = null)
        : base(message, "session_state_error", "fix lifecycle ordering or create a new session", innerException: innerException) { }
}

public class EngineError : SDKError
{
    public EngineError(string? message = null, Exception? innerException = null)
        : base(message, "engine_error", "preserve Engine evidence and classify the concrete error",
            innerException: innerException)
    { }

    protected EngineError(
        string? message,
        string code = "engine_error",
        string guidance = "preserve Engine evidence and classify the concrete error",
        bool retryable = false,
        bool degradeAllowed = false,
        bool abortRequired = true,
        bool configurationFix = false,
        bool versionChange = false,
        Exception? innerException = null)
        : base(message, code, guidance, retryable, degradeAllowed, abortRequired,
            configurationFix, versionChange, innerException)
    { }
}

public class EngineUnavailable : EngineError
{
    public EngineUnavailable(string? message = null, Exception? innerException = null)
        : base(message, "engine_unavailable", "restore the configured Engine binary or use explicit bounded fail-open", true, true, false, true, false, innerException) { }
}

public class EngineTimeout : EngineError
{
    public EngineTimeout(string? message = null, Exception? innerException = null)
        : base(message, "engine_timeout", "retry within host policy or use explicit bounded fail-open", true, true, false, false, false, innerException) { }
}

public class EngineCrashed : EngineError
{
    public EngineCrashed(string? message = null, Exception? innerException = null)
        : base(message, "engine_crashed", "create a new AgentContext; mutation and execution calls are never retried", innerException: innerException) { }
}

public class AgentPermissionError : EngineError
{
    public AgentPermissionError(string? message = null, Exception? innerException = null)
        : base(message, "agent_permission_denied", "create a new AgentContext with the required explicit permission", configurationFix: true, innerException: innerException) { }
}

public class UnsupportedCapabilityError : EngineError
{
    public UnsupportedCapabilityError(string? message = null, Exception? innerException = null)
        : base(message, "unsupported_capability", "install a compatible Engine or choose a negotiated capability", versionChange: true, innerException: innerException) { }
}

public class EngineProtocolError : EngineError
{
    public EngineProtocolError(string? message = null, Exception? innerException = null)
        : base(message, "engine_protocol_error", "fail closed and verify Engine interface, schema, and transport", innerException: innerException) { }
}

public class CompatibilityError : EngineProtocolError
{
    public CompatibilityError(string? message = null, Exception? innerException = null)
        : base(message, innerException)
    {
        // Stable compatibility classification overrides the protocol default.
        Code = "compatibility_error";
        Guidance = "install a supported version from the compatibility matrix";
        VersionChange = true;
    }
}

public class UnsupportedEngineError : CompatibilityError
{
    public UnsupportedEngineError(string? message = null, Exception? innerException = null)
        : base(message, innerException)
    {
        Code = "unsupported_engine";
        Guidance = "install an Engine identity and capability supported by this SDK";
    }
}

public class EngineRejected : EngineError
{
    public EngineRejected(
        string? message = null,
        ContextFailure? failure = null,
        ContextView? view = null,
        Exception? innerException = null)
        : base(message, "engine_rejected", "fail closed and satisfy the reported Engine policy",
            retryable: failure?.RetryableByHost ?? false, innerException: innerException)
    {
        Failure = failure;
        View = view;
    }

    public ContextFailure? Failure { get; }
    public ContextView? View { get; }
}

public class PolicyAdmissionError : EngineRejected
{
    public PolicyAdmissionError(
        string? message = null,
        ContextFailure? failure = null,
        ContextView? view = null,
        Exception? innerException = null)
        : base(message, failure, view, innerException)
    {
        Code = "policy_admission_rejected";
        Guidance = "abort or change the request to satisfy the reported Engine policy";
        ConfigurationFix = true;
    }
}

public class EngineExecutionError : EngineError
{
    public EngineExecutionError(
        string? message = null,
        ContextFailure? failure = null,
        ContextView? view = null,
        Exception? innerException = null)
        : base(message, "engine_execution_error", "fail closed and retain the factual Engine failure evidence",
            retryable: failure?.RetryableByHost ?? false, innerException: innerException)
    {
        Failure = failure;
        View = view;
    }

    public ContextFailure? Failure { get; }
    public ContextView? View { get; }
}

public class SourceUnavailableError : EngineExecutionError
{
    public SourceUnavailableError(string? message = null, ContextView? view = null, Exception? innerException = null)
        : base(message, null, view, innerException)
    {
        Code = "source_unavailable";
        Guidance = "restore source access or select another source before retrying";
    }
}

public class RecoveryUnavailableError : EngineExecutionError
{
    public RecoveryUnavailableError(string? message = null, ContextView? view = null, Exception? innerException = null)
        : base(message, null, view, innerException)
    {
        Code = "recovery_unavailable";
        Guidance = "abort and restore the exact source and recovery binding";
    }
}

public class FrameworkIntegrationError : SDKError
{
    public FrameworkIntegrationError(string? message = null, Exception? innerException = null)
        : base(message, "framework_integration_error", "fix the framework installation or adapter lifecycle before retrying", configurationFix: true, innerException: innerException) { }
}

public class FrameworkCompatibilityError : FrameworkIntegrationError
{
    public FrameworkCompatibilityError(string? message = null, Exception? innerException = null)
        : base(message, innerException)
    {
        Code = "framework_compatibility_error";
        Guidance = "install the exact certified framework version";
        VersionChange = true;
    }
}

public class ArtifactIntegrityError : EngineExecutionError
{
    public ArtifactIntegrityError(string? message = null, ContextView? view = null, Exception? innerException = null)
        : base(message, null, view, innerException)
    {
        Code = "artifact_integrity_error";
        Guidance = "abort and replace the artifact with a digest-verified copy";
    }
}
