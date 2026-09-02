using System.Collections;
using System.Collections.ObjectModel;
using System.Text;

namespace Thinkery.LeanCtx;

/// <summary>Stable protocol and package constants.</summary>
public static class Constants
{
    public const string __version__ = "1.1.0";
    public const int SCHEMA_VERSION = 1;
    public const int TRANSPORT_VERSION = 1;
    public const string ENGINE_INTERFACE_VERSION = "1.0.0";
    public const string AGENT_TOOLS_INTERFACE_VERSION = "1.0.0";
    public const int AGENT_TOOLS_SCHEMA_VERSION = 1;
    public const int AGENT_TOOLS_TRANSPORT_VERSION = 1;
    public const string SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = "3.10.1";
}

public enum FailureCode
{
    PolicyRejected,
    SourceUnavailable,
    SourceIntegrityMismatch,
    ResourceLimit,
    UnsupportedOperation,
    Internal,
}

public enum SessionState
{
    Created,
    Planned,
    Executing,
    Completed,
    Aborted,
    Closed,
}

public enum EngineStatus
{
    Succeeded,
    Degraded,
    Rejected,
    Failed,
}

public enum HostOutcome
{
    Unknown,
    Accepted,
    Rejected,
    Completed,
    Failed,
    Aborted,
}

public enum Integrity
{
    Sealed,
    Unsealed,
}

public enum Freshness
{
    Reuse,
    Refresh,
}

public enum ReadMode
{
    Auto,
    Full,
    Raw,
    Signatures,
    Map,
    Diff,
    Reference,
    Task,
    Anchored,
}

internal static class ProtocolText
{
    internal static string FailureCodeText(FailureCode value) => value switch
    {
        Thinkery.LeanCtx.FailureCode.PolicyRejected => "policy_rejected",
        Thinkery.LeanCtx.FailureCode.SourceUnavailable => "source_unavailable",
        Thinkery.LeanCtx.FailureCode.SourceIntegrityMismatch => "source_integrity_mismatch",
        Thinkery.LeanCtx.FailureCode.ResourceLimit => "resource_limit",
        Thinkery.LeanCtx.FailureCode.UnsupportedOperation => "unsupported_operation",
        _ => "internal",
    };

    internal static bool TryFailureCode(string value, out FailureCode result)
    {
        result = value switch
        {
            "policy_rejected" => FailureCode.PolicyRejected,
            "source_unavailable" => FailureCode.SourceUnavailable,
            "source_integrity_mismatch" => FailureCode.SourceIntegrityMismatch,
            "resource_limit" => FailureCode.ResourceLimit,
            "unsupported_operation" => FailureCode.UnsupportedOperation,
            "internal" => FailureCode.Internal,
            _ => default,
        };
        return value is "policy_rejected" or "source_unavailable" or
            "source_integrity_mismatch" or "resource_limit" or
            "unsupported_operation" or "internal";
    }

    internal static string EngineStatusText(EngineStatus value) => value switch
    {
        Thinkery.LeanCtx.EngineStatus.Succeeded => "succeeded",
        Thinkery.LeanCtx.EngineStatus.Degraded => "degraded",
        Thinkery.LeanCtx.EngineStatus.Rejected => "rejected",
        _ => "failed",
    };

    internal static bool TryEngineStatus(string value, out EngineStatus result)
    {
        result = value switch
        {
            "succeeded" => EngineStatus.Succeeded,
            "degraded" => EngineStatus.Degraded,
            "rejected" => EngineStatus.Rejected,
            "failed" => EngineStatus.Failed,
            _ => default,
        };
        return value is "succeeded" or "degraded" or "rejected" or "failed";
    }

    internal static string HostOutcomeText(HostOutcome value) => value switch
    {
        Thinkery.LeanCtx.HostOutcome.Unknown => "unknown",
        Thinkery.LeanCtx.HostOutcome.Accepted => "accepted",
        Thinkery.LeanCtx.HostOutcome.Rejected => "rejected",
        Thinkery.LeanCtx.HostOutcome.Completed => "completed",
        Thinkery.LeanCtx.HostOutcome.Failed => "failed",
        _ => "aborted",
    };

