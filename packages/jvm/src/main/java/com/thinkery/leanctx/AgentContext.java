package com.thinkery.leanctx;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.file.attribute.FileAttribute;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/** Persistent JSONL Agent Tools 1.1 client with fail-closed protocol handling. */
public final class AgentContext implements AutoCloseable {
    public static final String AGENT_TOOLS_INTERFACE_VERSION = "1.0.0";
    public static final int AGENT_TOOLS_SCHEMA_VERSION = 1;
    public static final int AGENT_TOOLS_TRANSPORT_VERSION = 1;
    public static final String SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = "3.10.1";

    private static final int MAX_REQUEST_BYTES = 1024 * 1024;
    private static final int MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
    private static final int MAX_TEXT_BYTES = 8 * 1024 * 1024;
    private static final Set<String> READ_TOOLS = Set.of(
            "ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree");
    private static final Set<String> WRITE_TOOLS = Set.of("ctx_edit", "ctx_fill", "ctx_patch");
    private static final Set<String> EXECUTE_TOOLS = Set.of("ctx_shell");
    private static final Set<String> HELLO_KEYS = Set.of(
            "agent_tools_interface_version", "allow_exec", "allow_write", "capabilities",
            "engine_version", "schema_version", "transport_version");
    private static final Set<String> RESULT_KEYS = Set.of(
            "text", "content_blocks", "original_tokens", "output_tokens", "saved_tokens",
            "mode", "changed", "shell");
    private static final Set<String> OK_RESPONSE_KEYS = Set.of("id", "ok", "result");
    private static final Set<String> ERROR_RESPONSE_KEYS = Set.of("id", "ok", "error");
    private static final Set<String> ERROR_KEYS = Set.of("code", "message");
    private static final ThreadFactory DAEMON_FACTORY = task -> {
        return Thread.ofVirtual().name("leanctx-agent-deadline").unstarted(task);
    };
    private static final ScheduledExecutorService DEADLINES =
            Executors.newScheduledThreadPool(2, DAEMON_FACTORY);

    private final Path projectRoot;
    private final String task;
    private final AgentPermissions permissions;
    private final ExecutionPolicy executionPolicy;
    private final String engineBinary;
    private final double timeout;
    private final AtomicLong nextId = new AtomicLong();
    private final ConcurrentHashMap<String, PendingCall> pending = new ConcurrentHashMap<>();
    private final Object writeLock = new Object();
    private final Object lifecycleLock = new Object();
    private final Object metricsLock = new Object();
    private final ByteArrayOutputStream stderr = new ByteArrayOutputStream();
    private volatile Process process;
    private volatile Thread stdoutReader;
    private volatile boolean closed;
    private volatile boolean helloAccepted;
    private volatile Path policyPath;
    private volatile Path policyDirectory;
    private volatile List<String> capabilities = List.of();
    private volatile AgentMetrics metrics = new AgentMetrics();

    public AgentContext(String projectRoot) {
        this(projectRoot, "", new AgentPermissions(), new ExecutionPolicy(), "lean-ctx", 30.0);
    }

    public AgentContext(Path projectRoot) {
        this(projectRoot == null ? null : projectRoot.toString());
    }

    public AgentContext(String projectRoot, String task, AgentPermissions permissions,
                        ExecutionPolicy executionPolicy, String engineBinary, double timeout) {
        this.projectRoot = canonicalRoot(projectRoot);
        this.task = boundedTask(task);
        this.permissions = permissions == null ? new AgentPermissions() : permissions;
        this.executionPolicy = executionPolicy == null ? new ExecutionPolicy() : executionPolicy;
        if (this.permissions.execute() && this.executionPolicy.allowedExecutables().isEmpty()) {
            throw new ConfigurationError("execute permission requires at least one allowed executable");
        }
        if (engineBinary == null || engineBinary.isEmpty()) {
            throw new ConfigurationError("engine_binary must not be empty");
        }
        if (!Double.isFinite(timeout) || timeout < 0.1 || timeout > 120.0) {
            throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
        }
        this.engineBinary = engineBinary;
        this.timeout = timeout;
        startTransactional();
    }

    public AgentContext(Path projectRoot, String task, AgentPermissions permissions,
                        ExecutionPolicy executionPolicy, Path engineBinary, double timeout) {
        this(projectRoot == null ? null : projectRoot.toString(), task, permissions,
                executionPolicy, engineBinary == null ? null : engineBinary.toString(), timeout);
    }

    public static AgentContext open(String projectRoot) {
        return new AgentContext(projectRoot);
    }

    public static AgentContext open(Path projectRoot) {
        return new AgentContext(projectRoot);
    }

    public static AgentContext open(String projectRoot, String task,
                                    AgentPermissions permissions,
                                    ExecutionPolicy executionPolicy,
                                    String engineBinary, double timeout) {
        return new AgentContext(projectRoot.toString(), task, permissions, executionPolicy,
                engineBinary, timeout);
    }

