// Until 2.0 the SDK throws the deprecated Engine error subtypes so that both the
// Engine and the Instance names catch them (see Errors.cs).
#pragma warning disable CS0618
using System.Collections;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace Thinkery.LeanCtx;

/// <summary>Engine Interface v1 adapter.</summary>
public interface InstanceClient
{
    ContextView ContextView(ContextPlan plan) =>
        ContextViewAsync(plan).GetAwaiter().GetResult();

    Task<ContextView> ContextViewAsync(
        ContextPlan plan,
        CancellationToken cancellationToken = default) =>
        Task.FromResult(ContextView(plan));

    RecoveredSource Recover(
        string projectRoot,
        string path,
        string recoveryRef,
        string sourceRef,
        string sourceDigest) =>
        RecoverAsync(projectRoot, path, recoveryRef, sourceRef, sourceDigest)
            .GetAwaiter().GetResult();

    Task<RecoveredSource> RecoverAsync(
        string projectRoot,
        string path,
        string recoveryRef,
        string sourceRef,
        string sourceDigest,
        CancellationToken cancellationToken = default) =>
        Task.FromResult(Recover(projectRoot, path, recoveryRef, sourceRef, sourceDigest));
}

/// <summary>Deprecated name of <see cref="InstanceClient"/>; removed in 2.0.</summary>
[Obsolete("Use InstanceClient; removed in 2.0")]
public interface EngineClient : InstanceClient
{
}

/// <summary>
/// Environment configuration of the LeanCTX instance binary. The 1.3 names are read
/// first; the deprecated Engine names are honoured until 2.0 with a one-time warning.
/// </summary>
internal static class InstanceEnvironment
{
    private static int warned;

    internal static string? Read(string name, string deprecatedName)
    {
        var value = Environment.GetEnvironmentVariable(name);
        if (!string.IsNullOrEmpty(value))
            return value;
        value = Environment.GetEnvironmentVariable(deprecatedName);
        if (!string.IsNullOrEmpty(value) && Interlocked.Exchange(ref warned, 1) == 0)
            Console.Error.WriteLine(
                $"Thinkery.LeanCtx: {deprecatedName} is deprecated and removed in 2.0; use {name}.");
        return string.IsNullOrEmpty(value) ? null : value;
    }

    internal static string Binary(string? explicitBinary) =>
        explicitBinary ?? Read("LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN") ?? "lean-ctx";
}

internal sealed record ParsedInstanceResponse(
    InstanceViewValue View,
    IReadOnlyDictionary<string, object?>? Invocation,
    IReadOnlyDictionary<string, object?>? Observation,
    RecoveryValue Recovery);

internal sealed record InstanceViewValue(string Text, string? OutputRef, string? OutputDigest);
internal sealed record RecoveryValue(string RecoveryRef, string SourceRef, string SourceDigest);

/// <summary>Strict, shell-free Engine Interface v1 subprocess client.</summary>
public class SubprocessInstanceClient : InstanceClient
{
    private volatile bool lastSessionContained;

    /// <summary>
    /// How the process tree of the most recent completed LeanCTX instance run was
    /// contained; see <see cref="SessionContainment"/>. Rule: it is set
    /// whenever a run reached its end and its output was read, also when the
    /// LeanCTX instance exited with a failure; a run ended before that (deadline,
    /// cancellation, output bound) leaves it unchanged. With concurrent calls
    /// it is the state of whichever finished last.
    /// </summary>
    public SessionContainment LastSessionContainment =>
        lastSessionContained ? SessionContainment.InstanceStarter : SessionContainment.Unavailable;

    private static readonly IReadOnlySet<string> TopLevelKeys = SetOf(
        "schema_version", "transport_version", "engine_interface_version", "view",
        "invocation", "observation", "recovery");
    private static readonly IReadOnlySet<string> ViewKeys = SetOf("text", "output_ref", "output_digest");
    private static readonly IReadOnlySet<string> RecoveryKeys = SetOf("recovery_ref", "source_ref", "source_digest");
    private static readonly IReadOnlySet<string> InvocationKeys = SetOf(
        "schema_version", "invocation_id", "engine", "operation", "input_ref",
        "input_digest", "source_refs", "policy_admission");
    private static readonly IReadOnlySet<string> EngineKeys = SetOf("engine_id", "engine_version");
    private static readonly IReadOnlySet<string> OperationKeys = SetOf("capability_id", "capability_version");
    private static readonly IReadOnlySet<string> PolicyKeys = SetOf("policy_ref", "decision");
    private static readonly IReadOnlySet<string> ObservationKeys = SetOf(
        "schema_version", "invocation_id", "status", "output_ref", "output_digest",
        "source_lineage", "measurements", "failure", "receipt_link");
    private static readonly IReadOnlySet<string> ObservationRequiredKeys = SetOf(
        "schema_version", "invocation_id", "status", "source_lineage", "measurements");
    private static readonly IReadOnlySet<string> MeasurementKeys = SetOf(
        "name", "unit", "classification", "value");
    private static readonly IReadOnlySet<string> FailureKeys = SetOf(
        "code", "retryable_by_host", "recovery_ref");
    private static readonly IReadOnlySet<string> ReceiptLinkKeys = SetOf(
        "schema_version", "receipt_id", "receipt_ref", "receipt_digest", "invocation_id");