    internal static bool TryHostOutcome(string value, out HostOutcome result)
    {
        result = value switch
        {
            "unknown" => HostOutcome.Unknown,
            "accepted" => HostOutcome.Accepted,
            "rejected" => HostOutcome.Rejected,
            "completed" => HostOutcome.Completed,
            "failed" => HostOutcome.Failed,
            "aborted" => HostOutcome.Aborted,
            _ => default,
        };
        return value is "unknown" or "accepted" or "rejected" or "completed" or
            "failed" or "aborted";
    }

    internal static string IntegrityText(Integrity value) => value == Thinkery.LeanCtx.Integrity.Sealed
        ? "sealed" : "unsealed";

    internal static bool TryIntegrity(string value, out Integrity result)
    {
        result = value switch
        {
            "sealed" => Thinkery.LeanCtx.Integrity.Sealed,
            "unsealed" => Thinkery.LeanCtx.Integrity.Unsealed,
            _ => default,
        };
        return value is "sealed" or "unsealed";
    }

    internal static string SessionStateText(SessionState value) => value switch
    {
        Thinkery.LeanCtx.SessionState.Created => "created",
        Thinkery.LeanCtx.SessionState.Planned => "planned",
        Thinkery.LeanCtx.SessionState.Executing => "executing",
        Thinkery.LeanCtx.SessionState.Completed => "completed",
        Thinkery.LeanCtx.SessionState.Aborted => "aborted",
        _ => "closed",
    };
}

public class ContextSource : IWireValue
{
    private readonly string path;
    private readonly string projectRoot;
    private readonly string mediaType;
    private readonly string? sourceRef;
    private readonly string? sourceDigest;

    public ContextSource(
        string path,
        string? projectRoot = null,
        string? mediaType = null,
        string? sourceRef = null,
        string? sourceDigest = null)
    {
        var suppliedPath = WireJson.Text(path, "path", WireJson.MaxPathBytes);
        projectRoot ??= Directory.GetCurrentDirectory();
        if (projectRoot.Contains('\0'))
            throw new ValidationError("project_root contains NUL");
        string root;
        try
        {
            root = System.IO.Path.GetFullPath(projectRoot);
        }
        catch (Exception error)
        {
            throw new ConfigurationError("project_root must be a path", error);
        }
        if (WireJson.Utf8(root, "project_root").Length > WireJson.MaxPathBytes)
            throw new ValidationError("project_root exceeds the path bound");
        var candidate = FullPath(root, suppliedPath);
        if (!Contained(candidate, root))
            throw new ValidationError("source path escapes project_root");
        this.path = System.IO.Path.IsPathRooted(suppliedPath)
            ? candidate
            : NormalizeRelative(suppliedPath);
        if (WireJson.Utf8(candidate, "path").Length > WireJson.MaxPathBytes)
            throw new ValidationError("path exceeds the path bound");
        this.projectRoot = root;
        this.mediaType = WireJson.Text(mediaType ?? "text/plain", "media_type", WireJson.MaxRefBytes);
        this.sourceRef = sourceRef is null ? null : WireJson.ValidateRef(sourceRef, "source_ref");
        this.sourceDigest = sourceDigest is null ? null : WireJson.ValidateDigest(sourceDigest, "source_digest");
    }

    public string Path => path;
    public string ProjectRoot => projectRoot;
    public string MediaType => mediaType;
    public string? SourceRef => sourceRef;
    public string? SourceDigest => sourceDigest;
    public string RelativePath
    {
        get
        {
            var absolute = FullPath(projectRoot, path);
            if (!Contained(absolute, projectRoot))
                throw new ValidationError("source containment cannot be proven");
            var relative = NormalizeRelative(System.IO.Path.GetRelativePath(projectRoot, absolute));
            if (relative is "." or ".." || relative.StartsWith("../", StringComparison.Ordinal) ||
                relative.Any(character => character < 0x20))
                throw new ValidationError("source path must be a rooted relative file path");
            return relative;
        }
    }

    public string relative_path => RelativePath;
    public string project_root => ProjectRoot;
    public string media_type => MediaType;
    public string? source_ref => SourceRef;
    public string? source_digest => SourceDigest;