    public Path projectRoot() {
        return projectRoot;
    }

    public Path getProjectRoot() {
        return projectRoot;
    }

    public String project_root() {
        return projectRoot.toString();
    }

    public String task() {
        return task;
    }

    public String getTask() {
        return task;
    }

    public AgentPermissions permissions() {
        return permissions;
    }

    public AgentPermissions getPermissions() {
        return permissions;
    }

    public ExecutionPolicy executionPolicy() {
        return executionPolicy;
    }

    public ExecutionPolicy getExecutionPolicy() {
        return executionPolicy;
    }

    public String engineBinary() {
        return engineBinary;
    }

    public String getEngineBinary() {
        return engineBinary;
    }

    public double timeout() {
        return timeout;
    }

    public double getTimeout() {
        return timeout;
    }

    public List<String> capabilities() {
        return capabilities;
    }

    public List<String> getCapabilities() {
        return capabilities;
    }

    public AgentMetrics metrics() {
        return metrics;
    }

    public AgentMetrics getMetrics() {
        return metrics;
    }

    /** Synchronous result of a read/write Agent Tool call. */
    public ToolResult call(String tool) {
        return call(tool, Map.of());
    }

    /** Synchronous result of a read/write Agent Tool call. */
    public ToolResult call(String tool, Map<String, ?> arguments) {
        return join(callAsync(tool, arguments));
    }

    /** Asynchronous call; cancelling the returned future terminates this session. */
    public CompletableFuture<ToolResult> callAsync(String tool, Map<String, ?> arguments) {
        if (tool == null || tool.isEmpty()) {
            throw new ValidationError("tool must be a non-empty string");
        }
        if (EXECUTE_TOOLS.contains(tool)) {
            throw new AgentPermissionError("execution tools must use run()");
        }
        if (WRITE_TOOLS.contains(tool) && !permissions.write()) {
            throw new AgentPermissionError("write permission is disabled");
        }
        return callToolAsync(tool, arguments == null ? Map.of() : arguments, timeout);
    }

    public ToolResult read(String path) {
        return read(path, ReadMode.AUTO, false);
    }

    public ToolResult read(String path, ReadMode mode, boolean fresh) {
        return callTool("ctx_read", Map.of(
                "path", checkedArgumentString(path, "path"),
                "mode", mode == null ? ReadMode.AUTO.value() : mode.value(),
                "fresh", fresh));
    }

    public CompletableFuture<ToolResult> readAsync(String path, ReadMode mode, boolean fresh) {
        return callToolAsync("ctx_read", Map.of(
                "path", checkedArgumentString(path, "path"),
                "mode", mode == null ? ReadMode.AUTO.value() : mode.value(),
                "fresh", fresh), timeout);
    }

    public ToolResult search(String pattern) {
        return search(pattern, ".", 50, null);
    }

    public ToolResult search(String pattern, String path, int maxResults, String include) {
        Map<String, Object> arguments = new LinkedHashMap<>();
        arguments.put("path", path == null ? "." : path);
        arguments.put("pattern", checkedArgumentString(pattern, "pattern"));
        arguments.put("max_results", checkedPositive(maxResults, "max_results"));
        if (include != null) {
            arguments.put("include", include);
        }
        return callTool("ctx_search", arguments);
    }

    public ToolResult glob(String pattern) {
        return glob(pattern, ".", 200);
    }

    public ToolResult glob(String pattern, String path, int maxResults) {
        return callTool("ctx_glob", Map.of(
                "path", path == null ? "." : path,
                "pattern", checkedArgumentString(pattern, "pattern"),
                "max_results", checkedPositive(maxResults, "max_results")));
    }

    public ToolResult tree() {
        return tree(".", 3, false);
    }

    public ToolResult tree(String path, int depth, boolean showHidden) {
        return callTool("ctx_tree", Map.of(
                "path", path == null ? "." : path,
                "depth", checkedPositive(depth, "depth"),
                "show_hidden", showHidden));
    }

    public ToolResult compose() {
        return compose(task, ".");
    }

    public ToolResult compose(String composeTask, String path) {
        return callTool("ctx_compose", Map.of(
                "path", path == null ? "." : path,
                "task", checkedArgumentString(composeTask, "task")));
    }

    public ToolResult symbol(String name) {
        return callTool("ctx_symbol", Map.of("name", checkedArgumentString(name, "name")));
    }

    public ToolResult patch(Map<String, ?> arguments) {
        if (!permissions.write()) {
            throw new AgentPermissionError("write permission is disabled");
        }
        return callTool("ctx_patch", arguments == null ? Map.of() : arguments);
    }

    public ToolResult createFile(String path, String text) {
        return patch(Map.of("path", path, "op", "create", "new_text", text));
    }

