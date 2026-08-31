using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Text;

namespace Thinkery.LeanCtx;

public sealed class AgentPermissions
{
    public AgentPermissions(bool write = false, bool execute = false)
    {
        Write = write;
        Execute = execute;
    }

    public bool Write { get; }
    public bool Execute { get; }
    public bool AllowWrite => Write;
    public bool AllowExecute => Execute;
    public bool allow_write => Write;
    public bool allow_exec => Execute;
}

public sealed class ExecutionPolicy
{
    private static readonly HashSet<string> ForbiddenEnvironment = new(StringComparer.OrdinalIgnoreCase)
    {
        "COMSPEC", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "HOME",
        "LD_LIBRARY_PATH", "LD_PRELOAD", "PATH", "PATHEXT", "PYTHONPATH",
        "RUSTC_WRAPPER", "SHELL", "NODE_OPTIONS", "NODE_PATH", "RUBYOPT",
        "PERL5OPT", "GCONV_PATH", "BASH_ENV", "ENV", "IFS", "CDPATH",
        "DOTNET_STARTUP_HOOKS", "DOTNET_ADDITIONAL_DEPS", "DOTNET_ROOT",
    };

    public ExecutionPolicy(
        double maxTimeout = 30,
        IEnumerable<string>? allowedExecutables = null,
        IEnumerable<string>? allowedEnv = null)
    {
        if (!double.IsFinite(maxTimeout) || maxTimeout < 0.1 || maxTimeout > 120)
            throw new ValidationError("max_timeout must be between 0.1 and 120 seconds");
        var executables = (allowedExecutables ?? Array.Empty<string>()).ToList();
        if (executables.Any(item => string.IsNullOrEmpty(item) ||
            !System.Text.RegularExpressions.Regex.IsMatch(item, "^[A-Za-z0-9._+\\-]+$")))
            throw new ValidationError("allowed_executables must contain executable basenames");
        var environment = (allowedEnv ?? Array.Empty<string>()).ToList();
        if (environment.Any(item => string.IsNullOrEmpty(item) ||
            !System.Text.RegularExpressions.Regex.IsMatch(item, "^[A-Za-z_][A-Za-z0-9_]*$") ||
            IsForbiddenEnvironmentName(item)))
            throw new ValidationError("allowed_env must contain environment variable names");
        MaxTimeout = maxTimeout;
        AllowedExecutables = new ReadOnlyCollection<string>(executables.Distinct(StringComparer.Ordinal)
            .OrderBy(item => item, StringComparer.Ordinal).ToList());
        AllowedEnv = new ReadOnlyCollection<string>(environment.Distinct(StringComparer.Ordinal)
            .OrderBy(item => item, StringComparer.Ordinal).ToList());
    }

    public double MaxTimeout { get; }
    public IReadOnlyList<string> AllowedExecutables { get; }
    public IReadOnlyList<string> AllowedEnv { get; }
    public double max_timeout => MaxTimeout;
    public IReadOnlyList<string> allowed_executables => AllowedExecutables;
    public IReadOnlyList<string> allowed_env => AllowedEnv;

    private static bool IsForbiddenEnvironmentName(string name) =>
        ForbiddenEnvironment.Contains(name) ||
        name.StartsWith("LD_", StringComparison.OrdinalIgnoreCase) ||
        name.StartsWith("DYLD_", StringComparison.OrdinalIgnoreCase) ||
        name.StartsWith("COMPlus_", StringComparison.OrdinalIgnoreCase) ||
        name.StartsWith("DOTNET_", StringComparison.OrdinalIgnoreCase);
}

public sealed class ToolResult
{
    internal ToolResult(
        string tool,
        string text,
        IReadOnlyList<IReadOnlyDictionary<string, object?>> contentBlocks,
        long originalTokens,
        long outputTokens,
        long savedTokens,
        string? mode,
        bool changed,
        IReadOnlyDictionary<string, object?>? shell)
    {
        Tool = tool;
        Text = text;
        ContentBlocks = contentBlocks;
        OriginalTokens = originalTokens;
        OutputTokens = outputTokens;
        SavedTokens = savedTokens;
        Mode = mode;
        Changed = changed;
        Shell = shell;
    }

    public string Tool { get; }
    public string Text { get; }
    public IReadOnlyList<IReadOnlyDictionary<string, object?>> ContentBlocks { get; }
    public long OriginalTokens { get; }
    public long OutputTokens { get; }
    public long SavedTokens { get; }
    public string? Mode { get; }
    public bool Changed { get; }
    public IReadOnlyDictionary<string, object?>? Shell { get; }
    public double SavedRatio => OriginalTokens == 0
        ? 0 : Math.Min(SavedTokens, OriginalTokens) / (double)OriginalTokens;
    public IReadOnlyList<IReadOnlyDictionary<string, object?>> content_blocks => ContentBlocks;
    public long original_tokens => OriginalTokens;
    public long output_tokens => OutputTokens;
    public long saved_tokens => SavedTokens;
    public double saved_ratio => SavedRatio;
}

public sealed class AgentMetrics
{
    internal AgentMetrics(long toolCalls = 0, long originalTokens = 0,
        long outputTokens = 0, long savedTokens = 0)
    {
        ToolCalls = toolCalls;
        OriginalTokens = originalTokens;
        OutputTokens = outputTokens;
        SavedTokens = savedTokens;
    }