    public IReadOnlyDictionary<string, object?> Descriptor() =>
        new ReadOnlyDictionary<string, object?>(DescriptorMutable());

    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(DescriptorMutable(), StringComparer.Ordinal)
        {
            ["project_root"] = projectRoot,
        });

    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    object IWireValue.ToWireValue() => ToDictionary();

    private Dictionary<string, object?> DescriptorMutable()
    {
        var result = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["path"] = RelativePath,
            ["media_type"] = mediaType,
        };
        if (sourceRef is not null)
            result["source_ref"] = sourceRef;
        if (sourceDigest is not null)
            result["source_digest"] = sourceDigest;
        return result;
    }

    internal static string FullPath(string root, string pathValue)
    {
        try
        {
            return System.IO.Path.GetFullPath(
                System.IO.Path.IsPathRooted(pathValue)
                    ? pathValue
                    : System.IO.Path.Combine(root, pathValue));
        }
        catch (Exception error)
        {
            throw new ValidationError("path is invalid", error);
        }
    }

    internal static bool Contained(string candidate, string root)
    {
        var normalizedRoot = root.TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar);
        if (normalizedRoot.Length == 0)
            normalizedRoot = System.IO.Path.DirectorySeparatorChar.ToString();
        return candidate.Equals(normalizedRoot, StringComparison.Ordinal) ||
            candidate.StartsWith(normalizedRoot + System.IO.Path.DirectorySeparatorChar,
                StringComparison.Ordinal) ||
            candidate.StartsWith(normalizedRoot + System.IO.Path.AltDirectorySeparatorChar,
                StringComparison.Ordinal);
    }

    internal static string NormalizeRelative(string value) =>
        value.Replace(System.IO.Path.DirectorySeparatorChar, '/').Replace(
            System.IO.Path.AltDirectorySeparatorChar, '/');
}

public class ContextPlan : IWireValue
{
    public ContextPlan(
        string sessionId,
        string taskId,
        string task,
        ContextSource source,
        string mode = "aggressive",
        string freshness = "reuse")
    {
        SessionId = WireJson.Text(sessionId, "session_id", WireJson.MaxRefBytes);
        TaskId = WireJson.Text(taskId, "task_id", WireJson.MaxRefBytes);
        Task = WireJson.Text(task, "task", WireJson.MaxTaskBytes, controls: false);
        Source = source ?? throw new ValidationError("source must be ContextSource");
        Mode = mode ?? throw new ValidationError("mode must be aggressive in Engine Interface v1");
        Freshness = freshness ?? throw new ValidationError("freshness must be reuse or refresh");
        if (Mode != "aggressive")
            throw new ValidationError("mode must be aggressive in Engine Interface v1");
        if (Freshness is not "reuse" and not "refresh")
            throw new ValidationError("freshness must be reuse or refresh");
        PlanId = WireJson.Sha256Digest(WireJson.CanonicalBytes(ToIntent()))
            .Replace("sha256:", "plan:sha256:", StringComparison.Ordinal);
    }

    public ContextPlan(
        string sessionId,
        string taskId,
        string task,
        ContextSource source,
        string mode,
        Freshness freshness)
        : this(sessionId, taskId, task, source, mode,
            freshness == Thinkery.LeanCtx.Freshness.Reuse ? "reuse" : "refresh")
    { }

    public string SessionId { get; }
    public string TaskId { get; }
    public string Task { get; }
    public ContextSource Source { get; }
    public string Mode { get; }
    public string Freshness { get; }
    public string PlanId { get; }
    public string session_id => SessionId;
    public string task_id => TaskId;
    public string plan_id => PlanId;

    public IReadOnlyDictionary<string, object?> ToIntent() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["intent_version"] = 1L,
            ["session_id"] = SessionId,
            ["task_id"] = TaskId,
            ["task"] = Task,
            ["source"] = Source.Descriptor(),
            ["mode"] = Mode,
            ["freshness"] = Freshness,
        });

    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(ToIntent(), StringComparer.Ordinal)
        {
            ["plan_id"] = PlanId,
        });

    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    public IReadOnlyDictionary<string, object?> ToIntentDictionary() => ToIntent();
    object IWireValue.ToWireValue() => ToDictionary();
}