    public ToolResult replaceUnique(String path, String oldText, String newText) {
        return patch(Map.of("path", path, "op", "replace_unique",
                "old_text", oldText, "new_text", newText));
    }

    public ToolResult create_file(String path, String text) {
        return createFile(path, text);
    }

    public ToolResult replace_unique(String path, String oldText, String newText) {
        return replaceUnique(path, oldText, newText);
    }

    public ToolResult run(List<String> argv) {
        return run(argv, ".", Map.of(), executionPolicy.maxTimeout());
    }

    public ToolResult run(List<String> argv, String cwd, Map<String, String> env,
                          double timeoutSeconds) {
        return join(runAsync(argv, cwd, env, timeoutSeconds));
    }

    public CompletableFuture<ToolResult> runAsync(List<String> argv, String cwd,
                                                   Map<String, String> env,
                                                   double timeoutSeconds) {
        if (!permissions.execute()) {
            throw new AgentPermissionError("execute permission is disabled");
        }
        List<String> checkedArgv = checkArgv(argv);
        String executable = checkedArgv.get(0);
        if (executable.indexOf('/') >= 0 || executable.indexOf('\\') >= 0
                || !executionPolicy.allowedExecutables().contains(executable)) {
            throw new AgentPermissionError("executable is not allowed: " + executable);
        }
        if (!Double.isFinite(timeoutSeconds) || timeoutSeconds < 0.1
                || timeoutSeconds > executionPolicy.maxTimeout()) {
            throw new ValidationError("timeout exceeds ExecutionPolicy");
        }
        String checkedCwd = checkCwd(cwd == null ? "." : cwd);
        Map<String, String> checkedEnv = checkEnvironment(env == null ? Map.of() : env);
        Map<String, Object> arguments = new LinkedHashMap<>();
        arguments.put("argv", checkedArgv);
        arguments.put("cwd", checkedCwd);
        arguments.put("env", checkedEnv);
        arguments.put("timeout_ms", (long) Math.floor(timeoutSeconds * 1000.0));
        return callToolAsync("ctx_shell", arguments, Math.max(timeout, timeoutSeconds + 2.0));
    }

    /** Terminates the process group and rejects all pending calls. */
    public void cancel() {
        terminate(new EngineCrashed("AgentContext cancelled"));
    }

    /** Reconnects with the same immutable policy and task. */
    public AgentContext reconnect() {
        close();
        return new AgentContext(projectRoot.toString(), task, permissions, executionPolicy,
                engineBinary, timeout);
    }

    @Override
    public void close() {
        if (!closed) {
            try {
                join(exchangeRaw(Map.of("op", "close"), timeout));
            } catch (RuntimeException ignored) {
                // Close remains terminal and best effort after a protocol/process failure.
            }
        }
        terminate(new EngineCrashed("AgentContext closed"));
        reapReader();
        removePolicy();
    }

    private void startTransactional() {
        try {
            createPolicy();
            Path binary = resolveBinary();
            ProcessBuilder builder = new ProcessBuilder(
                    binary.toString(), "engine", "tool-session",
                    "--project-root", projectRoot.toString(),
                    "--policy-file", policyPath.toString());
            builder.directory(projectRoot.toFile());
            builder.redirectErrorStream(false);
            Map<String, String> environment = builder.environment();
            environment.clear();
            environment.put("LANG", "C");
            environment.put("LC_ALL", "C");
            environment.put("TZ", "UTC");
            environment.put("PYTHONHASHSEED", "0");
            process = builder.start();
            stdoutReader = Thread.ofVirtual().name("leanctx-agent-stdout").start(this::readLoop);
            Map<String, Object> hello = new LinkedHashMap<>();
            hello.put("op", "hello");
            hello.put("schema_version", AGENT_TOOLS_SCHEMA_VERSION);
            hello.put("transport_version", AGENT_TOOLS_TRANSPORT_VERSION);
            hello.put("agent_tools_interface_version", AGENT_TOOLS_INTERFACE_VERSION);
            hello.put("sdk_version", LeanCtx.__version__);
            Map<String, Object> result = join(exchangeRaw(hello, timeout));
            acceptHello(result);
            helloAccepted = true;
            removePolicy();
        } catch (RuntimeException | IOException exception) {
            removePolicy();
            terminate(new EngineUnavailable("Agent Tools Engine could not be started", exception));
            if (exception instanceof SDKError error) {
                throw error;
            }
            throw new EngineUnavailable("Agent Tools Engine could not be started", exception);
        }
    }