    public long ToolCalls { get; }
    public long OriginalTokens { get; }
    public long OutputTokens { get; }
    public long SavedTokens { get; }
    public double SavedRatio => OriginalTokens == 0
        ? 0 : Math.Min(SavedTokens, OriginalTokens) / (double)OriginalTokens;
    public long tool_calls => ToolCalls;
    public long original_tokens => OriginalTokens;
    public long output_tokens => OutputTokens;
    public long saved_tokens => SavedTokens;
    public double saved_ratio => SavedRatio;
}

/// <summary>Persistent Agent Tools 1.1 JSONL client.</summary>
public sealed class AgentContext : IAsyncDisposable, IDisposable
{
    private const string EngineInterfaceVersion = Constants.SUPPORTED_AGENT_TOOLS_ENGINE_VERSION;
    private static readonly HashSet<string> ReadTools = SetOf(
        "ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree");
    private static readonly HashSet<string> WriteTools = SetOf("ctx_edit", "ctx_fill", "ctx_patch");
    private static readonly HashSet<string> ExecuteTools = SetOf("ctx_shell");
    private readonly object sync = new();
    private readonly SemaphoreSlim writeLock = new(1, 1);
    private readonly ConcurrentDictionary<string, TaskCompletionSource<Dictionary<string, object?>>> pending = new();
    private readonly Process process;
    private readonly Task stdoutTask;
    private readonly Task readyTask;
    private readonly Task stderrTask;
    private readonly List<byte> stderr = new();
    private readonly string policyDirectory;
    private readonly string policyPath;
    private long nextId;
    private IReadOnlyList<string> capabilitiesValue = Array.Empty<string>();
    private AgentMetrics metricsValue = new();
    private int terminal;
    private int helloAccepted;
    private EngineError? cleanupError;

