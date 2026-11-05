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

/// <summary>Base of every failure that comes from a LeanCTX instance or its protocol.</summary>
/// <remarks>
/// SDK 1.3 renamed the Engine error types to Instance names. .NET cannot alias a type
/// across assemblies, so until 2.0 the hierarchy keeps both names catchable: the
/// deprecated <c>EngineError</c> derives from <see cref="InstanceError"/>, every concrete
/// error derives from <c>EngineError</c>, and each deprecated Engine type derives from its
/// Instance type (for example <c>EngineUnavailable : InstanceUnavailable</c>). The SDK keeps
/// throwing the deprecated subtypes, so both <c>catch (InstanceUnavailable)</c> and
/// <c>catch (EngineUnavailable)</c> catch what it throws. Error codes such as
/// <c>engine_unavailable</c> are contract values and are unchanged.
/// </remarks>
public class InstanceError : SDKError
{
    public InstanceError(string? message = null, Exception? innerException = null)
        : base(message, "engine_error", "preserve LeanCTX instance evidence and classify the concrete error",
            innerException: innerException)
    { }

    protected InstanceError(
        string? message,
        string code = "engine_error",
        string guidance = "preserve LeanCTX instance evidence and classify the concrete error",
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

#pragma warning disable CS0618 // Until 2.0 the hierarchy runs through the deprecated Engine names.

/// <summary>Deprecated name of <see cref="InstanceError"/>; every SDK instance error derives from it until 2.0.</summary>
[Obsolete("Use InstanceError; removed in 2.0")]
public class EngineError : InstanceError
{
    public EngineError(string? message = null, Exception? innerException = null)
        : base(message, innerException)
    { }

    protected EngineError(
        string? message,
        string code = "engine_error",
        string guidance = "preserve LeanCTX instance evidence and classify the concrete error",
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

public class InstanceUnavailable : EngineError
{
    public InstanceUnavailable(string? message = null, Exception? innerException = null)
        : base(message, "engine_unavailable", "restore the configured LeanCTX instance binary or use explicit bounded fail-open", true, true, false, true, false, innerException) { }
}

/// <summary>Deprecated name of <see cref="InstanceUnavailable"/>; the type the SDK throws until 2.0.</summary>
[Obsolete("Use InstanceUnavailable; removed in 2.0")]
public class EngineUnavailable : InstanceUnavailable
{
    public EngineUnavailable(string? message = null, Exception? innerException = null)
        : base(message, innerException) { }
}

public class InstanceTimeout : EngineError
{
    public InstanceTimeout(string? message = null, Exception? innerException = null)
        : base(message, "engine_timeout", "retry within host policy or use explicit bounded fail-open", true, true, false, false, false, innerException) { }
}

/// <summary>Deprecated name of <see cref="InstanceTimeout"/>; the type the SDK throws until 2.0.</summary>
[Obsolete("Use InstanceTimeout; removed in 2.0")]
public class EngineTimeout : InstanceTimeout
{
    public EngineTimeout(string? message = null, Exception? innerException = null)
        : base(message, innerException) { }
}

public class InstanceCrashed : EngineError
{
    public InstanceCrashed(string? message = null, Exception? innerException = null)
        : base(message, "engine_crashed", "create a new AgentContext; mutation and execution calls are never retried", innerException: innerException) { }
}

/// <summary>Deprecated name of <see cref="InstanceCrashed"/>; the type the SDK throws until 2.0.</summary>
[Obsolete("Use InstanceCrashed; removed in 2.0")]
public class EngineCrashed : InstanceCrashed
{
    public EngineCrashed(string? message = null, Exception? innerException = null)
        : base(message, innerException) { }
}

public class AgentPermissionError : EngineError
{
    public AgentPermissionError(string? message = null, Exception? innerException = null)
        : base(message, "agent_permission_denied", "create a new AgentContext with the required explicit permission", configurationFix: true, innerException: innerException) { }
}

public class UnsupportedCapabilityError : EngineError
{
    public UnsupportedCapabilityError(string? message = null, Exception? innerException = null)
        : base(message, "unsupported_capability", "install a compatible LeanCTX instance or choose a negotiated capability", versionChange: true, innerException: innerException) { }
}

public class InstanceProtocolError : EngineError
{
    public InstanceProtocolError(string? message = null, Exception? innerException = null)
        : base(message, "engine_protocol_error", "fail closed and verify the Engine Interface, schema, and transport", innerException: innerException) { }
}

/// <summary>Deprecated name of <see cref="InstanceProtocolError"/>; the type the SDK throws until 2.0.</summary>
[Obsolete("Use InstanceProtocolError; removed in 2.0")]
public class EngineProtocolError : InstanceProtocolError
{
    public EngineProtocolError(string? message = null, Exception? innerException = null)
        : base(message, innerException) { }
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

public class UnsupportedInstanceError : CompatibilityError
{
    public UnsupportedInstanceError(string? message = null, Exception? innerException = null)
        : base(message, innerException)
    {
        Code = "unsupported_engine";
        Guidance = "install a LeanCTX instance identity and capability supported by this SDK";
    }
}

/// <summary>Deprecated name of <see cref="UnsupportedInstanceError"/>; the type the SDK throws until 2.0.</summary>
[Obsolete("Use UnsupportedInstanceError; removed in 2.0")]
public class UnsupportedEngineError : UnsupportedInstanceError
{
    public UnsupportedEngineError(string? message = null, Exception? innerException = null)
        : base(message, innerException) { }
}

public class InstanceRejected : EngineError
{
    public InstanceRejected(
        string? message = null,
        ContextFailure? failure = null,
        ContextView? view = null,
        Exception? innerException = null)
        : base(message, "engine_rejected", "fail closed and satisfy the reported LeanCTX instance policy",
            retryable: failure?.RetryableByHost ?? false, innerException: innerException)
    {
        Failure = failure;
        View = view;
    }

    public ContextFailure? Failure { get; }
    public ContextView? View { get; }
}

/// <summary>Deprecated name of <see cref="InstanceRejected"/>; the type the SDK throws until 2.0.</summary>
[Obsolete("Use InstanceRejected; removed in 2.0")]
public class EngineRejected : InstanceRejected
{
    public EngineRejected(
        string? message = null,
        ContextFailure? failure = null,
        ContextView? view = null,
        Exception? innerException = null)
        : base(message, failure, view, innerException) { }
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
        Guidance = "abort or change the request to satisfy the reported LeanCTX instance policy";
        ConfigurationFix = true;
    }
}

public class InstanceExecutionError : EngineError
{
    public InstanceExecutionError(
        string? message = null,
        ContextFailure? failure = null,
        ContextView? view = null,
        Exception? innerException = null)
        : base(message, "engine_execution_error", "fail closed and retain the factual LeanCTX instance failure evidence",
            retryable: failure?.RetryableByHost ?? false, innerException: innerException)
    {
        Failure = failure;
        View = view;
    }

    public ContextFailure? Failure { get; }
    public ContextView? View { get; }
}

/// <summary>Deprecated name of <see cref="InstanceExecutionError"/>; the type the SDK throws until 2.0.</summary>
[Obsolete("Use InstanceExecutionError; removed in 2.0")]
public class EngineExecutionError : InstanceExecutionError
{
    public EngineExecutionError(
        string? message = null,
        ContextFailure? failure = null,
        ContextView? view = null,
        Exception? innerException = null)
        : base(message, failure, view, innerException) { }
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

public class ArtifactIntegrityError : EngineExecutionError
{
    public ArtifactIntegrityError(string? message = null, ContextView? view = null, Exception? innerException = null)
        : base(message, null, view, innerException)
    {
        Code = "artifact_integrity_error";
        Guidance = "abort and replace the artifact with a digest-verified copy";
    }
}

#pragma warning restore CS0618

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