    private CompletableFuture<Map<String, Object>> exchangeRaw(Map<String, ?> request,
                                                                double responseTimeout) {
        Map<String, ?> checkedRequest = request == null ? Map.of() : request;
        if (closed || process == null || !process.isAlive()) {
            return failedFuture(new EngineCrashed(crashMessage()));
        }
        if (!Double.isFinite(responseTimeout) || responseTimeout < 0.1) {
            return failedFuture(new ValidationError("response timeout must be at least 0.1 seconds"));
        }
        String id = Long.toString(nextId.incrementAndGet());
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.putAll(copyObject(checkedRequest, "request"));
        envelope.put("id", id);
        byte[] encoded;
        try {
            encoded = appendNewline(Json.canonicalBytes(envelope));
        } catch (ValidationError exception) {
            return failedFuture(new EngineProtocolError("Agent Tools request is not deterministic", exception));
        }
        if (encoded.length > MAX_REQUEST_BYTES) {
            return failedFuture(new EngineProtocolError("Agent Tools request exceeds its bound"));
        }
        CancellableFuture<Map<String, Object>> future = new CancellableFuture<>(
                () -> terminate(new EngineTimeout("Agent Tools request cancelled")));
        PendingCall call = new PendingCall(future);
        pending.put(id, call);
        call.deadline = DEADLINES.schedule(() -> {
            if (pending.remove(id, call)) {
                future.completeExceptionally(new EngineTimeout(
                        "Agent Tools response exceeded its deadline"));
                terminate(new EngineTimeout("Agent Tools response exceeded its deadline"));
            }
        }, Math.max(1L, (long) Math.ceil(responseTimeout * 1000.0)), TimeUnit.MILLISECONDS);
        try {
            synchronized (writeLock) {
                if (closed || process == null || !process.isAlive()) {
                    throw new IOException("Agent Tools process is not alive");
                }
                process.getOutputStream().write(encoded);
                process.getOutputStream().flush();
            }
        } catch (IOException | RuntimeException exception) {
            if (pending.remove(id, call)) {
                call.cancelDeadline();
            }
            EngineCrashed failure = new EngineCrashed("Agent Tools Engine input failed", exception);
            future.completeExceptionally(failure);
            terminate(failure);
        }
        return future;
    }

    private CompletableFuture<ToolResult> callToolAsync(String tool,
                                                         Map<String, ?> arguments,
                                                         double responseTimeout) {
        if (!capabilities.contains(tool)) {
            throw new UnsupportedCapabilityError(
                    "Engine did not negotiate capability: " + tool);
        }
        Map<String, Object> checkedArguments = copyObject(arguments, "arguments");
        try {
            if (Json.canonicalBytes(checkedArguments).length > MAX_REQUEST_BYTES) {
                throw new ValidationError("arguments exceed the request bound");
            }
        } catch (ValidationError exception) {
            throw exception;
        } catch (RuntimeException exception) {
            throw new ValidationError("arguments must be deterministic JSON data", exception);
        }
        CompletableFuture<Map<String, Object>> raw = exchangeRaw(Map.of(
                "op", "call", "tool", tool, "arguments", checkedArguments), responseTimeout);
        CancellableFuture<ToolResult> result = new CancellableFuture<>(
                () -> terminate(new EngineTimeout("Agent Tools call cancelled")));
        raw.whenComplete((value, error) -> {
            if (error != null) {
                result.completeExceptionally(unwrap(error));
                return;
            }
            try {
                ToolResult parsed = parseToolResult(tool, value);
                synchronized (metricsLock) {
                    AgentMetrics current = metrics;
                    metrics = new AgentMetrics(current.toolCalls() + 1,
                            current.originalTokens() + parsed.originalTokens(),
                            current.outputTokens() + parsed.outputTokens(),
                            current.savedTokens() + parsed.savedTokens());
                }
                result.complete(parsed);
            } catch (EngineProtocolError exception) {
                result.completeExceptionally(exception);
                protocolViolation(exception);
            } catch (RuntimeException exception) {
                result.completeExceptionally(exception);
            }
        });
        return result;
    }

    private ToolResult callTool(String tool, Map<String, ?> arguments) {
        return join(callToolAsync(tool, arguments, timeout));
    }

    private ToolResult parseToolResult(String tool, Map<String, Object> value) {
        if (value == null) {
            throw new EngineProtocolError("Agent Tools result is missing");
        }
        Json.exactKeys(value, RESULT_KEYS, "Agent Tools result");
        String text = Json.string(value.get("text"), "text", MAX_TEXT_BYTES);
        Object rawBlocks = value.get("content_blocks");
        if (!(rawBlocks instanceof List<?> blocks)) {
            throw new EngineProtocolError("content_blocks must be a list");
        }
        List<Map<String, Object>> contentBlocks = new ArrayList<>();
        for (Object rawBlock : blocks) {
            contentBlocks.add(copyObject(rawBlock, "content_block"));
        }
        long original = Json.integer(value.get("original_tokens"), "original_tokens");
        long output = Json.integer(value.get("output_tokens"), "output_tokens");
        long saved = Json.integer(value.get("saved_tokens"), "saved_tokens");
        if (original < 0 || output < 0 || saved < 0 || output > original
                || saved != original - output) {
            throw new EngineProtocolError("Agent Tools token metrics are invalid");
        }
        String mode = value.get("mode") == null
                ? null : Json.string(value.get("mode"), "mode", Protocol.MAX_REF_BYTES);
        if (!(value.get("changed") instanceof Boolean changed)) {
            throw new EngineProtocolError("changed must be boolean");
        }
        Map<String, Object> shell = value.get("shell") == null
                ? null : copyObject(value.get("shell"), "shell");
        return new ToolResult(tool, text, contentBlocks, original, output, saved,
                mode, changed, shell);
    }