    public AgentContext(
        string projectRoot,
        string? task = null,
        AgentPermissions? permissions = null,
        ExecutionPolicy? executionPolicy = null,
        string? engineBinary = null,
        double timeout = 30)
    {
        ProjectRoot = ValidateProjectRoot(projectRoot);
        Task = task is null ? string.Empty : WireJson.Text(task, "task", WireJson.MaxTaskBytes, controls: false);
        Permissions = permissions ?? new AgentPermissions();
        ExecutionPolicy = executionPolicy ?? new ExecutionPolicy();
        if (Permissions.Execute && ExecutionPolicy.AllowedExecutables.Count == 0)
            throw new ConfigurationError("execute permission requires at least one allowed executable");
        if (!double.IsFinite(timeout) || timeout < 0.1 || timeout > 120)
            throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
        EngineBinary = engineBinary ?? Environment.GetEnvironmentVariable("LEANCTX_ENGINE_BIN") ?? "lean-ctx";
        if (string.IsNullOrWhiteSpace(EngineBinary) || EngineBinary.Contains('\0'))
            throw new ConfigurationError("engine_binary must be a non-empty path");
        Timeout = timeout;
        policyDirectory = Path.Combine(ProjectRoot, $".leanctx-agent-{Guid.NewGuid():N}");
        policyPath = Path.Combine(policyDirectory, "policy.json");
        try
        {
            Directory.CreateDirectory(policyDirectory);
            SetDirectoryMode(policyDirectory);
            var policy = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["allow_exec"] = Permissions.Execute,
                ["allow_write"] = Permissions.Write,
                ["allowed_env"] = ExecutionPolicy.AllowedEnv.ToList(),
                ["allowed_executables"] = ExecutionPolicy.AllowedExecutables.ToList(),
                ["max_timeout_ms"] = checked((long)Math.Truncate(ExecutionPolicy.MaxTimeout * 1000)),
                ["schema_version"] = 1L,
            };
            File.WriteAllBytes(policyPath, WireJson.CanonicalBytes(policy));
            SetFileMode(policyPath);
            process = StartProcess(ResolveBinary());
            stderrTask = ReadStdErrAsync(process.StandardError.BaseStream);
            stdoutTask = ReadLoopAsync();
            readyTask = StartAsync();
        }
        catch
        {
            RemovePolicy();
            throw;
        }
    }

    public string ProjectRoot { get; }
    public string Task { get; }
    public AgentPermissions Permissions { get; }
    public ExecutionPolicy ExecutionPolicy { get; }
    public string EngineBinary { get; }
    public double Timeout { get; }
    public IReadOnlyList<string> Capabilities => capabilitiesValue;
    public AgentMetrics Metrics => metricsValue;
    public Task ReadyAsync() => readyTask;

    public static async Task<AgentContext> OpenAsync(
        string projectRoot,
        string? task = null,
        AgentPermissions? permissions = null,
        ExecutionPolicy? executionPolicy = null,
        string? engineBinary = null,
        double timeout = 30,
        CancellationToken cancellationToken = default)
    {
        var context = new AgentContext(projectRoot, task, permissions, executionPolicy,
            engineBinary, timeout);
        try
        {
            await context.ReadyAsync().WaitAsync(cancellationToken).ConfigureAwait(false);
            return context;
        }
        catch
        {
            await context.TerminateAsync(new EngineCrashed("AgentContext startup failed"))
                .ConfigureAwait(false);
            throw;
        }
    }

    public static AgentContext Open(
        string projectRoot,
        string? task = null,
        AgentPermissions? permissions = null,
        ExecutionPolicy? executionPolicy = null,
        string? engineBinary = null,
        double timeout = 30) =>
        OpenAsync(projectRoot, task, permissions, executionPolicy, engineBinary, timeout)
            .GetAwaiter().GetResult();

    public async Task<ToolResult> CallAsync(
        string tool,
        IReadOnlyDictionary<string, object?>? arguments = null,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrEmpty(tool))
            throw new ValidationError("tool must be a non-empty string");
        if (ExecuteTools.Contains(tool))
            throw new AgentPermissionError("execution tools must use RunAsync");
        if (WriteTools.Contains(tool) && !Permissions.Write)
            throw new AgentPermissionError("write permission is disabled");
        return await CallToolAsync(tool, arguments ?? EmptyArguments, Timeout, cancellationToken)
            .ConfigureAwait(false);
    }

    public ToolResult Call(
        string tool,
        IReadOnlyDictionary<string, object?>? arguments = null) =>
        CallAsync(tool, arguments).GetAwaiter().GetResult();

    public Task<ToolResult> ReadAsync(
        string path,
        ReadMode mode = ReadMode.Auto,
        bool fresh = false,
        CancellationToken cancellationToken = default) =>
        CallToolAsync("ctx_read", Args(
            ("path", path), ("mode", ModeText(mode)), ("fresh", fresh)), Timeout, cancellationToken);

    public ToolResult Read(string path, ReadMode mode = ReadMode.Auto, bool fresh = false) =>
        ReadAsync(path, mode, fresh).GetAwaiter().GetResult();

    public Task<ToolResult> SearchAsync(
        string pattern,
        string path = ".",
        int maxResults = 50,
        string? include = null,
        CancellationToken cancellationToken = default)
    {
        var args = Args(("path", path), ("pattern", pattern), ("max_results", maxResults));
        if (include is not null) args["include"] = include;
        return CallToolAsync("ctx_search", args, Timeout, cancellationToken);
    }

    public ToolResult Search(string pattern, string path = ".", int maxResults = 50,
        string? include = null) =>
        SearchAsync(pattern, path, maxResults, include).GetAwaiter().GetResult();

    public Task<ToolResult> GlobAsync(
        string pattern,
        string path = ".",
        int maxResults = 200,
        CancellationToken cancellationToken = default) =>
        CallToolAsync("ctx_glob", Args(("path", path), ("pattern", pattern),
            ("max_results", maxResults)), Timeout, cancellationToken);

    public ToolResult Glob(string pattern, string path = ".", int maxResults = 200) =>
        GlobAsync(pattern, path, maxResults).GetAwaiter().GetResult();

    public Task<ToolResult> TreeAsync(
        string path = ".",
        int depth = 3,
        bool showHidden = false,
        CancellationToken cancellationToken = default) =>
        CallToolAsync("ctx_tree", Args(("path", path), ("depth", depth),
            ("show_hidden", showHidden)), Timeout, cancellationToken);

    public ToolResult Tree(string path = ".", int depth = 3, bool showHidden = false) =>
        TreeAsync(path, depth, showHidden).GetAwaiter().GetResult();

    public Task<ToolResult> ComposeAsync(
        string? task = null,
        string path = ".",
        CancellationToken cancellationToken = default) =>
        CallToolAsync("ctx_compose", Args(("path", path), ("task", task ?? Task)),
            Timeout, cancellationToken);

    public ToolResult Compose(string? task = null, string path = ".") =>
        ComposeAsync(task, path).GetAwaiter().GetResult();

    public Task<ToolResult> SymbolAsync(string name, CancellationToken cancellationToken = default) =>
        CallToolAsync("ctx_symbol", Args(("name", name)), Timeout, cancellationToken);

    public ToolResult Symbol(string name) => SymbolAsync(name).GetAwaiter().GetResult();

    public Task<ToolResult> PatchAsync(
        IReadOnlyDictionary<string, object?> arguments,
        CancellationToken cancellationToken = default)
    {
        if (!Permissions.Write)
            return System.Threading.Tasks.Task.FromException<ToolResult>(new AgentPermissionError("write permission is disabled"));
        return CallToolAsync("ctx_patch", arguments, Timeout, cancellationToken);
    }

    public ToolResult Patch(IReadOnlyDictionary<string, object?> arguments) =>
        PatchAsync(arguments).GetAwaiter().GetResult();

    public Task<ToolResult> CreateFileAsync(
        string path,
        string text,
        CancellationToken cancellationToken = default) =>
        PatchAsync(Args(("path", path), ("op", "create"), ("new_text", text)), cancellationToken);

    public ToolResult CreateFile(string path, string text) =>
        CreateFileAsync(path, text).GetAwaiter().GetResult();

    public Task<ToolResult> ReplaceUniqueAsync(
        string path,
        string oldText,
        string newText,
        CancellationToken cancellationToken = default) =>
        PatchAsync(Args(("path", path), ("op", "replace_unique"),
            ("old_text", oldText), ("new_text", newText)), cancellationToken);

    public ToolResult ReplaceUnique(string path, string oldText, string newText) =>
        ReplaceUniqueAsync(path, oldText, newText).GetAwaiter().GetResult();

    public Task<ToolResult> RunAsync(
        IReadOnlyList<string> argv,
        string cwd = ".",
        IReadOnlyDictionary<string, string>? env = null,
        double? timeout = null,
        CancellationToken cancellationToken = default)
    {
        if (!Permissions.Execute)
            return System.Threading.Tasks.Task.FromException<ToolResult>(new AgentPermissionError("execute permission is disabled"));
        if (argv is null || argv.Count == 0 || argv.Any(item => string.IsNullOrEmpty(item)))
            return System.Threading.Tasks.Task.FromException<ToolResult>(new ValidationError("argv must be a non-empty sequence of strings"));
        var executable = argv[0];
        if (executable.Contains('/') || executable.Contains('\\') ||
            !ExecutionPolicy.AllowedExecutables.Contains(executable, StringComparer.Ordinal))
            return System.Threading.Tasks.Task.FromException<ToolResult>(new AgentPermissionError("executable is not allowed: " + executable));
        var selectedTimeout = timeout ?? ExecutionPolicy.MaxTimeout;
        if (!double.IsFinite(selectedTimeout) || selectedTimeout < 0.1 ||
            selectedTimeout > ExecutionPolicy.MaxTimeout)
            return System.Threading.Tasks.Task.FromException<ToolResult>(new ValidationError("timeout exceeds ExecutionPolicy"));
        string absoluteCwd;
        string relativeCwd;
        try
        {
            absoluteCwd = ContextSource.FullPath(ProjectRoot, cwd);
            if (!IsSafeDirectory(ProjectRoot, absoluteCwd))
                return System.Threading.Tasks.Task.FromException<ToolResult>(new AgentPermissionError("cwd escapes project root"));
            relativeCwd = ContextSource.NormalizeRelative(Path.GetRelativePath(ProjectRoot, absoluteCwd));
        }
        catch (ValidationError error) { return System.Threading.Tasks.Task.FromException<ToolResult>(error); }
        var values = env ?? EmptyEnvironment;
        foreach (var pair in values)
        {
            if (!ExecutionPolicy.AllowedEnv.Contains(pair.Key, StringComparer.Ordinal))
                return System.Threading.Tasks.Task.FromException<ToolResult>(new AgentPermissionError(
                    "environment variable is not allowed: " + pair.Key));
            if (pair.Value is null)
                return System.Threading.Tasks.Task.FromException<ToolResult>(new ValidationError("env must be a string mapping"));
        }
        return CallToolAsync("ctx_shell", Args(
            ("argv", argv.ToList()),
            ("cwd", relativeCwd),
            ("env", values.ToDictionary(pair => pair.Key, pair => (object?)pair.Value,
                StringComparer.Ordinal)),
            ("timeout_ms", checked((long)Math.Truncate(selectedTimeout * 1000)))),
            Math.Max(Timeout, selectedTimeout + 2), cancellationToken);
    }

    public ToolResult Run(
        IReadOnlyList<string> argv,
        string cwd = ".",
        IReadOnlyDictionary<string, string>? env = null,
        double? timeout = null) =>
        RunAsync(argv, cwd, env, timeout).GetAwaiter().GetResult();

    public async Task CloseAsync()
    {
        if (Volatile.Read(ref terminal) != 0)
        {
            await ReapAsync().ConfigureAwait(false);
            await stdoutTask.ConfigureAwait(false);
            if (cleanupError is not null)
                throw cleanupError;
            return;
        }
        try
        {
            await ExchangeRawAsync(Args(("op", "close")), bypassReady: false,
                Timeout, CancellationToken.None).ConfigureAwait(false);
        }
        catch (EngineError) { }
        await TerminateAsync(null).ConfigureAwait(false);
        await stdoutTask.ConfigureAwait(false);
        if (cleanupError is not null)
            throw cleanupError;
    }

    public void Close() => CloseAsync().GetAwaiter().GetResult();

    public async Task CancelAsync()
    {
        await TerminateAsync(new EngineCrashed("AgentContext cancelled")).ConfigureAwait(false);
        await stdoutTask.ConfigureAwait(false);
        if (cleanupError is not null)
            throw cleanupError;
    }
    public void Cancel() => CancelAsync().GetAwaiter().GetResult();

    public async Task<AgentContext> ReconnectAsync()
    {
        await CloseAsync().ConfigureAwait(false);
        return await OpenAsync(ProjectRoot, Task, Permissions, ExecutionPolicy,
            EngineBinary, Timeout).ConfigureAwait(false);
    }

    public AgentContext Reconnect() => ReconnectAsync().GetAwaiter().GetResult();

    public async ValueTask DisposeAsync() => await CloseAsync().ConfigureAwait(false);
    public void Dispose() => Close();

    private async Task StartAsync()
    {
        try
        {
            var hello = await ExchangeRawAsync(Args(
                ("op", "hello"),
                ("schema_version", 1L),
                ("transport_version", 1L),
                ("agent_tools_interface_version", Constants.AGENT_TOOLS_INTERFACE_VERSION),
                ("sdk_version", Constants.__version__)), true, Timeout, CancellationToken.None)
                .ConfigureAwait(false);
            AcceptHello(hello);
            Volatile.Write(ref helloAccepted, 1);
            RemovePolicy();
        }
        catch (Exception error)
        {
            var typed = error as EngineError ?? new EngineProtocolError(
                "Agent Tools startup failed", error);
            await TerminateAsync(typed).ConfigureAwait(false);
            throw typed;
        }
    }

    private async Task ReadLoopAsync()
    {
        try
        {
            var reader = new BoundedLineReader(process.StandardOutput.BaseStream);
            while (Volatile.Read(ref terminal) == 0)
            {
                var line = await reader.ReadLineAsync(WireJson.MaxResponseBytes).ConfigureAwait(false);
                if (line is null)
                    break;
                Dictionary<string, object?> response;
                try
                {
                    response = WireJson.ParseObject(line, "Agent Tools response",
                        WireJson.MaxResponseBytes);
                }
                catch (EngineProtocolError) { throw; }
                Dispatch(response);
            }
            if (Volatile.Read(ref terminal) == 0)
            {
                var message = await CrashMessageAsync().ConfigureAwait(false);
                await TerminateAsync(new EngineCrashed(message)).ConfigureAwait(false);
            }
        }
        catch (EngineProtocolError error)
        {
            await TerminateFromReaderAsync(error).ConfigureAwait(false);
        }
        catch (ValidationError error)
        {
            await TerminateFromReaderAsync(new EngineProtocolError(
                "Agent Tools response is invalid", error)).ConfigureAwait(false);
        }
        catch (Exception error) when (error is IOException or DecoderFallbackException)
        {
            await TerminateFromReaderAsync(new EngineCrashed("Agent Tools Engine exited", error))
                .ConfigureAwait(false);
        }
    }

    private async Task TerminateFromReaderAsync(EngineError error)
    {
        try
        {
            await TerminateAsync(error).ConfigureAwait(false);
        }
        catch (EngineError cleanup)
        {
            cleanupError = cleanup;
            foreach (var pair in pending)
            {
                if (pending.TryRemove(pair.Key, out var waiter))
                    waiter.TrySetException(cleanup);
            }
        }
    }

    private void Dispatch(IReadOnlyDictionary<string, object?> response)
    {
        if (response.Count != 3 || response.GetValueOrDefault("id") is not string id ||
            response.GetValueOrDefault("ok") is not bool ok)
            throw new EngineProtocolError("Agent Tools response envelope is invalid");
        if (!pending.TryRemove(id, out var waiter))
            throw new EngineProtocolError("Agent Tools response id is unexpected");
        if (ok)
        {
            if (response.GetValueOrDefault("result") is not IReadOnlyDictionary<string, object?> result)
            {
                var error = new EngineProtocolError("Agent Tools response omitted result");
                waiter.TrySetException(error);
                throw error;
            }
            waiter.TrySetResult(new Dictionary<string, object?>(result, StringComparer.Ordinal));
        }
        else
        {
            if (response.GetValueOrDefault("error") is not IReadOnlyDictionary<string, object?> error ||
                error.Count != 2 || error.GetValueOrDefault("code") is not string code ||
                error.GetValueOrDefault("message") is not string message)
            {
                var violation = new EngineProtocolError("Agent Tools error envelope is invalid");
                waiter.TrySetException(violation);
                throw violation;
            }
            waiter.TrySetException(ErrorFromWire(code, message));
        }
    }

    private sealed class BoundedLineReader
    {
        private readonly Stream stream;
        private readonly byte[] buffer = new byte[8192];
        private int offset;
        private int count;

        internal BoundedLineReader(Stream stream) => this.stream = stream;

        internal async Task<byte[]?> ReadLineAsync(int maximum)
        {
            using var output = new MemoryStream();
            while (true)
            {
                if (offset == count)
                {
                    count = await stream.ReadAsync(buffer.AsMemory()).ConfigureAwait(false);
                    offset = 0;
                    if (count == 0)
                        return output.Length == 0 ? null : output.ToArray();
                }
                var newline = Array.IndexOf(buffer, (byte)'\n', offset, count - offset);
                var length = newline < 0 ? count - offset : newline - offset;
                if (output.Length + length > maximum)
                    throw new EngineProtocolError("Agent Tools response exceeds its bound");
                output.Write(buffer, offset, length);
                offset += length;
                if (newline < 0)
                    continue;
                offset++;
                var line = output.ToArray();
                if (line.Length > 0 && line[^1] == (byte)'\r')
                    Array.Resize(ref line, line.Length - 1);
                return line;
            }
        }
    }

    private async Task<ToolResult> CallToolAsync(
        string tool,
        IReadOnlyDictionary<string, object?> arguments,
        double responseTimeout,
        CancellationToken cancellationToken)
    {
        if (!capabilitiesValue.Contains(tool, StringComparer.Ordinal))
            throw new UnsupportedCapabilityError($"Engine did not negotiate capability: {tool}");
        var canonical = WireJson.CanonicalBytes(arguments);
        if (canonical.Length > WireJson.MaxAgentRequestBytes)
            throw new ValidationError("arguments exceed the request bound");
        var result = await ExchangeRawAsync(Args(("op", "call"), ("tool", tool),
            ("arguments", arguments)), false, responseTimeout, cancellationToken).ConfigureAwait(false);
        ToolResult parsed;
        try { parsed = ParseToolResult(tool, result); }
        catch (EngineProtocolError error)
        {
            await TerminateAsync(error).ConfigureAwait(false);
            throw;
        }
        var previous = metricsValue;
        metricsValue = new AgentMetrics(previous.ToolCalls + 1,
            previous.OriginalTokens + parsed.OriginalTokens,
            previous.OutputTokens + parsed.OutputTokens,
            previous.SavedTokens + parsed.SavedTokens);
        return parsed;
    }

    private async Task<Dictionary<string, object?>> ExchangeRawAsync(
        IReadOnlyDictionary<string, object?> request,
        bool bypassReady,
        double responseTimeout,
        CancellationToken cancellationToken)
    {
        if (!bypassReady)
            await readyTask.ConfigureAwait(false);
        if (Volatile.Read(ref terminal) != 0 || process.HasExited)
            throw new EngineCrashed(CrashMessage());
        cancellationToken.ThrowIfCancellationRequested();
        var id = Interlocked.Increment(ref nextId).ToString(System.Globalization.CultureInfo.InvariantCulture);
        var envelope = new Dictionary<string, object?>(request, StringComparer.Ordinal)
        {
            ["id"] = id,
        };
        var bytes = WireJson.Utf8(WireJson.CanonicalJson(envelope) + "\n", "Agent Tools request");
        if (bytes.Length > WireJson.MaxAgentRequestBytes)
            throw new EngineProtocolError("Agent Tools request exceeds its bound");
        var waiter = new TaskCompletionSource<Dictionary<string, object?>>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        if (!pending.TryAdd(id, waiter))
            throw new EngineProtocolError("Agent Tools request id collision");
        using var timeoutSource = new CancellationTokenSource(
            TimeSpan.FromSeconds(responseTimeout));
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken, timeoutSource.Token);
        try
        {
            await writeLock.WaitAsync(linked.Token).ConfigureAwait(false);
            try
            {
                await process.StandardInput.BaseStream.WriteAsync(bytes, linked.Token)
                    .ConfigureAwait(false);
                await process.StandardInput.BaseStream.FlushAsync(linked.Token)
                    .ConfigureAwait(false);
            }
            finally { writeLock.Release(); }
            return await waiter.Task.WaitAsync(linked.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            pending.TryRemove(id, out _);
            if (timeoutSource.IsCancellationRequested && !cancellationToken.IsCancellationRequested)
            {
                var timeout = new EngineTimeout("Agent Tools response exceeded its deadline");
                await TerminateAsync(timeout).ConfigureAwait(false);
                throw timeout;
            }
            var cancellation = new OperationCanceledException(cancellationToken);
            await TerminateAsync(new EngineCrashed("Agent Tools request cancelled"))
                .ConfigureAwait(false);
            throw cancellation;
        }
        catch
        {
            pending.TryRemove(id, out _);
            throw;
        }
    }

    private void AcceptHello(IReadOnlyDictionary<string, object?> value)
    {
        var expected = SetOf("agent_tools_interface_version", "allow_exec", "allow_write",
            "capabilities", "engine_version", "schema_version", "transport_version");
        if (value.Count != expected.Count || value.Keys.Any(key => !expected.Contains(key)) ||
            value.GetValueOrDefault("agent_tools_interface_version") as string !=
                Constants.AGENT_TOOLS_INTERFACE_VERSION ||
            value.GetValueOrDefault("engine_version") as string != EngineInterfaceVersion ||
            value.GetValueOrDefault("schema_version") is not long schema || schema != 1 ||
            value.GetValueOrDefault("transport_version") is not long transport || transport != 1 ||
            value.GetValueOrDefault("allow_write") is not bool allowWrite ||
            value.GetValueOrDefault("allow_exec") is not bool allowExec ||
            allowWrite != Permissions.Write || allowExec != Permissions.Execute)
            throw new EngineProtocolError("Agent Tools hello is incompatible");
        if (value.GetValueOrDefault("capabilities") is not List<object?> rawCapabilities ||
            rawCapabilities.Any(item => item is not string))
            throw new EngineProtocolError("Agent Tools capabilities are invalid");
        var capabilities = rawCapabilities.Cast<string>().ToList();
        if (!capabilities.SequenceEqual(capabilities.Distinct(StringComparer.Ordinal)
            .OrderBy(item => item, StringComparer.Ordinal), StringComparer.Ordinal))
            throw new EngineProtocolError("Agent Tools capabilities are not canonical");
        var expectedCapabilities = new HashSet<string>(ReadTools, StringComparer.Ordinal);
        if (Permissions.Write) expectedCapabilities.UnionWith(WriteTools);
        if (Permissions.Execute) expectedCapabilities.UnionWith(ExecuteTools);
        if (!capabilities.ToHashSet(StringComparer.Ordinal).SetEquals(expectedCapabilities))
            throw new EngineProtocolError("Agent Tools capabilities do not match policy");
        capabilitiesValue = new ReadOnlyCollection<string>(capabilities);
    }

    private static ToolResult ParseToolResult(string tool, IReadOnlyDictionary<string, object?> value)
    {
        var expected = SetOf("text", "content_blocks", "original_tokens", "output_tokens",
            "saved_tokens", "mode", "changed", "shell");
        if (value.Count != expected.Count || value.Keys.Any(key => !expected.Contains(key)))
            throw new EngineProtocolError("Agent Tools result fields are invalid");
        if (value.GetValueOrDefault("text") is not string text ||
            WireJson.Utf8(text, "Agent Tools text").Length > WireJson.MaxTextBytes ||
            (value.GetValueOrDefault("mode") is not null &&
             value.GetValueOrDefault("mode") is not string))
            throw new EngineProtocolError("Agent Tools text or mode is invalid");
        var original = NonNegative(value, "original_tokens");
        var output = NonNegative(value, "output_tokens");
        var saved = NonNegative(value, "saved_tokens");
        if (output > original || saved > original || output + saved != original)
            throw new EngineProtocolError("Agent Tools token metrics are invalid");
        if (value.GetValueOrDefault("changed") is not bool changed)
            throw new EngineProtocolError("Agent Tools status metadata is invalid");
        var blocks = value.GetValueOrDefault("content_blocks") as List<object?> ??
            throw new EngineProtocolError("Agent Tools content blocks are invalid");
        var converted = new List<IReadOnlyDictionary<string, object?>>();
        foreach (var block in blocks)
        {
            if (block is not IReadOnlyDictionary<string, object?> dictionary)
                throw new EngineProtocolError("Agent Tools content blocks are invalid");
            converted.Add(new ReadOnlyDictionary<string, object?>(
                new Dictionary<string, object?>(dictionary, StringComparer.Ordinal)));
        }
        IReadOnlyDictionary<string, object?>? shell = null;
        if (value.GetValueOrDefault("shell") is not null)
        {
            if (value.GetValueOrDefault("shell") is not IReadOnlyDictionary<string, object?> shellValue)
                throw new EngineProtocolError("Agent Tools status metadata is invalid");
            shell = new ReadOnlyDictionary<string, object?>(
                new Dictionary<string, object?>(shellValue, StringComparer.Ordinal));
        }
        return new ToolResult(tool, text, new ReadOnlyCollection<IReadOnlyDictionary<string, object?>>(converted),
            original, output, saved, value.GetValueOrDefault("mode") as string, changed, shell);
    }

    private static long NonNegative(IReadOnlyDictionary<string, object?> value, string key)
    {
        if (value.GetValueOrDefault(key) is not long number || number < 0)
            throw new EngineProtocolError("Agent Tools token metrics are invalid");
        return number;
    }

    private static Exception ErrorFromWire(string code, string message) => code switch
    {
        "permission_denied" => new AgentPermissionError(message),
        "unsupported_capability" => new UnsupportedCapabilityError(message),
        "invalid_request" or "invalid_state" or "unsupported_interface" =>
            new EngineProtocolError(message),
        _ => new EngineExecutionError(message),
    };

    private async Task TerminateAsync(Exception? error)
    {
        if (Interlocked.Exchange(ref terminal, 1) == 0)
        {
            var failure = error ?? new EngineCrashed("AgentContext terminated");
            foreach (var pair in pending)
            {
                if (pending.TryRemove(pair.Key, out var waiter))
                    waiter.TrySetException(failure);
            }
            RemovePolicy();
            KillAndReap(process);
        }
        else
        {
            foreach (var pair in pending)
            {
                if (pending.TryRemove(pair.Key, out var waiter))
                    waiter.TrySetException(error ?? new EngineCrashed("AgentContext terminated"));
            }
        }
        await ReapAsync().ConfigureAwait(false);
    }

    private async Task ReapAsync()
    {
        try { await process.WaitForExitAsync().ConfigureAwait(false); }
        catch (InvalidOperationException) { }
        RemovePolicy();
        try { await stderrTask.ConfigureAwait(false); }
        catch { }
    }

    private async Task ReadStdErrAsync(Stream stream)
    {
        var buffer = new byte[8192];
        try
        {
            while (true)
            {
                var count = await stream.ReadAsync(buffer.AsMemory()).ConfigureAwait(false);
                if (count == 0) break;
                lock (sync)
                {
                    if (stderr.Count < WireJson.MaxStdErrBytes)
                        stderr.AddRange(buffer.AsSpan(0, Math.Min(count,
                            WireJson.MaxStdErrBytes - stderr.Count)).ToArray());
                }
            }
        }
        catch (IOException) { }
    }

    private async Task<string> CrashMessageAsync()
    {
        try { await stderrTask.ConfigureAwait(false); }
        catch { }
        return CrashMessage();
    }

    private string CrashMessage()
    {
        lock (sync)
        {
            var detail = Encoding.UTF8.GetString(stderr.ToArray()).Trim();
            return detail.Length == 0 ? "Agent Tools Engine exited" :
                "Agent Tools Engine exited: " + detail[..Math.Min(detail.Length, 4096)];
        }
    }

    private Process StartProcess(string binary)
    {
        var child = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = binary,
                WorkingDirectory = ProjectRoot,
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            },
            EnableRaisingEvents = true,
        };
        child.StartInfo.ArgumentList.Add("engine");
        child.StartInfo.ArgumentList.Add("tool-session");
        child.StartInfo.ArgumentList.Add("--project-root");
        child.StartInfo.ArgumentList.Add(ProjectRoot);
        child.StartInfo.ArgumentList.Add("--policy-file");
        child.StartInfo.ArgumentList.Add(policyPath);
        child.StartInfo.Environment.Clear();
        child.StartInfo.Environment["LANG"] = "C";
        child.StartInfo.Environment["LC_ALL"] = "C";
        child.StartInfo.Environment["PYTHONHASHSEED"] = "0";
        child.StartInfo.Environment["TZ"] = "UTC";
        try
        {
            if (!child.Start())
                throw new InvalidOperationException("Process.Start returned false");
            return child;
        }
        catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            child.Dispose();
            throw new EngineUnavailable("Agent Tools Engine could not be started", error);
        }
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
        return candidate;
    }

    private static bool IsExecutableFile(string path)
    {
        try
        {
            if (!File.Exists(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                return false;
            if (OperatingSystem.IsWindows()) return true;
            var mode = File.GetUnixFileMode(path);
            return mode.HasFlag(UnixFileMode.UserExecute) || mode.HasFlag(UnixFileMode.GroupExecute) ||
                mode.HasFlag(UnixFileMode.OtherExecute);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    private static string ValidateProjectRoot(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Contains('\0'))
            throw new ConfigurationError("project_root must be a directory");
        try
        {
            var root = Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar);
            if (!Directory.Exists(root)) throw new IOException();
            return root;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        {
            throw new ConfigurationError("project_root must be a directory", error);
        }
    }

    internal static bool IsSafeDirectory(string root, string candidate)
    {
        if (!ContextSource.Contained(candidate, root) || !Directory.Exists(candidate))
            return false;
        var current = root;
        var paths = new List<string> { current };
        var relative = Path.GetRelativePath(root, candidate);
        if (relative != ".")
        {
            foreach (var component in relative.Split(
                new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries))
            {
                current = Path.Combine(current, component);
                paths.Add(current);
            }
        }
        return paths.All(path =>
        {
            var info = new DirectoryInfo(path);
            return info.Exists && info.LinkTarget is null &&
                (info.Attributes & FileAttributes.ReparsePoint) == 0;
        });
    }

    private static void KillAndReap(Process child)
    {
        try { ProcessTree.KillAndReap(child, "Agent Tools"); }
        finally { child.Dispose(); }
    }

    private void RemovePolicy()
    {
        try { if (File.Exists(policyPath)) File.Delete(policyPath); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        try { if (Directory.Exists(policyDirectory)) Directory.Delete(policyDirectory); }
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

    private static Dictionary<string, object?> Args(
        params (string Key, object? Value)[] values) =>
        new Dictionary<string, object?>(values.ToDictionary(value => value.Key,
            value => value.Value, StringComparer.Ordinal), StringComparer.Ordinal);

    private static readonly IReadOnlyDictionary<string, object?> EmptyArguments =
        new ReadOnlyDictionary<string, object?>(new Dictionary<string, object?>());
    private static readonly IReadOnlyDictionary<string, string> EmptyEnvironment =
        new ReadOnlyDictionary<string, string>(new Dictionary<string, string>());

    private static string ModeText(ReadMode value) => value switch
    {
        ReadMode.Auto => "auto",
        ReadMode.Full => "full",
        ReadMode.Raw => "raw",
        ReadMode.Signatures => "signatures",
        ReadMode.Map => "map",
        ReadMode.Diff => "diff",
        ReadMode.Reference => "reference",
        ReadMode.Task => "task",
        _ => "anchored",
    };

    private static HashSet<string> SetOf(params string[] values) =>
        new(values, StringComparer.Ordinal);
}

/// <summary>Async-first wrapper that opens AgentContext on demand.</summary>
public sealed class AsyncAgentContext : IAsyncDisposable
{
    private readonly string projectRoot;
    private readonly string task;
    private readonly AgentPermissions? permissions;
    private readonly ExecutionPolicy? executionPolicy;
    private readonly string? engineBinary;
    private readonly double timeout;
    private AgentContext? context;

    public AsyncAgentContext(
        string projectRoot,
        string? task = null,
        AgentPermissions? permissions = null,
        ExecutionPolicy? executionPolicy = null,
        string? engineBinary = null,
        double timeout = 30)
    {
        this.projectRoot = projectRoot;
        this.task = task ?? string.Empty;
        this.permissions = permissions;
        this.executionPolicy = executionPolicy;
        this.engineBinary = engineBinary;
        this.timeout = timeout;
    }

    public async Task<AsyncAgentContext> OpenAsync(CancellationToken cancellationToken = default)
    {
        context ??= await AgentContext.OpenAsync(projectRoot, task, permissions,
            executionPolicy, engineBinary, timeout, cancellationToken).ConfigureAwait(false);
        return this;
    }

    public IReadOnlyList<string> Capabilities => Current.Capabilities;
    public AgentMetrics Metrics => Current.Metrics;
    public Task ReadyAsync() => Current.ReadyAsync();
    public Task<ToolResult> CallAsync(string tool,
        IReadOnlyDictionary<string, object?>? arguments = null,
        CancellationToken cancellationToken = default) =>
        Current.CallAsync(tool, arguments, cancellationToken);
    public Task<ToolResult> ReadAsync(string path, ReadMode mode = ReadMode.Auto,
        bool fresh = false, CancellationToken cancellationToken = default) =>
        Current.ReadAsync(path, mode, fresh, cancellationToken);
    public Task<ToolResult> SearchAsync(string pattern, string path = ".",
        int maxResults = 50, string? include = null,
        CancellationToken cancellationToken = default) =>
        Current.SearchAsync(pattern, path, maxResults, include, cancellationToken);
    public Task<ToolResult> GlobAsync(string pattern, string path = ".",
        int maxResults = 200, CancellationToken cancellationToken = default) =>
        Current.GlobAsync(pattern, path, maxResults, cancellationToken);
    public Task<ToolResult> TreeAsync(string path = ".", int depth = 3,
        bool showHidden = false, CancellationToken cancellationToken = default) =>
        Current.TreeAsync(path, depth, showHidden, cancellationToken);
    public Task<ToolResult> ComposeAsync(string? task = null, string path = ".",
        CancellationToken cancellationToken = default) =>
        Current.ComposeAsync(task, path, cancellationToken);
    public Task<ToolResult> SymbolAsync(string name, CancellationToken cancellationToken = default) =>
        Current.SymbolAsync(name, cancellationToken);
    public Task<ToolResult> PatchAsync(IReadOnlyDictionary<string, object?> arguments,
        CancellationToken cancellationToken = default) => Current.PatchAsync(arguments, cancellationToken);
    public Task<ToolResult> CreateFileAsync(string path, string text,
        CancellationToken cancellationToken = default) => Current.CreateFileAsync(path, text, cancellationToken);
    public Task<ToolResult> ReplaceUniqueAsync(string path, string oldText, string newText,
        CancellationToken cancellationToken = default) => Current.ReplaceUniqueAsync(path, oldText, newText, cancellationToken);
    public Task<ToolResult> RunAsync(IReadOnlyList<string> argv, string cwd = ".",
        IReadOnlyDictionary<string, string>? env = null, double? timeout = null,
        CancellationToken cancellationToken = default) =>
        Current.RunAsync(argv, cwd, env, timeout, cancellationToken);
    public Task CancelAsync() => context?.CancelAsync() ?? Task.CompletedTask;
    public Task ReconnectAsync() => ReconnectCoreAsync();
    public Task CloseAsync() => context?.CloseAsync() ?? Task.CompletedTask;
    public ValueTask DisposeAsync() => context is null ? ValueTask.CompletedTask : context.DisposeAsync();

    private AgentContext Current => context ?? throw new EngineUnavailable(
        "AsyncAgentContext is not open");

    private async Task ReconnectCoreAsync()
    {
        if (context is null) await OpenAsync().ConfigureAwait(false);
        else context = await context.ReconnectAsync().ConfigureAwait(false);
    }
}
