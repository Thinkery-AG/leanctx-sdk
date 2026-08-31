package com.thinkery.leanctx;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;

/** CompletableFuture facade for AgentContext, convenient from Kotlin coroutines. */
public final class AsyncAgentContext implements AutoCloseable {
    private final String projectRoot;
    private final String task;
    private final AgentPermissions permissions;
    private final ExecutionPolicy executionPolicy;
    private final String engineBinary;
    private final double timeout;
    private volatile AgentContext context;

    public AsyncAgentContext(String projectRoot) {
        this(projectRoot, "", new AgentPermissions(), new ExecutionPolicy(), "lean-ctx", 30.0);
    }

    public AsyncAgentContext(String projectRoot, String task, AgentPermissions permissions,
                             ExecutionPolicy executionPolicy, String engineBinary,
                             double timeout) {
        this.projectRoot = projectRoot;
        this.task = task;
        this.permissions = permissions;
        this.executionPolicy = executionPolicy;
        this.engineBinary = engineBinary;
        this.timeout = timeout;
    }

    public AsyncAgentContext(Path projectRoot, String task, AgentPermissions permissions,
                             ExecutionPolicy executionPolicy, Path engineBinary,
                             double timeout) {
        this(projectRoot == null ? null : projectRoot.toString(), task, permissions,
                executionPolicy, engineBinary == null ? null : engineBinary.toString(), timeout);
    }

    public static CompletableFuture<AsyncAgentContext> open(String projectRoot) {
        return new AsyncAgentContext(projectRoot).open();
    }

    public static CompletableFuture<AsyncAgentContext> open(Path projectRoot) {
        return new AsyncAgentContext(projectRoot == null ? null : projectRoot.toString()).open();
    }

    public CompletableFuture<AsyncAgentContext> open() {
        if (context != null) {
            return CompletableFuture.completedFuture(this);
        }
        return CompletableFuture.supplyAsync(() -> {
            synchronized (this) {
                if (context == null) {
                    context = new AgentContext(projectRoot, task, permissions,
                            executionPolicy, engineBinary, timeout);
                }
            }
            return this;
        });
    }

    public List<String> capabilities() {
        return current().capabilities();
    }

    public AgentMetrics metrics() {
        return current().metrics();
    }

    public CompletableFuture<ToolResult> call(String tool, Map<String, ?> arguments) {
        return current().callAsync(tool, arguments);
    }

    public CompletableFuture<ToolResult> read(String path, ReadMode mode, boolean fresh) {
        return current().readAsync(path, mode, fresh);
    }

    public CompletableFuture<ToolResult> search(String pattern, String path, int maxResults,
                                                String include) {
        return CompletableFuture.supplyAsync(() -> current().search(pattern, path, maxResults,
                include));
    }

    public CompletableFuture<ToolResult> glob(String pattern, String path, int maxResults) {
        return CompletableFuture.supplyAsync(() -> current().glob(pattern, path, maxResults));
    }

    public CompletableFuture<ToolResult> tree(String path, int depth, boolean showHidden) {
        return CompletableFuture.supplyAsync(() -> current().tree(path, depth, showHidden));
    }

    public CompletableFuture<ToolResult> compose(String task, String path) {
        return CompletableFuture.supplyAsync(() -> current().compose(task, path));
    }

    public CompletableFuture<ToolResult> symbol(String name) {
        return CompletableFuture.supplyAsync(() -> current().symbol(name));
    }

    public CompletableFuture<ToolResult> patch(Map<String, ?> arguments) {
        return CompletableFuture.supplyAsync(() -> current().patch(arguments));
    }

    public CompletableFuture<ToolResult> run(List<String> argv, String cwd,
                                             Map<String, String> env, double timeoutSeconds) {
        return current().runAsync(argv, cwd, env, timeoutSeconds);
    }

    public CompletableFuture<AsyncAgentContext> reconnect() {
        return CompletableFuture.supplyAsync(() -> {
            AgentContext old = current();
            old.close();
            synchronized (this) {
                context = new AgentContext(projectRoot, task, permissions,
                        executionPolicy, engineBinary, timeout);
            }
            return this;
        });
    }

    public CompletableFuture<Void> cancel() {
        return CompletableFuture.runAsync(() -> {
            AgentContext current = context;
            if (current != null) {
                current.cancel();
            }
        });
    }

    public CompletableFuture<Void> closeAsync() {
        return CompletableFuture.runAsync(this::close);
    }

    @Override
    public void close() {
        AgentContext current = context;
        if (current != null) {
            current.close();
        }
    }

    private AgentContext current() {
        AgentContext current = context;
        if (current == null) {
            throw new EngineUnavailable("AsyncAgentContext is not open");
        }
        return current;
    }
}