    private void acceptHello(Map<String, Object> value) {
        Json.exactKeys(value, HELLO_KEYS, "Agent Tools hello");
        long schemaVersion = Json.integer(value.get("schema_version"), "schema_version");
        long transportVersion = Json.integer(value.get("transport_version"), "transport_version");
        if (!AGENT_TOOLS_INTERFACE_VERSION.equals(value.get("agent_tools_interface_version"))
                || schemaVersion != AGENT_TOOLS_SCHEMA_VERSION
                || transportVersion != AGENT_TOOLS_TRANSPORT_VERSION
                || !SUPPORTED_AGENT_TOOLS_ENGINE_VERSION.equals(value.get("engine_version"))
                || !Boolean.valueOf(permissions.write()).equals(value.get("allow_write"))
                || !Boolean.valueOf(permissions.execute()).equals(value.get("allow_exec"))) {
            throw new EngineProtocolError("Agent Tools hello is incompatible");
        }
        Object rawCapabilities = value.get("capabilities");
        if (!(rawCapabilities instanceof List<?> list)) {
            throw new EngineProtocolError("Agent Tools capabilities are invalid");
        }
        List<String> received = new ArrayList<>();
        for (Object item : list) {
            if (!(item instanceof String capability) || capability.isEmpty()) {
                throw new EngineProtocolError("Agent Tools capabilities are invalid");
            }
            received.add(capability);
        }
        List<String> sorted = new ArrayList<>(new TreeSet<>(received));
        if (sorted.size() != received.size() || !sorted.equals(received)) {
            throw new EngineProtocolError("Agent Tools capabilities are not canonical");
        }
        Set<String> expected = new HashSet<>(READ_TOOLS);
        if (permissions.write()) {
            expected.addAll(WRITE_TOOLS);
        }
        if (permissions.execute()) {
            expected.addAll(EXECUTE_TOOLS);
        }
        if (!expected.equals(new HashSet<>(received))) {
            throw new EngineProtocolError("Agent Tools capabilities do not match policy");
        }
        capabilities = Collections.unmodifiableList(received);
    }

