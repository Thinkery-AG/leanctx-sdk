using System.Collections;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace Thinkery.LeanCtx;

/// <summary>Engine Interface v1 adapter.</summary>
public interface EngineClient
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

internal sealed record ParsedEngineResponse(
    EngineViewValue View,
    IReadOnlyDictionary<string, object?>? Invocation,
    IReadOnlyDictionary<string, object?>? Observation,
    RecoveryValue Recovery);

internal sealed record EngineViewValue(string Text, string? OutputRef, string? OutputDigest);
internal sealed record RecoveryValue(string RecoveryRef, string SourceRef, string SourceDigest);

/// <summary>Strict, shell-free Engine Interface v1 subprocess client.</summary>
public sealed class SubprocessEngineClient : EngineClient
{
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

    public SubprocessEngineClient(string? engineBinary = null, double timeout = 30)
    {
        if (!double.IsFinite(timeout) || timeout < 0.1 || timeout > 120)
            throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
        EngineBinary = engineBinary ?? Environment.GetEnvironmentVariable("LEANCTX_ENGINE_BIN") ?? "lean-ctx";
        if (string.IsNullOrWhiteSpace(EngineBinary) || EngineBinary.Contains('\0'))
            throw new ConfigurationError("engine_binary must be a non-empty path");
        Timeout = timeout;
    }