public class ContextMeasurement : IWireValue
{
    public ContextMeasurement(string name, string unit, string classification, object? value)
        : this(name, unit, classification, IntegralValue(value)) { }

    public ContextMeasurement(string name, string unit, string classification, long? value)
    {
        if (name is null || !System.Text.RegularExpressions.Regex.IsMatch(name, "^[a-z0-9_]+$"))
            throw new ValidationError("measurement name must be lowercase ASCII");
        if (unit is null || !System.Text.RegularExpressions.Regex.IsMatch(unit, "^[a-z0-9_]+$"))
            throw new ValidationError("measurement unit must be lowercase ASCII");
        if (classification is not "measured" and not "estimated" and not "unavailable")
            throw new ValidationError("invalid measurement classification");
        if (classification == "unavailable" && value is not null)
            throw new ValidationError("unavailable measurement value must be null");
        if (classification != "unavailable" && (value is null || value < 0))
            throw new ValidationError("measurement value must be a non-negative integer");
        Name = name;
        Unit = unit;
        Classification = classification;
        Value = value;
    }

    public string Name { get; }
    public string Unit { get; }
    public string Classification { get; }
    public long? Value { get; }
    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["name"] = Name,
            ["unit"] = Unit,
            ["classification"] = Classification,
            ["value"] = Value,
        });
    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    object IWireValue.ToWireValue() => ToDictionary();

    private static long? IntegralValue(object? rawValue)
    {
        if (rawValue is null)
            return null;
        if (rawValue is not double value || !double.IsFinite(value) ||
            value < long.MinValue || value > long.MaxValue || Math.Truncate(value) != value)
            throw new ValidationError("measurement value must be a non-negative integer");
        return checked((long?)value);
    }
}

public class ContextFailure : IWireValue
{
    public ContextFailure(FailureCode code, bool retryableByHost, string? recoveryRef)
    {
        if (!Enum.IsDefined(code))
            throw new ValidationError("invalid failure code");
        Code = code;
        RetryableByHost = retryableByHost;
        RecoveryRef = recoveryRef is null ? null : WireJson.ValidateRef(recoveryRef, "recovery_ref");
    }

    public ContextFailure(string code, bool retryableByHost, string? recoveryRef)
    {
        if (!ProtocolText.TryFailureCode(code, out var parsed))
            throw new ValidationError("invalid failure code");
        Code = parsed;
        RetryableByHost = retryableByHost;
        RecoveryRef = recoveryRef is null ? null : WireJson.ValidateRef(recoveryRef, "recovery_ref");
    }

    public FailureCode Code { get; }
    public bool RetryableByHost { get; }
    public string? RecoveryRef { get; }
    public string code => ProtocolText.FailureCodeText(Code);
    public bool retryable_by_host => RetryableByHost;
    public string? recovery_ref => RecoveryRef;
    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["code"] = ProtocolText.FailureCodeText(Code),
            ["retryable_by_host"] = RetryableByHost,
            ["recovery_ref"] = RecoveryRef,
        });
    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    object IWireValue.ToWireValue() => ToDictionary();
}

public class ContextReceiptLink : IWireValue
{
    public ContextReceiptLink(int schemaVersion, string receiptId, string receiptRef, string receiptDigest, string invocationId)
    {
        if (schemaVersion != Constants.SCHEMA_VERSION)
            throw new ValidationError("receipt link schema_version must be 1");
        SchemaVersion = schemaVersion;
        ReceiptId = WireJson.ValidateRef(receiptId, "receipt_id");
        ReceiptRef = WireJson.ValidateRef(receiptRef, "receipt_ref");
        ReceiptDigest = WireJson.ValidateDigest(receiptDigest, "receipt_digest");
        InvocationId = WireJson.Text(invocationId, "invocation_id", WireJson.MaxRefBytes);
        if (ReceiptRef != $"receipt:{ReceiptDigest}")
            throw new ValidationError("receipt_ref does not match receipt_digest");
    }