    private void readLoop() {
        Process child = process;
        if (child == null) {
            return;
        }
        try (InputStream input = child.getInputStream();
             InputStream error = child.getErrorStream()) {
            Thread stderrReader = Thread.ofVirtual().name("leanctx-agent-stderr").start(
                    () -> readStderr(error));
            readResponses(input);
            stderrReader.join();
            if (!closed) {
                protocolOrCrash(new EngineCrashed(crashMessage()));
            }
        } catch (OutputLimitExceeded exception) {
            protocolViolation(new EngineProtocolError("Agent Tools response exceeds its bound", exception));
        } catch (IOException exception) {
            if (!closed) {
                protocolOrCrash(new EngineCrashed("Agent Tools stdout failed", exception));
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        }
    }

    private void readResponses(InputStream input) throws IOException, OutputLimitExceeded {
        ByteArrayOutputStream line = new ByteArrayOutputStream(Math.min(MAX_RESPONSE_BYTES, 8192));
        int next;
        while ((next = input.read()) >= 0) {
            if (next == '\n') {
                dispatch(line.toByteArray());
                line.reset();
                continue;
            }
            if (line.size() >= MAX_RESPONSE_BYTES) {
                throw new OutputLimitExceeded();
            }
            line.write(next);
        }
        if (line.size() > 0) {
            throw new EngineProtocolError("Agent Tools response is not newline terminated");
        }
    }

    private void readStderr(InputStream input) {
        byte[] buffer = new byte[4096];
        try {
            int count;
            while ((count = input.read(buffer)) >= 0) {
                synchronized (stderr) {
                    int remaining = Protocol.MAX_STDERR_BYTES - stderr.size();
                    if (remaining > 0) {
                        stderr.write(buffer, 0, Math.min(remaining, count));
                    }
                }
            }
        } catch (IOException ignored) {
            // Process termination closes stderr; retained bytes still aid diagnostics.
        }
    }

    private void dispatch(byte[] line) {
        Object parsed;
        try {
            parsed = Json.parse(line, "Agent Tools response");
        } catch (EngineProtocolError exception) {
            protocolViolation(new EngineProtocolError("Agent Tools response is invalid JSON", exception));
            return;
        }
        Map<String, Object> response;
        PendingCall call = null;
        try {
            response = Json.object(parsed, "Agent Tools response");
            Object idValue = response.get("id");
            Object okValue = response.get("ok");
            if (!(idValue instanceof String id) || !(okValue instanceof Boolean ok)) {
                throw new EngineProtocolError("Agent Tools response envelope is invalid");
            }
            call = pending.remove(id);
            if (call == null) {
                throw new EngineProtocolError("Agent Tools response id is unexpected");
            }
            call.cancelDeadline();
            if (ok) {
                Json.exactKeys(response, OK_RESPONSE_KEYS, "Agent Tools response");
                Map<String, Object> result = Json.object(response.get("result"),
                        "Agent Tools response result");
                call.future.complete(result);
            } else {
                Json.exactKeys(response, ERROR_RESPONSE_KEYS, "Agent Tools response");
                Map<String, Object> error = Json.object(response.get("error"),
                        "Agent Tools error");
                Json.exactKeys(error, ERROR_KEYS, "Agent Tools error");
                String code = Json.string(error.get("code"), "error.code", Protocol.MAX_REF_BYTES);
                String message = Json.string(error.get("message"), "error.message", MAX_TEXT_BYTES);
                call.future.completeExceptionally(errorFromWire(code, message));
            }
        } catch (EngineProtocolError exception) {
            if (call != null) {
                call.cancelDeadline();
                call.future.completeExceptionally(exception);
            }
            protocolViolation(exception);
        } catch (RuntimeException exception) {
            if (call != null) {
                call.cancelDeadline();
                call.future.completeExceptionally(exception);
            }
            protocolViolation(new EngineProtocolError("Agent Tools response envelope is invalid", exception));
        }
    }

    private SDKError errorFromWire(String code, String message) {
        return switch (code) {
            case "permission_denied" -> new AgentPermissionError(message);
            case "unsupported_capability" -> new UnsupportedCapabilityError(message);
            case "invalid_request", "invalid_state", "unsupported_interface" ->
                    new EngineProtocolError(message);
            default -> new EngineExecutionError(message);
        };
    }

    private void protocolOrCrash(EngineError error) {
        if (error instanceof EngineProtocolError protocol) {
            protocolViolation(protocol);
        } else {
            terminate(error);
        }
    }

    /** Protocol violations are terminal and reject every pending call. */
    private void protocolViolation(EngineProtocolError error) {
        terminate(error);
    }

    private void terminate(EngineError reason) {
        Process child;
        synchronized (lifecycleLock) {
            if (closed) {
                removePolicy();
                return;
            }
            closed = true;
            failPending(reason);
            child = process;
        }
        if (child != null && child.isAlive()) {
            killProcessTree(child);
        }
        removePolicy();
    }

    private void failPending(EngineError error) {
        for (Map.Entry<String, PendingCall> entry : pending.entrySet()) {
            PendingCall call = pending.remove(entry.getKey());
            if (call != null) {
                call.cancelDeadline();
                call.future.completeExceptionally(error);
            }
        }
    }

    private static void killProcessTree(Process child) {
        try {
            ProcessHandle handle = child.toHandle();
            List<ProcessHandle> descendants = handle.descendants().toList();
            for (int i = descendants.size() - 1; i >= 0; i--) {
                descendants.get(i).destroyForcibly();
            }
            handle.destroyForcibly();
        } catch (RuntimeException ignored) {
            child.destroyForcibly();
        }
    }

    private void reapReader() {
        Thread reader = stdoutReader;
        Process child = process;
        if (child != null) {
            try {
                child.waitFor(2, TimeUnit.SECONDS);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
        }
        if (reader != null && reader != Thread.currentThread()) {
            try {
                reader.join(2000);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private void createPolicy() throws IOException {
        try {
            policyDirectory = Files.createTempDirectory(projectRoot, ".leanctx-agent-",
                    privateDirectoryAttribute());
        } catch (UnsupportedOperationException exception) {
            policyDirectory = Files.createTempDirectory(projectRoot, ".leanctx-agent-");
            setPermissions(policyDirectory, true);
        }
        policyPath = policyDirectory.resolve("policy.json");
        try {
            Files.createFile(policyPath, privateFileAttribute());
        } catch (UnsupportedOperationException exception) {
            Files.createFile(policyPath);
            setPermissions(policyPath, false);
        }
        Map<String, Object> policy = new LinkedHashMap<>();
        policy.put("allow_exec", permissions.execute());
        policy.put("allow_write", permissions.write());
        policy.put("allowed_env", executionPolicy.allowedEnv());
        policy.put("allowed_executables", executionPolicy.allowedExecutables());
        policy.put("max_timeout_ms", (long) Math.floor(executionPolicy.maxTimeout() * 1000.0));
        policy.put("schema_version", AGENT_TOOLS_SCHEMA_VERSION);
        byte[] payload = Json.canonicalBytes(policy);
        try (OutputStream output = Files.newOutputStream(policyPath)) {
            output.write(payload);
            output.flush();
        }
        try (var channel = java.nio.channels.FileChannel.open(policyPath,
                java.nio.file.StandardOpenOption.WRITE)) {
            channel.force(true);
        }
        setPermissions(policyPath, false);
        setPermissions(policyDirectory, true);
    }

    private void removePolicy() {
        Path path = policyPath;
        Path directory = policyDirectory;
        policyPath = null;
        policyDirectory = null;
        if (path != null) {
            try {
                Files.deleteIfExists(path);
            } catch (IOException ignored) {
                // Best effort cleanup after process termination.
            }
        }
        if (directory != null) {
            try {
                Files.deleteIfExists(directory);
            } catch (IOException ignored) {
                // Directory is SDK-owned and contains no user files.
            }
        }
    }

    private Path resolveBinary() {
        Path candidate;
        if (!engineBinary.contains("/") && !engineBinary.contains("\\")) {
            String path = System.getenv("PATH");
            candidate = null;
            if (path != null) {
                for (String entry : path.split(java.util.regex.Pattern.quote(
                        java.io.File.pathSeparator), -1)) {
                    if (entry.isEmpty()) {
                        continue;
                    }
                    Path item;
                    try {
                        item = Path.of(entry).resolve(engineBinary).toAbsolutePath().normalize();
                    } catch (InvalidPathException exception) {
                        continue;
                    }
                    if (Files.isRegularFile(item) && Files.isExecutable(item)) {
                        candidate = item;
                        break;
                    }
                }
            }
            if (candidate == null) {
                throw new EngineUnavailable("configured Engine binary is unavailable");
            }
        } else {
            try {
                candidate = Path.of(engineBinary).toAbsolutePath().normalize();
            } catch (InvalidPathException exception) {
                throw new EngineUnavailable("configured Engine binary is unavailable", exception);
            }
            if (!Files.isRegularFile(candidate) || !Files.isExecutable(candidate)) {
                throw new EngineUnavailable("configured Engine binary is unavailable");
            }
        }
        try {
            Path canonical = candidate.toRealPath();
            if (!Files.isRegularFile(canonical) || !Files.isExecutable(canonical)) {
                throw new IOException("binary is not executable");
            }
            return canonical;
        } catch (IOException exception) {
            throw new EngineUnavailable("configured Engine binary is unavailable", exception);
        }
    }

    private static Path canonicalRoot(String value) {
        if (value == null || value.isEmpty() || value.indexOf('\0') >= 0) {
            throw new ConfigurationError("project_root must be a directory");
        }
        try {
            Path root = Path.of(value).toAbsolutePath().normalize().toRealPath();
            if (!Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS)) {
                throw new IOException("not a directory");
            }
            if (root.toString().getBytes(StandardCharsets.UTF_8).length > Protocol.MAX_PATH_BYTES) {
                throw new IOException("path exceeds bound");
            }
            return root;
        } catch (IOException | InvalidPathException exception) {
            throw new ConfigurationError("project_root must be a directory", exception);
        }
    }

    private static String boundedTask(String value) {
        String task = value == null ? "" : value;
        Json.validateUnicode(task, "task");
        if (task.getBytes(StandardCharsets.UTF_8).length > Protocol.MAX_TASK_BYTES
                || task.indexOf('\0') >= 0) {
            throw new ValidationError("task must be a bounded string");
        }
        return task;
    }

    private String checkCwd(String value) {
        if (value == null || value.isEmpty() || value.indexOf('\0') >= 0) {
            throw new ValidationError("cwd must be a directory inside project root");
        }
        try {
            Path requested = Path.of(value);
            Path lexical = (requested.isAbsolute() ? requested : projectRoot.resolve(requested))
                    .normalize();
            if (!Protocol.contained(lexical, projectRoot)
                    || !Files.isDirectory(lexical, LinkOption.NOFOLLOW_LINKS)) {
                throw new AgentPermissionError("cwd escapes project root");
            }
            Path real = lexical.toRealPath();
            if (!Protocol.contained(real, projectRoot)) {
                throw new AgentPermissionError("cwd escapes project root");
            }
            return value;
        } catch (InvalidPathException | IOException exception) {
            throw new AgentPermissionError("cwd escapes project root", exception);
        }
    }

    private Map<String, String> checkEnvironment(Map<String, String> values) {
        Map<String, String> copy = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : values.entrySet()) {
            String name = entry.getKey();
            String value = entry.getValue();
            if (name == null || !executionPolicy.allowedEnv().contains(name)) {
                throw new AgentPermissionError("environment variable is not allowed: " + name);
            }
            if (value == null) {
                throw new ValidationError("env must be a string mapping");
            }
            Json.validateUnicode(value, "env value");
            if (value.indexOf('\0') >= 0) {
                throw new ValidationError("env values must not contain NUL");
            }
            copy.put(name, value);
        }
        return Collections.unmodifiableMap(copy);
    }

    private static List<String> checkArgv(List<String> values) {
        if (values == null || values.isEmpty()) {
            throw new ValidationError("argv must be a non-empty sequence of strings");
        }
        List<String> copy = new ArrayList<>();
        for (String value : values) {
            if (value == null || value.isEmpty()) {
                throw new ValidationError("argv must be a non-empty sequence of strings");
            }
            Json.validateUnicode(value, "argv");
            if (value.indexOf('\0') >= 0) {
                throw new ValidationError("argv values must not contain NUL");
            }
            copy.add(value);
        }
        return Collections.unmodifiableList(copy);
    }

    private static String checkedArgumentString(String value, String field) {
        return Protocol.text(value, field, Protocol.MAX_TEXT_BYTES, false);
    }

    private static int checkedPositive(int value, String field) {
        if (value < 0 || value > Json.MAX_SAFE_INTEGER) {
            throw new ValidationError(field + " must be a non-negative integer");
        }
        return value;
    }

    private static Map<String, Object> copyObject(Object value, String label) {
        if (!(value instanceof Map<?, ?> map)) {
            throw new ValidationError(label + " must be a string-keyed mapping");
        }
        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new ValidationError(label + " keys must be strings");
            }
            copy.put(key, entry.getValue());
        }
        try {
            Json.canonical(copy);
        } catch (RuntimeException exception) {
            throw new ValidationError(label + " must be deterministic JSON data", exception);
        }
        return copy;
    }

    private static byte[] appendNewline(byte[] payload) {
        byte[] result = new byte[payload.length + 1];
        System.arraycopy(payload, 0, result, 0, payload.length);
        result[payload.length] = '\n';
        return result;
    }

    private String crashMessage() {
        String detail;
        synchronized (stderr) {
            detail = stderr.toString(StandardCharsets.UTF_8);
        }
        detail = detail.trim();
        return detail.isEmpty() ? "Agent Tools Engine exited" :
                "Agent Tools Engine exited: " + detail.substring(0, Math.min(4096, detail.length()));
    }

    private static <T> T join(CompletableFuture<T> future) {
        try {
            return future.join();
        } catch (CompletionException exception) {
            Throwable cause = unwrap(exception);
            if (cause instanceof RuntimeException runtime) {
                throw runtime;
            }
            throw new SDKError("Agent Tools call failed", cause);
        }
    }

    private static Throwable unwrap(Throwable error) {
        Throwable current = error;
        while ((current instanceof CompletionException || current instanceof ExecutionException)
                && current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }

    private static <T> CompletableFuture<T> failedFuture(Throwable error) {
        CompletableFuture<T> future = new CompletableFuture<>();
        future.completeExceptionally(error);
        return future;
    }

    private static FileAttribute<Set<PosixFilePermission>> privateDirectoryAttribute() {
        return PosixFilePermissions.asFileAttribute(Set.of(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
                PosixFilePermission.OWNER_EXECUTE));
    }

    private static FileAttribute<Set<PosixFilePermission>> privateFileAttribute() {
        return PosixFilePermissions.asFileAttribute(Set.of(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE));
    }

    private static void setPermissions(Path path, boolean directory) {
        try {
            var view = Files.getFileAttributeView(path,
                    java.nio.file.attribute.PosixFileAttributeView.class, LinkOption.NOFOLLOW_LINKS);
            if (view != null) {
                view.setPermissions(directory ? Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.OWNER_WRITE,
                        PosixFilePermission.OWNER_EXECUTE) : Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.OWNER_WRITE));
            }
        } catch (IOException exception) {
            throw new EngineUnavailable("Agent Tools temporary state permissions could not be secured",
                    exception);
        }
    }

    private static final class PendingCall {
        private final CompletableFuture<Map<String, Object>> future;
        private volatile ScheduledFuture<?> deadline;

        private PendingCall(CompletableFuture<Map<String, Object>> future) {
            this.future = future;
        }

        private void cancelDeadline() {
            ScheduledFuture<?> item = deadline;
            if (item != null) {
                item.cancel(false);
            }
        }
    }

    private static final class CancellableFuture<T> extends CompletableFuture<T> {
        private final Runnable cancelAction;

        private CancellableFuture(Runnable cancelAction) {
            this.cancelAction = cancelAction;
        }

        @Override
        public boolean cancel(boolean mayInterruptIfRunning) {
            boolean cancelled = super.cancel(mayInterruptIfRunning);
            if (cancelled) {
                cancelAction.run();
            }
            return cancelled;
        }
    }

    private static final class OutputLimitExceeded extends Exception {
        private static final long serialVersionUID = 1L;
    }
}