    public string EngineBinary { get; }
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
            throw Protocol("Engine source_ref differs from requested binding");
        if (source.SourceDigest is not null && source.SourceDigest != response.Recovery.SourceDigest)
            throw Protocol("Engine source_digest differs from requested binding");
        ContextView result;
        try
        {
            result = BuildView(source, response);
        }
        catch (ValidationError error)
        {
            throw Protocol(error.Message, error);
        }
        if (result.Status == EngineStatus.Rejected)
        {
            var failure = result.Failure;
            if (failure?.Code == FailureCode.PolicyRejected)
                throw new PolicyAdmissionError("Engine rejected request: policy_rejected", failure, result);
            if (failure?.Code == FailureCode.SourceUnavailable)
                throw new SourceUnavailableError("Engine rejected request: source_unavailable", result);
            throw new EngineRejected($"Engine rejected request: {failure?.code ?? "rejected"}", failure, result);
        }
        if (result.Status == EngineStatus.Failed)
        {
            var failure = result.Failure;
            if (failure?.Code == FailureCode.UnsupportedOperation)
                throw new UnsupportedEngineError("Engine execution failed: unsupported_operation");
            if (failure?.Code == FailureCode.SourceIntegrityMismatch)
                throw new ArtifactIntegrityError("Engine execution failed: source_integrity_mismatch", result);
            if (failure?.Code == FailureCode.SourceUnavailable)
                throw new SourceUnavailableError("Engine execution failed: source_unavailable", result);
            throw new EngineExecutionError(
                $"Engine execution failed: {failure?.code ?? "failed"}", failure, result);
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

    private async Task<ParsedEngineResponse> InvokeAsync(
        string operation,
        string projectRoot,
        IReadOnlyDictionary<string, object?> request,
        CancellationToken cancellationToken)
    {
        var payload = WireJson.CanonicalBytes(request);
        if (payload.Length > WireJson.MaxRequestBytes)
            throw new EngineProtocolError("Engine request exceeds the bound");
        var directory = Path.Combine(projectRoot, $".leanctx-sdk-{Guid.NewGuid():N}");
        var requestPath = Path.Combine(directory, "request.json");
        try
        {
            Directory.CreateDirectory(directory);
            SetDirectoryMode(directory);
            await File.WriteAllBytesAsync(requestPath, payload, cancellationToken).ConfigureAwait(false);
            SetFileMode(requestPath);
            var responseBytes = await RunAsync(operation, projectRoot, requestPath, cancellationToken)
                .ConfigureAwait(false);
            return ParseResponse(responseBytes);
        }
        finally
        {
            TryDeleteDirectory(directory);
        }
    }

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
        try
        {
            if (!process.Start())
                throw new InvalidOperationException("Process.Start returned false");
        }
        catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            throw new EngineUnavailable("Engine process could not be started", error);
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
            if (process.ExitCode != 0)
                throw MapNonZero(process.ExitCode, stderr);
            return stdout;
        }
        catch (OperationCanceledException) when (timeoutSource.IsCancellationRequested &&
            !cancellationToken.IsCancellationRequested)
        {
            KillAndReap(process);
            throw new EngineTimeout("Engine process exceeded its deadline");
        }
        catch (OperationCanceledException)
        {
            KillAndReap(process);
            throw;
        }
        catch (EngineProtocolError)
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
                    throw new EngineProtocolError("Engine process output exceeds its bound");
                output.Write(buffer, 0, count);
            }
            return output.ToArray();
        }
    }

    private static Exception MapNonZero(int exitCode, byte[] stderrBytes)
    {
        var stderr = Encoding.UTF8.GetString(stderrBytes).Trim();
        var code = stderr.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)
            .Select(item => item.TrimEnd(':', ';', ','))
            .FirstOrDefault(item => item.Length <= 128);
        return code switch
        {
            "unsafe_root" or "source_outside_root" or "source_symlink" or "policy_rejected" =>
                new PolicyAdmissionError($"Engine rejected request: {code}"),
            "source_unavailable" => new SourceUnavailableError("Engine source is unavailable"),
            "unsupported_mode" => new UnsupportedEngineError("Engine operation is unsupported"),
            _ => new EngineExecutionError(
                $"Engine process failed: {code ?? $"nonzero_exit_{exitCode}"}"),
        };
    }

    private static void KillAndReap(Process process)
    {
        ProcessTree.KillAndReap(process, "Engine");
    }

    private string ResolveBinary()
    {
        var candidate = EngineBinary;
        if (!Path.IsPathRooted(candidate) && !candidate.Contains('/') && !candidate.Contains('\\'))
        {
            var paths = (Environment.GetEnvironmentVariable("PATH") ?? string.Empty)
                .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries);
            candidate = paths.Select(path => Path.GetFullPath(Path.Combine(path, candidate)))
                .FirstOrDefault(IsExecutableFile) ??
                throw new EngineUnavailable("configured Engine binary is unavailable");
        }
        else
        {
            candidate = Path.GetFullPath(candidate);
            if (!IsExecutableFile(candidate))
                throw new EngineUnavailable("configured Engine binary is unavailable");
        }
        try
        {
            return Path.GetFullPath(new FileInfo(candidate).FullName);
        }
        catch (Exception error)
        {
            throw new EngineUnavailable("configured Engine binary is unavailable", error);
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
            throw new SourceUnavailableError("Engine source is unavailable");
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
            throw new SourceUnavailableError("Engine source is unavailable", innerException: error);
        }
    }

    private static string ValidateRelativePath(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Contains('\0') ||
            WireJson.Utf8(value, "path").Length > WireJson.MaxPathBytes ||
            Path.IsPathRooted(value) || value.Any(character => character < 0x20))
            throw Protocol("path must be a rooted relative path");
        var normalized = ContextSource.NormalizeRelative(Path.GetFullPath(value, "/"));
        if (normalized is "." or ".." || normalized.StartsWith("../", StringComparison.Ordinal))
            throw Protocol("path escapes project root");
        return normalized;
    }

    private static ContextView BuildView(ContextSource source, ParsedEngineResponse parsed)
    {
        if (parsed.Invocation is null || parsed.Observation is null)
            throw Protocol("context-view response omitted invocation/observation");
        var observation = parsed.Observation;
        var statusText = observation.GetValueOrDefault("status") as string ?? "";
        if (!ProtocolText.TryEngineStatus(statusText, out var status))
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

    private static ParsedEngineResponse ParseResponse(byte[] bytes)
    {
        Dictionary<string, object?> item;
        try { item = WireJson.ParseObject(bytes, "Engine response", WireJson.MaxResponseBytes); }
        catch (EngineProtocolError) { throw; }
        WireJson.RequireExactKeys(item, TopLevelKeys, "Engine response");
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
            return new ParsedEngineResponse(view, null, null, recovery);
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
        return new ParsedEngineResponse(view, invocation, observation, recovery);
    }

    private static EngineViewValue ParseView(IReadOnlyDictionary<string, object?> item)
    {
        WireJson.RequireExactKeys(item, ViewKeys, "view");
        var text = WireJson.RequiredString(item, "text", WireJson.MaxTextBytes, allowEmpty: true);
        var outputRef = OptionalOutputRef(item.GetValueOrDefault("output_ref"), "view.output_ref");
        var outputDigest = OptionalDigest(item.GetValueOrDefault("output_digest"), "view.output_digest");
        ValidatePair(outputRef, outputDigest, "view");
        if (outputDigest is not null && WireJson.Sha256Digest(text) != outputDigest)
            throw Protocol("view output digest mismatch");
        return new EngineViewValue(text, outputRef, outputDigest);
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
        var engineId = WireJson.RequiredString(engine, "engine.engine_id");
        var engineVersion = WireJson.RequiredString(engine, "engine.engine_version");
        if (engineId != "lean-ctx-local" || !IsSemVer(engineVersion) ||
            !engineVersion.StartsWith("3.", StringComparison.Ordinal))
            throw new UnsupportedEngineError("unsupported Engine identity");
        var capabilityId = WireJson.RequiredString(operation, "operation.capability_id");
        var capabilityVersion = WireJson.RequiredString(operation, "operation.capability_version");
        if (capabilityId != "capability://leanctx/context-optimization" || capabilityVersion != "1.0.0")
            throw new UnsupportedEngineError("unsupported Engine capability");
        var decision = WireJson.RequiredString(policy, "policy_admission.decision");
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
                ["policy_ref"] = WireJson.RequiredRef(policy, "policy_admission.policy_ref"),
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
        if (!ProtocolText.TryEngineStatus(statusText, out _))
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
            throw Protocol("unknown Engine failure code");
        var recoveryRef = item.GetValueOrDefault("recovery_ref") is null
            ? null : WireJson.RequiredRef(item, "failure.recovery_ref");
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
        var digest = WireJson.RequiredDigest(item, "receipt_link.receipt_digest");
        var receiptRef = WireJson.RequiredRef(item, "receipt_link.receipt_ref");
        if (receiptRef != $"receipt:{digest}" ||
            WireJson.RequiredString(item, "receipt_link.invocation_id") != invocationId)
            throw Protocol("receipt_link binding mismatch");
        try
        {
            return new ContextReceiptLink(
                checked((int)WireJson.RequiredInteger(item, "receipt_link.schema_version")),
                WireJson.RequiredRef(item, "receipt_link.receipt_id"),
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