    /// <param name="instanceBinary">The LeanCTX instance executable.</param>
    /// <param name="timeout">Deadline per run in seconds.</param>
    /// <param name="engineBinary">Deprecated name of <paramref name="instanceBinary"/>; removed in 2.0.</param>
    public SubprocessInstanceClient(string? instanceBinary = null, double timeout = 30, string? engineBinary = null)
    {
        if (!double.IsFinite(timeout) || timeout < 0.1 || timeout > 120)
            throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
        InstanceBinary = InstanceEnvironment.Binary(instanceBinary ?? engineBinary);
        if (string.IsNullOrWhiteSpace(InstanceBinary) || InstanceBinary.Contains('\0'))
            throw new ConfigurationError("instance_binary must be a non-empty path");
        Timeout = timeout;
    }

    public string InstanceBinary { get; }

    /// <summary>Deprecated name of <see cref="InstanceBinary"/>; removed in 2.0.</summary>
    [Obsolete("Use InstanceBinary; removed in 2.0")]
    public string EngineBinary => InstanceBinary;
    public double Timeout { get; }

    internal static object ParseForTest(byte[] response) => ParseResponse(response);

    public ContextView ContextView(ContextPlan plan) =>
        ContextViewAsync(plan).GetAwaiter().GetResult();