    public int SchemaVersion { get; }
    public string ReceiptId { get; }
    public string ReceiptRef { get; }
    public string ReceiptDigest { get; }
    public string InvocationId { get; }
    public int schema_version => SchemaVersion;
    public string receipt_id => ReceiptId;
    public string receipt_ref => ReceiptRef;
    public string receipt_digest => ReceiptDigest;
    public string invocation_id => InvocationId;
    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = (long)SchemaVersion,
            ["receipt_id"] = ReceiptId,
            ["receipt_ref"] = ReceiptRef,
            ["receipt_digest"] = ReceiptDigest,
            ["invocation_id"] = InvocationId,
        });
    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    object IWireValue.ToWireValue() => ToDictionary();
}

public class RecoveredSource : IWireValue
{
    public RecoveredSource(string text, string sourceRef, string sourceDigest, string recoveryRef)
    {
        Text = WireJson.Text(text, "recovered text", WireJson.MaxTextBytes, controls: false);
        SourceRef = WireJson.ValidateRef(sourceRef, "source_ref");
        SourceDigest = WireJson.ValidateDigest(sourceDigest, "source_digest");
        RecoveryRef = WireJson.ValidateRef(recoveryRef, "recovery_ref");
        if (WireJson.Sha256Digest(Text) != SourceDigest)
            throw new ValidationError("recovered text digest does not match source_digest");
    }

    public string Text { get; }
    public string SourceRef { get; }
    public string SourceDigest { get; }
    public string RecoveryRef { get; }
    public string source_ref => SourceRef;
    public string source_digest => SourceDigest;
    public string recovery_ref => RecoveryRef;
    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["text"] = Text,
            ["source_ref"] = SourceRef,
            ["source_digest"] = SourceDigest,
            ["recovery_ref"] = RecoveryRef,
        });
    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    object IWireValue.ToWireValue() => ToDictionary();
}

public class ContextView : IWireValue
{
    public ContextView(
        ContextSource source,
        string? text,
        string? outputRef,
        string? outputDigest,
        string sourceRef,
        string sourceDigest,
        string? recoveryRef,
        EngineStatus status,
        IReadOnlyList<ContextMeasurement>? measurements = null,
        ContextFailure? failure = null,
        ContextReceiptLink? receiptLink = null,
        IReadOnlyDictionary<string, object?>? invocation = null,
        IReadOnlyDictionary<string, object?>? observation = null,
        int schemaVersion = Constants.SCHEMA_VERSION,
        int transportVersion = Constants.TRANSPORT_VERSION,
        string engineInterfaceVersion = Constants.ENGINE_INTERFACE_VERSION)
        : this(source, text, outputRef, outputDigest, sourceRef, sourceDigest,
            recoveryRef, ProtocolText.EngineStatusText(status), measurements, failure,
            receiptLink, invocation, observation, schemaVersion, transportVersion,
            engineInterfaceVersion)
    { }

    public ContextView(
        ContextSource source,
        string? text,
        string? outputRef,
        string? outputDigest,
        string sourceRef,
        string sourceDigest,
        string? recoveryRef,
        string status,
        IReadOnlyList<ContextMeasurement>? measurements = null,
        ContextFailure? failure = null,
        ContextReceiptLink? receiptLink = null,
        IReadOnlyDictionary<string, object?>? invocation = null,
        IReadOnlyDictionary<string, object?>? observation = null,
        int schemaVersion = Constants.SCHEMA_VERSION,
        int transportVersion = Constants.TRANSPORT_VERSION,
        string engineInterfaceVersion = Constants.ENGINE_INTERFACE_VERSION)
    {
        Source = source ?? throw new ValidationError("view source must be ContextSource");
        if (text is not null && WireJson.Utf8(text, "view text").Length > WireJson.MaxTextBytes)
            throw new ValidationError("view text exceeds the bound");
        if (outputRef is not null)
            WireJson.ValidateOutputRef(outputRef);
        if (outputDigest is not null)
            WireJson.ValidateDigest(outputDigest, "output_digest");
        if ((outputRef is null) != (outputDigest is null))
            throw new ValidationError("output_ref and output_digest must be paired");
        if (outputDigest is not null && text is not null)
        {
            if (WireJson.Sha256Digest(text) != outputDigest)
                throw new ValidationError("view output digest mismatch");
            if (outputRef != $"output:{outputDigest[7..]}")
                throw new ValidationError("view output reference mismatch");
        }
        SourceRef = WireJson.ValidateRef(sourceRef, "source_ref");
        SourceDigest = WireJson.ValidateDigest(sourceDigest, "source_digest");
        RecoveryRef = recoveryRef is null ? null : WireJson.ValidateRef(recoveryRef, "recovery_ref");
        if (!ProtocolText.TryEngineStatus(status, out var parsedStatus))
            throw new ValidationError("invalid Engine observation status");
        Status = parsedStatus;
        Measurements = new ReadOnlyCollection<ContextMeasurement>(
            (measurements ?? Array.Empty<ContextMeasurement>()).ToList());
        if (Measurements.Count > WireJson.MaxMeasurements || Measurements.Any(item => item is null))
            throw new ValidationError("invalid measurements");
        Failure = failure;
        ReceiptLink = receiptLink;
        Invocation = FreezeRecord(invocation);
        Observation = FreezeRecord(observation);
        if (schemaVersion != Constants.SCHEMA_VERSION)
            throw new ValidationError("view schema_version must be 1");
        if (transportVersion != Constants.TRANSPORT_VERSION)
            throw new ValidationError("view transport_version must be integer 1");
        if (engineInterfaceVersion != Constants.ENGINE_INTERFACE_VERSION)
            throw new ValidationError("unsupported Engine Interface version");
        SchemaVersion = schemaVersion;
        TransportVersion = transportVersion;
        EngineInterfaceVersion = engineInterfaceVersion;
        Text = text;
        OutputRef = outputRef;
        OutputDigest = outputDigest;
    }

    public ContextSource Source { get; }
    public string? Text { get; }
    public string? OutputRef { get; }
    public string? OutputDigest { get; }
    public string SourceRef { get; }
    public string SourceDigest { get; }
    public string? RecoveryRef { get; }
    public EngineStatus Status { get; }
    public IReadOnlyList<ContextMeasurement> Measurements { get; }
    public ContextFailure? Failure { get; }
    public ContextReceiptLink? ReceiptLink { get; }
    public IReadOnlyDictionary<string, object?> Invocation { get; }
    public IReadOnlyDictionary<string, object?> Observation { get; }
    public int SchemaVersion { get; }
    public int TransportVersion { get; }
    public string EngineInterfaceVersion { get; }
    public Integrity IntegrityStatus => Verify() ? Integrity.Sealed : Integrity.Unsealed;
    public Integrity integrity_status => IntegrityStatus;
    public string? output_ref => OutputRef;
    public string? output_digest => OutputDigest;
    public string source_ref => SourceRef;
    public string source_digest => SourceDigest;
    public string? recovery_ref => RecoveryRef;
    public string? InvocationId => StringValue(Invocation, "invocation_id");
    public string? input_ref => StringValue(Invocation, "input_ref");
    public string? EngineVersion => NestedString(Invocation, "engine", "engine_version");
    public string? CapabilityVersion => NestedString(Invocation, "operation", "capability_version");

    public string RequireText() => Text ?? throw new EngineExecutionError("Engine view has no text", view: this);
    public string require_text() => RequireText();