    public async Task<ContextView> ContextViewAsync(
        ContextPlan plan,
        CancellationToken cancellationToken = default)
    {
        if (plan is null)
            throw new ValidationError("context_view requires ContextPlan");
        var source = plan.Source;
        var root = ValidateRoot(source.ProjectRoot);
        var relativePath = ValidateRelativePath(source.RelativePath);
        ValidateSourcePath(root, relativePath);
        var request = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["transport_version"] = 1L,
            ["engine_interface_version"] = Constants.ENGINE_INTERFACE_VERSION,
            ["path"] = relativePath,
            ["mode"] = plan.Mode,
        };
        var response = await InvokeAsync("context-view", root, request, cancellationToken).ConfigureAwait(false);
        if (response.Invocation is null || response.Observation is null)
            throw Protocol("context-view response omitted invocation/observation");
        var sourceRefs = StringList(response.Invocation, "source_refs");
        if (!sourceRefs.Contains(response.Recovery.SourceRef, StringComparer.Ordinal))
            throw Protocol("recovery source_ref is not admitted by invocation");
        if (source.SourceRef is not null && source.SourceRef != response.Recovery.SourceRef)
            throw Protocol("LeanCTX instance source_ref differs from requested binding");
        if (source.SourceDigest is not null && source.SourceDigest != response.Recovery.SourceDigest)
            throw Protocol("LeanCTX instance source_digest differs from requested binding");
        ContextView result;
        try
        {
            result = BuildView(source, response);
        }
        catch (ValidationError error)
        {
            throw Protocol(error.Message, error);
        }
        if (result.Status == InstanceStatus.Rejected)
        {
            var failure = result.Failure;
            if (failure?.Code == FailureCode.PolicyRejected)
                throw new PolicyAdmissionError("LeanCTX instance rejected request: policy_rejected", failure, result);
            if (failure?.Code == FailureCode.SourceUnavailable)
                throw new SourceUnavailableError("LeanCTX instance rejected request: source_unavailable", result);
            throw new EngineRejected($"LeanCTX instance rejected request: {failure?.code ?? "rejected"}", failure, result);
        }
        if (result.Status == InstanceStatus.Failed)
        {
            var failure = result.Failure;
            if (failure?.Code == FailureCode.UnsupportedOperation)
                throw new UnsupportedEngineError("LeanCTX instance execution failed: unsupported_operation");
            if (failure?.Code == FailureCode.SourceIntegrityMismatch)
                throw new ArtifactIntegrityError("LeanCTX instance execution failed: source_integrity_mismatch", result);
            if (failure?.Code == FailureCode.SourceUnavailable)
                throw new SourceUnavailableError("LeanCTX instance execution failed: source_unavailable", result);
            throw new EngineExecutionError(
                $"LeanCTX instance execution failed: {failure?.code ?? "failed"}", failure, result);
        }
        return result;
    }

    public RecoveredSource Recover(
        string projectRoot,
        string path,
        string recoveryRef,
        string sourceRef,
        string sourceDigest) =>
        RecoverAsync(projectRoot, path, recoveryRef, sourceRef, sourceDigest)
            .GetAwaiter().GetResult();

    public async Task<RecoveredSource> RecoverAsync(
        string projectRoot,
        string path,
        string recoveryRef,
        string sourceRef,
        string sourceDigest,
        CancellationToken cancellationToken = default)
    {
        var root = ValidateRoot(projectRoot);
        var relativePath = ValidateRelativePath(path);
        var checkedRecovery = CheckedRef(recoveryRef, "recovery_ref");
        var checkedSource = CheckedRef(sourceRef, "source_ref");
        var checkedDigest = CheckedDigest(sourceDigest, "source_digest");
        var request = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["transport_version"] = 1L,
            ["engine_interface_version"] = Constants.ENGINE_INTERFACE_VERSION,
            ["path"] = relativePath,
            ["recovery_ref"] = checkedRecovery,
            ["source_ref"] = checkedSource,
            ["source_digest"] = checkedDigest,
        };
        var response = await InvokeAsync("recover", root, request, cancellationToken).ConfigureAwait(false);
        if (response.Invocation is not null || response.Observation is not null)
            throw Protocol("recover response must have null invocation/observation");
        if (response.Recovery is not { RecoveryRef: var recovery } || recovery != checkedRecovery ||
            response.Recovery.SourceRef != checkedSource || response.Recovery.SourceDigest != checkedDigest)
            throw new ArtifactIntegrityError("recover response binding mismatch");
        if (response.View.OutputDigest != checkedDigest)
            throw new ArtifactIntegrityError("recover output digest does not match source digest");
        var expectedRef = $"output:{checkedDigest[7..]}";
        if (response.View.OutputRef is not null && response.View.OutputRef != expectedRef)
            throw new ArtifactIntegrityError("recover output reference does not match source digest");
        try
        {
            return new RecoveredSource(
                response.View.Text, checkedSource, checkedDigest, checkedRecovery);
        }
        catch (ValidationError error)
        {
            throw Protocol(error.Message, error);
        }
    }

    private async Task<ParsedInstanceResponse> InvokeAsync(
        string operation,
        string projectRoot,
        IReadOnlyDictionary<string, object?> request,
        CancellationToken cancellationToken)
    {
        var payload = WireJson.CanonicalBytes(request);
        if (payload.Length > WireJson.MaxRequestBytes)
            throw new EngineProtocolError("LeanCTX instance request exceeds the bound");
        var responseBytes = await RunPayloadAsync(operation, projectRoot, payload, cancellationToken)
            .ConfigureAwait(false);
        return ParseResponse(responseBytes);
    }

    /// <summary>Writes one request document to a private file under the root and runs the operation.</summary>
    internal async Task<byte[]> RunPayloadAsync(
        string operation,
        string projectRoot,
        byte[] payload,
        CancellationToken cancellationToken)
    {
        var directory = Path.Combine(projectRoot, $".leanctx-sdk-{Guid.NewGuid():N}");
        var requestPath = Path.Combine(directory, "request.json");
        try
        {
            Directory.CreateDirectory(directory);
            SetDirectoryMode(directory);
            await File.WriteAllBytesAsync(requestPath, payload, cancellationToken).ConfigureAwait(false);
            SetFileMode(requestPath);
            return await RunAsync(operation, projectRoot, requestPath, cancellationToken)
                .ConfigureAwait(false);
        }
        finally
        {
            TryDeleteDirectory(directory);
        }
    }

    internal static string ValidatedRoot(string projectRoot) => ValidateRoot(projectRoot);

    private async Task<byte[]> RunAsync(
        string operation,
        string projectRoot,
        string requestPath,
        CancellationToken cancellationToken)
    {
        var binary = ResolveBinary();
        using var process = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = binary,
                WorkingDirectory = projectRoot,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            },
            EnableRaisingEvents = true,
        };
        process.StartInfo.ArgumentList.Add("engine");
        process.StartInfo.ArgumentList.Add(operation);
        process.StartInfo.ArgumentList.Add("--project-root");
        process.StartInfo.ArgumentList.Add(projectRoot);
        process.StartInfo.ArgumentList.Add("--json-file");
        process.StartInfo.ArgumentList.Add(requestPath);
        process.StartInfo.Environment.Clear();
        process.StartInfo.Environment["LANG"] = "C";
        process.StartInfo.Environment["LC_ALL"] = "C";
        process.StartInfo.Environment["PYTHONHASHSEED"] = "0";
        process.StartInfo.Environment["TZ"] = "UTC";
        SessionContainmentProtocol.Request(process.StartInfo);
        try
        {
            if (!process.Start())
                throw new InvalidOperationException("Process.Start returned false");
        }
        catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            throw new EngineUnavailable("LeanCTX instance process could not be started", error);
        }

        using var timeoutSource = new CancellationTokenSource(TimeSpan.FromSeconds(Timeout));
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken, timeoutSource.Token);
        var stdoutTask = ReadBoundedAsync(process.StandardOutput.BaseStream,
            WireJson.MaxResponseBytes, linked.Token);
        var stderrTask = ReadBoundedAsync(process.StandardError.BaseStream,
            WireJson.MaxStdErrBytes, linked.Token);
        try
        {
            await process.WaitForExitAsync(linked.Token).ConfigureAwait(false);
            var stdout = await stdoutTask.ConfigureAwait(false);
            var stderr = await stderrTask.ConfigureAwait(false);
            // The session starter's acknowledgement precedes everything the
            // LeanCTX instance wrote. It is recorded — for a failed run too — and is not
            // output.
            var ack = SessionContainmentProtocol.AckPrefixLength(stdout);
            lastSessionContained = ack > 0;
            if (process.ExitCode != 0)
                throw MapNonZero(process.ExitCode, stderr);
            return ack > 0 ? stdout[ack..] : stdout;
        }
        catch (OperationCanceledException) when (timeoutSource.IsCancellationRequested &&
            !cancellationToken.IsCancellationRequested)
        {
            KillAndReap(process);
            throw new EngineTimeout("LeanCTX instance process exceeded its deadline");
        }
        catch (OperationCanceledException)
        {
            KillAndReap(process);
            throw;
        }
        catch (InstanceProtocolError)
        {
            KillAndReap(process);
            throw;
        }
        finally
        {
            if (!process.HasExited)
                KillAndReap(process);
        }
    }

    private static async Task<byte[]> ReadBoundedAsync(
        Stream stream,
        int maximum,
        CancellationToken cancellationToken)
    {
        await using (stream.ConfigureAwait(false))
        {
            using var output = new MemoryStream();
            var buffer = new byte[8192];
            while (true)
            {
                var count = await stream.ReadAsync(buffer.AsMemory(), cancellationToken)
                    .ConfigureAwait(false);
                if (count == 0)
                    break;
                if (output.Length + count > maximum)
                    throw new EngineProtocolError("LeanCTX instance process output exceeds its bound");
                output.Write(buffer, 0, count);
            }
            return output.ToArray();
        }
    }

    private static Exception MapNonZero(int exitCode, byte[] stderrBytes)
    {
        var stderr = Encoding.UTF8.GetString(stderrBytes).Trim();
        // LeanCTX instance diagnostics may contain paths or source material. Only retain
        // the bounded machine-readable code on an explicit LeanCTX instance error line.
        var code = stderr.Split('\n')
            .Where(line => line.StartsWith("engine:", StringComparison.Ordinal))
            .Select(line => line["engine:".Length..].Trim())
            .FirstOrDefault(value => value.Length is > 0 and <= 128 &&
                value.All(character => character is >= 'a' and <= 'z' or '_'));
        return code switch
        {
            "unsafe_root" or "source_outside_root" or "source_symlink" or "policy_rejected" =>
                new PolicyAdmissionError($"LeanCTX instance rejected request: {code}"),
            "source_unavailable" => new SourceUnavailableError("LeanCTX instance source is unavailable"),
            "unsupported_mode" => new UnsupportedEngineError("LeanCTX instance operation is unsupported"),
            _ => new EngineExecutionError(
                $"LeanCTX instance process failed: {code ?? $"nonzero_exit_{exitCode}"}"),
        };
    }

    private static void KillAndReap(Process process)
    {
        ProcessTree.KillAndReap(process, "LeanCTX instance");
    }

    private string ResolveBinary()
    {
        var candidate = InstanceBinary;
        if (!Path.IsPathRooted(candidate) && !candidate.Contains('/') && !candidate.Contains('\\'))
        {
            var paths = (Environment.GetEnvironmentVariable("PATH") ?? string.Empty)
                .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries);
            candidate = paths.Select(path => Path.GetFullPath(Path.Combine(path, candidate)))
                .FirstOrDefault(IsExecutableFile) ??
                throw new EngineUnavailable("configured LeanCTX instance binary is unavailable");
        }
        else
        {
            candidate = Path.GetFullPath(candidate);
            if (!IsExecutableFile(candidate))
                throw new EngineUnavailable("configured LeanCTX instance binary is unavailable");
        }
        try
        {
            return Path.GetFullPath(new FileInfo(candidate).FullName);
        }
        catch (Exception error)
        {
            throw new EngineUnavailable("configured LeanCTX instance binary is unavailable", error);
        }
    }

    private static bool IsExecutableFile(string path)
    {
        try
        {
            if (!File.Exists(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 ||
                new FileInfo(path).LinkTarget is not null)
                return false;
            if (OperatingSystem.IsWindows())
                return true;
            var mode = File.GetUnixFileMode(path);
            return mode.HasFlag(UnixFileMode.UserExecute) ||
                mode.HasFlag(UnixFileMode.GroupExecute) ||
                mode.HasFlag(UnixFileMode.OtherExecute);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    private static string ValidateRoot(string projectRoot)
    {
        if (string.IsNullOrWhiteSpace(projectRoot) || projectRoot.Contains('\0') ||
            WireJson.Utf8(projectRoot, "project_root").Length > WireJson.MaxPathBytes)
            throw new SourceUnavailableError("project_root is unavailable");
        try
        {
            var root = Path.GetFullPath(projectRoot);
            if (!Directory.Exists(root) || (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                throw new IOException();
            var trimmed = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return trimmed.Length == 0 ? Path.DirectorySeparatorChar.ToString() : trimmed;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        {
            throw new SourceUnavailableError("project_root is unavailable", innerException: error);
        }
    }

    private static void ValidateSourcePath(string root, string relativePath)
    {
        var candidate = ContextSource.FullPath(root, relativePath);
        if (!ContextSource.Contained(candidate, root))
            throw new PolicyAdmissionError("source path escapes project_root");
        if (!File.Exists(candidate))
            throw new SourceUnavailableError("LeanCTX instance source is unavailable");
        try
        {
            var current = root;
            foreach (var part in relativePath.Split('/', StringSplitOptions.RemoveEmptyEntries))
            {
                current = Path.Combine(current, part);
                var currentInfo = new FileInfo(current);
                if (currentInfo.LinkTarget is not null ||
                    (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new PolicyAdmissionError("source path is a symlink");
            }
            var info = new FileInfo(candidate);
            if ((info.Attributes & FileAttributes.ReparsePoint) != 0 || info.LinkTarget is not null)
                throw new PolicyAdmissionError("source path is a symlink");
            var final = info.ResolveLinkTarget(returnFinalTarget: true)?.FullName;
            if (final is not null && !ContextSource.Contained(Path.GetFullPath(final), root))
                throw new PolicyAdmissionError("source path resolves outside project_root");
        }
        catch (PolicyAdmissionError) { throw; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            throw new SourceUnavailableError("LeanCTX instance source is unavailable", innerException: error);
        }
    }

    private static string ValidateRelativePath(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Contains('\0') ||
            WireJson.Utf8(value, "path").Length > WireJson.MaxPathBytes ||
            Path.IsPathRooted(value) || value.Any(character => character < 0x20))
            throw Protocol("path must be a rooted relative path");
        var validationRoot = Path.GetFullPath(Path.Combine(
            Path.GetTempPath(), "leanctx-sdk-path-validation-root"));
        var candidate = Path.GetFullPath(value, validationRoot);
        if (!ContextSource.Contained(candidate, validationRoot))
            throw Protocol("path escapes project root");
        var relative = ContextSource.NormalizeRelative(
            Path.GetRelativePath(validationRoot, candidate));
        if (relative is "." or ".." || relative.StartsWith("../", StringComparison.Ordinal))
            throw Protocol("path escapes project root");
        return relative;
    }

    private static ContextView BuildView(ContextSource source, ParsedInstanceResponse parsed)
    {
        if (parsed.Invocation is null || parsed.Observation is null)
            throw Protocol("context-view response omitted invocation/observation");
        var observation = parsed.Observation;
        var statusText = observation.GetValueOrDefault("status") as string ?? "";
        if (!ProtocolText.TryInstanceStatus(statusText, out var status))
            throw Protocol("unknown observation status");
        return new ContextView(
            source,
            parsed.View.Text,
            parsed.View.OutputRef,
            parsed.View.OutputDigest,
            parsed.Recovery.SourceRef,
            parsed.Recovery.SourceDigest,
            parsed.Recovery.RecoveryRef,
            status,
            observation.GetValueOrDefault("measurements") is IEnumerable measurements
                ? measurements.Cast<ContextMeasurement>().ToList()
                : Array.Empty<ContextMeasurement>(),
            observation.GetValueOrDefault("failure") as ContextFailure,
            observation.GetValueOrDefault("receipt_link") as ContextReceiptLink,
            parsed.Invocation,
            observation);
    }

    private static ParsedInstanceResponse ParseResponse(byte[] bytes)
    {
        Dictionary<string, object?> item;
        try { item = WireJson.ParseObject(bytes, "LeanCTX instance response", WireJson.MaxResponseBytes); }
        catch (InstanceProtocolError) { throw; }
        WireJson.RequireExactKeys(item, TopLevelKeys, "LeanCTX instance response");
        if (WireJson.RequiredInteger(item, "schema_version") != Constants.SCHEMA_VERSION)
            throw new CompatibilityError("unsupported schema version");
        if (WireJson.RequiredInteger(item, "transport_version") != Constants.TRANSPORT_VERSION)
            throw new CompatibilityError("unsupported transport version");
        if (item.GetValueOrDefault("engine_interface_version") as string != Constants.ENGINE_INTERFACE_VERSION)
            throw new CompatibilityError("unsupported Engine Interface version");
        var view = ParseView(AsObject(item.GetValueOrDefault("view"), "view"));
        var recovery = ParseRecovery(AsObject(item.GetValueOrDefault("recovery"), "recovery"));
        var invocationValue = item.GetValueOrDefault("invocation");
        var observationValue = item.GetValueOrDefault("observation");
        if (invocationValue is null || observationValue is null)
        {
            if (invocationValue is not null || observationValue is not null)
                throw Protocol("invocation and observation must both be null or present");
            return new ParsedInstanceResponse(view, null, null, recovery);
        }
        var invocation = ParseInvocation(AsObject(invocationValue, "invocation"));
        var invocationId = WireJson.RequiredString(invocation, "invocation_id");
        var observation = ParseObservation(AsObject(observationValue, "observation"), invocationId);
        var lineage = StringList(observation, "source_lineage");
        var sourceRefs = StringList(invocation, "source_refs");
        if (!lineage.SequenceEqual(sourceRefs, StringComparer.Ordinal))
            throw Protocol("observation source lineage does not match invocation");
        if (!Equals(observation.GetValueOrDefault("output_ref"), view.OutputRef) ||
            !Equals(observation.GetValueOrDefault("output_digest"), view.OutputDigest))
            throw Protocol("view and observation output binding mismatch");
        return new ParsedInstanceResponse(view, invocation, observation, recovery);
    }

    private static InstanceViewValue ParseView(IReadOnlyDictionary<string, object?> item)
    {
        WireJson.RequireExactKeys(item, ViewKeys, "view");
        var text = WireJson.RequiredString(item, "text", WireJson.MaxTextBytes, allowEmpty: true);
        var outputRef = OptionalOutputRef(item.GetValueOrDefault("output_ref"), "view.output_ref");
        var outputDigest = OptionalDigest(item.GetValueOrDefault("output_digest"), "view.output_digest");
        ValidatePair(outputRef, outputDigest, "view");
        if (outputDigest is not null && WireJson.Sha256Digest(text) != outputDigest)
            throw Protocol("view output digest mismatch");
        return new InstanceViewValue(text, outputRef, outputDigest);
    }

    private static RecoveryValue ParseRecovery(IReadOnlyDictionary<string, object?> item)
    {
        WireJson.RequireExactKeys(item, RecoveryKeys, "recovery");
        var digest = CheckedDigest(item.GetValueOrDefault("source_digest") as string,
            "recovery.source_digest");
        return new RecoveryValue(
            CheckedRef(item.GetValueOrDefault("recovery_ref") as string, "recovery.recovery_ref"),
            CheckedRef(item.GetValueOrDefault("source_ref") as string, "recovery.source_ref"),
            digest);
    }

    private static IReadOnlyDictionary<string, object?> ParseInvocation(
        IReadOnlyDictionary<string, object?> item)
    {
        WireJson.RequireExactKeys(item, InvocationKeys, "invocation");
        var engine = AsObject(item.GetValueOrDefault("engine"), "invocation.engine");
        var operation = AsObject(item.GetValueOrDefault("operation"), "invocation.operation");
        var policy = AsObject(item.GetValueOrDefault("policy_admission"), "invocation.policy_admission");
        WireJson.RequireExactKeys(engine, EngineKeys, "invocation.engine");
        WireJson.RequireExactKeys(operation, OperationKeys, "invocation.operation");
        WireJson.RequireExactKeys(policy, PolicyKeys, "invocation.policy_admission");
        var engineId = WireJson.RequiredString(engine, "engine_id");
        var engineVersion = WireJson.RequiredString(engine, "engine_version");
        if (engineId != "lean-ctx-local" || !IsSemVer(engineVersion) ||
            !engineVersion.StartsWith("3.", StringComparison.Ordinal))
            throw new UnsupportedEngineError("unsupported LeanCTX instance identity");
        var capabilityId = WireJson.RequiredString(operation, "capability_id");
        var capabilityVersion = WireJson.RequiredString(operation, "capability_version");
        if (capabilityId != "capability://leanctx/context-optimization" || capabilityVersion != "1.0.0")
            throw new UnsupportedEngineError("unsupported LeanCTX instance capability");
        var decision = WireJson.RequiredString(policy, "decision");
        if (decision is not "admitted" and not "rejected")
            throw Protocol("unknown policy decision");
        var invocationId = WireJson.RequiredString(item, "invocation_id");
        var inputRef = WireJson.RequiredRef(item, "input_ref");
        var inputDigest = WireJson.RequiredDigest(item, "input_digest");
        var sourceRefs = StringList(item, "source_refs");
        if (sourceRefs.Count == 0 || sourceRefs.Count > WireJson.MaxRefs ||
            sourceRefs.Distinct(StringComparer.Ordinal).Count() != sourceRefs.Count ||
            !sourceRefs.Contains(inputRef, StringComparer.Ordinal))
            throw Protocol("invocation.source_refs is invalid");
        return new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = WireJson.RequiredInteger(item, "schema_version"),
            ["invocation_id"] = invocationId,
            ["engine"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["engine_id"] = engineId,
                ["engine_version"] = engineVersion,
            },
            ["operation"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["capability_id"] = capabilityId,
                ["capability_version"] = capabilityVersion,
            },
            ["input_ref"] = inputRef,
            ["input_digest"] = inputDigest,
            ["source_refs"] = sourceRefs,
            ["policy_admission"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["policy_ref"] = WireJson.RequiredRef(policy, "policy_ref"),
                ["decision"] = decision,
            },
        });
    }

    private static IReadOnlyDictionary<string, object?> ParseObservation(
        IReadOnlyDictionary<string, object?> item,
        string invocationId)
    {
        if (item.Keys.Any(key => !ObservationKeys.Contains(key)) ||
            ObservationRequiredKeys.Any(key => !item.ContainsKey(key)))
            throw Protocol("observation fields do not match the v1 contract");
        if (WireJson.RequiredString(item, "invocation_id") != invocationId)
            throw Protocol("observation invocation binding mismatch");
        if (WireJson.RequiredInteger(item, "schema_version") != Constants.SCHEMA_VERSION)
            throw Protocol("unsupported observation schema version");
        var statusText = WireJson.RequiredString(item, "status");
        if (!ProtocolText.TryInstanceStatus(statusText, out _))
            throw Protocol("unknown observation status");
        var outputRef = OptionalOutputRef(item.GetValueOrDefault("output_ref"), "observation.output_ref");
        var outputDigest = OptionalDigest(item.GetValueOrDefault("output_digest"), "observation.output_digest");
        ValidatePair(outputRef, outputDigest, "observation");
        var lineage = StringList(item, "source_lineage");
        if (lineage.Count == 0 || lineage.Count > WireJson.MaxRefs ||
            lineage.Distinct(StringComparer.Ordinal).Count() != lineage.Count)
            throw Protocol("observation.source_lineage is invalid");
        var measurementsValue = WireJson.RequiredArray(item, "measurements", WireJson.MaxMeasurements);
        var measurements = measurementsValue.Select(ParseMeasurement).ToList();
        var failure = item.GetValueOrDefault("failure") is null
            ? null : ParseFailure(AsObject(item.GetValueOrDefault("failure"), "failure"));
        var receiptLink = item.GetValueOrDefault("receipt_link") is null
            ? null : ParseReceiptLink(AsObject(item.GetValueOrDefault("receipt_link"), "receipt_link"), invocationId);
        if ((statusText is "succeeded" or "degraded") && failure is not null)
            throw Protocol("successful/degraded observation cannot contain failure");
        if ((statusText is "failed" or "rejected") && failure is null)
            throw Protocol("failed/rejected observation requires failure");
        if (statusText == "succeeded" && receiptLink is null)
            throw Protocol("succeeded observation requires receipt_link");
        return new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["invocation_id"] = invocationId,
            ["status"] = statusText,
            ["output_ref"] = outputRef,
            ["output_digest"] = outputDigest,
            ["source_lineage"] = lineage,
            ["measurements"] = measurements,
            ["failure"] = failure,
            ["receipt_link"] = receiptLink,
        });
    }

    private static ContextMeasurement ParseMeasurement(object? value)
    {
        var item = AsObject(value, "measurement");
        WireJson.RequireExactKeys(item, MeasurementKeys, "measurement");
        var classification = WireJson.RequiredString(item, "classification");
        var rawValue = item.GetValueOrDefault("value");
        long? measurementValue = rawValue is null ? null : rawValue as long?;
        if (rawValue is not null && rawValue is not long)
            throw Protocol("measurement.value must be an integer or null");
        try
        {
            return new ContextMeasurement(
                WireJson.RequiredString(item, "name"),
                WireJson.RequiredString(item, "unit"),
                classification,
                measurementValue);
        }
        catch (ValidationError error) { throw Protocol(error.Message, error); }
    }

    private static ContextFailure? ParseFailure(IReadOnlyDictionary<string, object?> item)
    {
        WireJson.RequireExactKeys(item, FailureKeys, "failure");
        var codeText = WireJson.RequiredString(item, "code");
        if (!ProtocolText.TryFailureCode(codeText, out var code))
            throw Protocol("unknown LeanCTX instance failure code");
        var recoveryRef = item.GetValueOrDefault("recovery_ref") is null
            ? null : WireJson.RequiredRef(item, "recovery_ref");
        try
        {
            return new ContextFailure(code, WireJson.RequiredBool(item, "retryable_by_host"), recoveryRef);
        }
        catch (ValidationError error) { throw Protocol(error.Message, error); }
    }

    private static ContextReceiptLink? ParseReceiptLink(
        IReadOnlyDictionary<string, object?> item,
        string invocationId)
    {
        WireJson.RequireExactKeys(item, ReceiptLinkKeys, "receipt_link");
        var digest = WireJson.RequiredDigest(item, "receipt_digest");
        var receiptRef = WireJson.RequiredRef(item, "receipt_ref");
        if (receiptRef != $"receipt:{digest}" ||
            WireJson.RequiredString(item, "invocation_id") != invocationId)
            throw Protocol("receipt_link binding mismatch");
        try
        {
            return new ContextReceiptLink(
                checked((int)WireJson.RequiredInteger(item, "schema_version")),
                WireJson.RequiredRef(item, "receipt_id"),
                receiptRef,
                digest,
                invocationId);
        }
        catch (ValidationError error) { throw Protocol(error.Message, error); }
    }

    private static IReadOnlyDictionary<string, object?> AsObject(object? value, string label)
    {
        if (value is IReadOnlyDictionary<string, object?> result)
            return result;
        throw Protocol($"{label} must be an object");
    }

    private static List<string> StringList(
        IReadOnlyDictionary<string, object?> value,
        string key)
    {
        if (value.GetValueOrDefault(key) is not IEnumerable entries)
            throw Protocol($"{key} must be an array");
        var result = new List<string>();
        foreach (var entry in entries)
        {
            try { result.Add(WireJson.ValidateRef(entry as string, key)); }
            catch (ValidationError error) { throw Protocol(error.Message, error); }
        }
        return result;
    }

    private static void ValidatePair(string? outputRef, string? outputDigest, string label)
    {
        if (outputRef is not null && outputDigest is null)
            throw Protocol($"{label} output reference requires a digest");
        if (outputRef is not null && outputDigest is not null &&
            outputRef != $"output:{outputDigest[7..]}")
            throw Protocol($"{label} output reference does not match digest");
    }

    private static string? OptionalDigest(object? value, string label) => value is null
        ? null : CheckedDigest(value as string, label);

    private static string? OptionalOutputRef(object? value, string label) => value is null
        ? null : CheckedOutputRef(value as string, label);

    private static string CheckedRef(string? value, string label)
    {
        try { return WireJson.ValidateRef(value, label); }
        catch (ValidationError error) { throw Protocol(error.Message, error); }
    }

    private static string CheckedDigest(string? value, string label)
    {
        try { return WireJson.ValidateDigest(value, label); }
        catch (ValidationError error) { throw Protocol(error.Message, error); }
    }

    private static string CheckedOutputRef(string? value, string label)
    {
        try { return WireJson.ValidateOutputRef(value, label); }
        catch (ValidationError error) { throw Protocol(error.Message, error); }
    }

    private static bool IsSemVer(string value) =>
        System.Text.RegularExpressions.Regex.IsMatch(value, "^[0-9]+\\.[0-9]+\\.[0-9]+$");

    private static EngineProtocolError Protocol(string message, Exception? cause = null) =>
        new(message, cause);

    private static IReadOnlySet<string> SetOf(params string[] values) =>
        new HashSet<string>(values, StringComparer.Ordinal);

    private static void TryDeleteDirectory(string directory)
    {
        try
        {
            if (Directory.Exists(directory))
                Directory.Delete(directory, recursive: true);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static void SetFileMode(string path)
    {
        if (!OperatingSystem.IsWindows())
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
    }

    private static void SetDirectoryMode(string path)
    {
        if (!OperatingSystem.IsWindows())
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite |
                UnixFileMode.UserExecute);
    }
}