    public IReadOnlyDictionary<string, string> RecoveryBinding()
    {
        if (RecoveryRef is null)
            throw new ValidationError("view has no recovery binding");
        return new ReadOnlyDictionary<string, string>(new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["recovery_ref"] = RecoveryRef,
            ["source_ref"] = SourceRef,
            ["source_digest"] = SourceDigest,
        });
    }

    public IReadOnlyDictionary<string, string> recovery_binding() => RecoveryBinding();

    public bool Verify()
    {
        try
        {
            if (Status is not (EngineStatus.Succeeded or EngineStatus.Degraded) ||
                RecoveryRef is null || OutputRef is null || OutputDigest is null || Text is null)
                return false;
            if (Invocation.GetValueOrDefault("source_refs") is not IEnumerable refs ||
                !refs.Cast<object?>().OfType<string>().Contains(SourceRef, StringComparer.Ordinal))
                return false;
            var invocationId = InvocationId;
            if (invocationId is null ||
                !Equals(Observation.GetValueOrDefault("invocation_id"), invocationId) ||
                !Equals(Observation.GetValueOrDefault("output_digest"), OutputDigest) ||
                !Equals(Observation.GetValueOrDefault("output_ref"), OutputRef) ||
                ReceiptLink is null || ReceiptLink.InvocationId != invocationId)
                return false;
            return true;
        }
        catch
        {
            return false;
        }
    }

    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = (long)SchemaVersion,
            ["transport_version"] = (long)TransportVersion,
            ["engine_interface_version"] = EngineInterfaceVersion,
            ["source"] = Source.ToDictionary(),
            ["text"] = Text,
            ["output_ref"] = OutputRef,
            ["output_digest"] = OutputDigest,
            ["source_ref"] = SourceRef,
            ["source_digest"] = SourceDigest,
            ["recovery_ref"] = RecoveryRef,
            ["status"] = ProtocolText.EngineStatusText(Status),
            ["measurements"] = Measurements.Select(item => item.ToDictionary()).ToList(),
            ["failure"] = Failure?.ToDictionary(),
            ["receipt_link"] = ReceiptLink?.ToDictionary(),
            ["invocation"] = Invocation,
            ["observation"] = Observation,
        });

    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    object IWireValue.ToWireValue() => ToDictionary();

    private static IReadOnlyDictionary<string, object?> FreezeRecord(
        IReadOnlyDictionary<string, object?>? value)
    {
        var result = new Dictionary<string, object?>(StringComparer.Ordinal);
        if (value is not null)
        {
            foreach (var pair in value)
                result[pair.Key] = WireJson.DeepFreeze(WireJson.Plain(pair.Value));
        }
        return new ReadOnlyDictionary<string, object?>(result);
    }

    private static string? StringValue(IReadOnlyDictionary<string, object?> value, string key) =>
        value.GetValueOrDefault(key) as string;

    private static string? NestedString(
        IReadOnlyDictionary<string, object?> value,
        string record,
        string key) => value.GetValueOrDefault(record) is IReadOnlyDictionary<string, object?> nested
        ? nested.GetValueOrDefault(key) as string
        : null;
}

public class ContextReceipt : IWireValue
{
    public ContextReceipt(
        string sessionId,
        string taskId,
        string? planId,
        ContextView? view,
        HostOutcome outcome,
        Integrity integrityStatus,
        IReadOnlyList<string>? degradations = null,
        IReadOnlyDictionary<string, object?>? usage = null,
        string? hostExceptionType = null,
        object? hostResult = null,
        Exception? hostException = null,
        int schemaVersion = Constants.SCHEMA_VERSION)
        : this(sessionId, taskId, planId, view, ProtocolText.HostOutcomeText(outcome),
            ProtocolText.IntegrityText(integrityStatus), degradations, usage,
            hostExceptionType, hostResult, hostException, schemaVersion)
    {
        if (!Enum.IsDefined(outcome))
            throw new ValidationError("invalid host outcome");
        if (!Enum.IsDefined(integrityStatus))
            throw new ValidationError("invalid integrity status");
    }

    public ContextReceipt(
        string sessionId,
        string taskId,
        string? planId,
        ContextView? view,
        string outcome,
        string integrityStatus,
        IReadOnlyList<string>? degradations = null,
        IReadOnlyDictionary<string, object?>? usage = null,
        string? hostExceptionType = null,
        object? hostResult = null,
        Exception? hostException = null,
        int schemaVersion = Constants.SCHEMA_VERSION)
    {
        SessionId = WireJson.Text(sessionId, "session_id", WireJson.MaxRefBytes);
        TaskId = WireJson.Text(taskId, "task_id", WireJson.MaxRefBytes);
        PlanId = planId is null ? null : WireJson.ValidatePlanRef(planId);
        View = view;
        if (!ProtocolText.TryHostOutcome(outcome, out var parsedOutcome))
            throw new ValidationError("invalid host outcome");
        if (!ProtocolText.TryIntegrity(integrityStatus, out var parsedIntegrity))
            throw new ValidationError("invalid integrity status");
        Outcome = parsedOutcome;
        IntegrityStatus = parsedIntegrity;
        if (hostExceptionType is not null &&
            (hostExceptionType.Length == 0 || hostExceptionType.Contains(':') ||
             hostExceptionType.Contains('\n') || WireJson.Utf8(hostExceptionType, "host_exception_type").Length > WireJson.MaxRefBytes))
            throw new ValidationError("host_exception_type must be a safe type name");
        if (hostException is not null && parsedOutcome != HostOutcome.Aborted)
            throw new ValidationError("host_exception requires an aborted outcome");
        if (hostException is not null && hostExceptionType is not null &&
            hostException.GetType().Name != hostExceptionType)
            throw new ValidationError("host_exception_type does not match host_exception");
        if (schemaVersion != Constants.SCHEMA_VERSION)
            throw new ValidationError("receipt schema_version must be 1");
        Degradations = new ReadOnlyCollection<string>((degradations ?? Array.Empty<string>()).ToList());
        if (Degradations.Any(item => string.IsNullOrEmpty(item)))
            throw new ValidationError("degradations must be non-empty strings");
        if (parsedIntegrity == Integrity.Sealed && (view is null || !view.Verify()))
            throw new ValidationError("sealed receipt requires verified Engine evidence");
        Usage = usage is null ? null : FreezeUsage(usage);
        HostExceptionType = hostExceptionType;
        HostResult = hostResult;
        HostException = hostException;
        SchemaVersion = schemaVersion;
    }

    public string SessionId { get; }
    public string TaskId { get; }
    public string? PlanId { get; }
    public ContextView? View { get; }
    public HostOutcome Outcome { get; }
    public Integrity IntegrityStatus { get; }
    public IReadOnlyList<string> Degradations { get; }
    public IReadOnlyDictionary<string, object?>? Usage { get; }
    public string? HostExceptionType { get; }
    public object? HostResult { get; }
    public Exception? HostException { get; }
    public int SchemaVersion { get; }
    public bool Sealed => IntegrityStatus == Integrity.Sealed;
    public string session_id => SessionId;
    public string task_id => TaskId;
    public string? plan_id => PlanId;
    public string outcome => ProtocolText.HostOutcomeText(Outcome);
    public string integrity_status => ProtocolText.IntegrityText(IntegrityStatus);
    public string? host_exception_type => HostExceptionType;
    public string? Status => View is null ? null : ProtocolText.EngineStatusText(View.Status);
    public ContextSource? Source => View?.Source;
    public IReadOnlyDictionary<string, object?>? Invocation => View?.Invocation;
    public IReadOnlyDictionary<string, object?>? Observation => View?.Observation;
    public ContextReceiptLink? ReceiptLink => View?.ReceiptLink;
    public string? RecoveryRef => View?.RecoveryRef;
    public string? OutputDigest => View?.OutputDigest;
    public Exception? Exception => HostException;

    public bool Verify() => Sealed && View is not null && View.Verify();

    public void RequireVerified()
    {
        if (!Verify())
            throw new ArtifactIntegrityError("receipt evidence is not sealed", View);
    }

    public void require_verified() => RequireVerified();

    public IReadOnlyDictionary<string, object?> ToDictionary() =>
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = (long)SchemaVersion,
            ["session_id"] = SessionId,
            ["task_id"] = TaskId,
            ["plan_id"] = PlanId,
            ["outcome"] = ProtocolText.HostOutcomeText(Outcome),
            ["integrity_status"] = ProtocolText.IntegrityText(IntegrityStatus),
            ["degradations"] = Degradations.ToList(),
            ["usage"] = Usage,
            ["host_exception_type"] = HostExceptionType,
            ["status"] = Status,
            ["source"] = Source?.ToDictionary(),
            ["invocation"] = Invocation,
            ["observation"] = Observation,
            ["receipt_link"] = ReceiptLink?.ToDictionary(),
            ["recovery_ref"] = RecoveryRef,
            ["output_digest"] = OutputDigest,
        });

    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
    object IWireValue.ToWireValue() => ToDictionary();

    private static IReadOnlyDictionary<string, object?> FreezeUsage(
        IReadOnlyDictionary<string, object?> value)
    {
        var plain = WireJson.Plain(value) as Dictionary<string, object?> ??
            throw new ValidationError("usage must be a mapping");
        _ = WireJson.CanonicalBytes(plain);
        return (IReadOnlyDictionary<string, object?>)WireJson.DeepFreeze(plain)!;
    }
}
