package com.thinkery.leanctx;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/** Host-owned Product lifecycle with explicit fail-open and receipt semantics. */
public final class ContextSession {
    private final String task;
    private final String projectRoot;
    private final String sessionId;
    private final String taskId;
    private final boolean failOpen;
    private final EngineClient engine;
    private SessionState state = SessionState.CREATED;
    private ContextPlan plan;
    private ContextView view;
    private ContextReceipt receipt;
    private boolean prepared;
    private final List<String> degradations = new ArrayList<>();

    public ContextSession(String task) {
        this(task, null, null, null, false, null);
    }

    public ContextSession(String task, EngineClient engine) {
        this(task, null, null, null, false, engine);
    }

    public ContextSession(String task, String projectRoot, boolean failOpen,
                          EngineClient engine) {
        this(task, projectRoot, null, null, failOpen, engine);
    }

    public ContextSession(String task, String projectRoot, String sessionId, String taskId,
                          boolean failOpen, EngineClient engine) {
        this.task = Protocol.text(task, "task", Protocol.MAX_TASK_BYTES, false);
        this.projectRoot = projectRoot;
        this.sessionId = sessionId == null ? runtimeId("session")
                : Protocol.text(sessionId, "session_id", Protocol.MAX_REF_BYTES);
        this.taskId = taskId == null ? runtimeId("task")
                : Protocol.text(taskId, "task_id", Protocol.MAX_REF_BYTES);
        this.failOpen = failOpen;
        this.engine = engine == null ? new SubprocessEngineClient() : engine;
    }

    public static ContextSession open(String task) {
        return new ContextSession(task);
    }

    public synchronized SessionState state() {
        return state;
    }

    public synchronized SessionState getState() {
        return state;
    }

    public String task() {
        return task;
    }

    public String getTask() {
        return task;
    }

    public String projectRoot() {
        return projectRoot;
    }

    public String getProjectRoot() {
        return projectRoot;
    }

    public String project_root() {
        return projectRoot;
    }

    public String sessionId() {
        return sessionId;
    }

    public String getSessionId() {
        return sessionId;
    }

    public String session_id() {
        return sessionId;
    }

    public String taskId() {
        return taskId;
    }

    public String getTaskId() {
        return taskId;
    }

    public String task_id() {
        return taskId;
    }

    public boolean failOpen() {
        return failOpen;
    }

    public boolean isFailOpen() {
        return failOpen;
    }

    public EngineClient engine() {
        return engine;
    }

    public EngineClient getEngine() {
        return engine;
    }

    public synchronized ContextPlan currentPlan() {
        return plan;
    }

    public synchronized ContextPlan getCurrentPlan() {
        return plan;
    }

    public synchronized ContextPlan current_plan() {
        return plan;
    }

    public synchronized ContextView view() {
        return view;
    }

    public synchronized ContextView getView() {
        return view;
    }

    public synchronized ContextReceipt receipt() {
        return receipt;
    }

    public synchronized ContextReceipt getReceipt() {
        return receipt;
    }

    public synchronized List<String> degradations() {
        return Collections.unmodifiableList(new ArrayList<>(degradations));
    }

    public synchronized ContextPlan plan(ContextSource source) {
        return plan(source, "aggressive", Freshness.REUSE.value());
    }

    public synchronized ContextPlan plan(ContextSource source, String mode, String freshness) {
        ensureNotTerminal();
        ContextPlan candidate = new ContextPlan(sessionId, taskId, task, source, mode, freshness);
        if (plan != null) {
            if (!plan.planId().equals(candidate.planId())) {
                throw new SessionStateError("a session cannot replace its Product intent");
            }
            return plan;
        }
        plan = candidate;
        state = SessionState.PLANNED;
        return plan;
    }

    public ContextPlan planFor(ContextSource source) {
        return plan(source);
    }

    public ContextPlan planIntent(ContextSource source) {
        return plan(source);
    }

    public synchronized ContextView prepare(ContextSource source) {
        return prepare(source, "aggressive", Freshness.REUSE.value());
    }

    public synchronized ContextView prepare(ContextSource source, String mode, String freshness) {
        if (state == SessionState.COMPLETED || state == SessionState.ABORTED
                || state == SessionState.CLOSED) {
            throw new SessionStateError("prepare is not legal after terminal completion");
        }
        if (prepared) {
            return view;
        }
        if (plan == null) {
            if (source == null) {
                throw new SessionStateError("prepare requires a source before planning");
            }
            plan(source, mode, freshness);
        } else if (source != null) {
            plan(source, mode, freshness);
        }
        if (plan == null) {
            throw new SessionStateError("prepare could not establish a plan");
        }
        state = SessionState.EXECUTING;
        try {
            view = engine.contextView(plan);
            prepared = true;
            if (view != null && EngineStatus.DEGRADED.value().equals(view.status())) {
                addDegradation("engine:degraded");
            }
            return view;
        } catch (EngineUnavailable | EngineTimeout exception) {
            if (failOpen) {
                addDegradation("engine:" + exception.code());
                prepared = true;
                view = null;
                return null;
            }
            abortEngineFailure(exception);
            throw exception;
        } catch (RuntimeException exception) {
            abortEngineFailure(exception);
            throw exception;
        }
    }

    public ContextView prepare() {
        return prepare(null);
    }

    public synchronized ContextReceipt complete() {
        return complete(null, HostOutcome.UNKNOWN.value(), null);
    }

    public synchronized ContextReceipt complete(Object hostResult, HostOutcome outcome,
                                                Map<String, ?> usage) {
        String checkedOutcome = outcome == null ? HostOutcome.UNKNOWN.value() : outcome.value();
        return complete(hostResult, checkedOutcome, usage);
    }

    public synchronized ContextReceipt complete(Object hostResult, String outcome,
                                                Map<String, ?> usage) {
        if (state == SessionState.COMPLETED) {
            if (receipt == null || !sameCompletion(receipt, outcome, usage)) {
                throw new SessionStateError("conflicting repeated complete");
            }
            return receipt;
        }
        if (state == SessionState.ABORTED || state == SessionState.CLOSED) {
            throw new SessionStateError("complete is not legal after abort/close");
        }
        if (state != SessionState.EXECUTING) {
            throw new SessionStateError("complete requires an executing session");
        }
        if (!isNonAbortedOutcome(outcome)) {
            throw new ValidationError("complete outcome must be an explicit non-aborted host outcome");
        }
        receipt = makeReceipt(outcome, hostResult, usage, null, null);
        state = SessionState.COMPLETED;
        return receipt;
    }

    public synchronized ContextReceipt abort(Throwable error) {
        if (error == null) {
            throw new ValidationError("abort requires a Throwable");
        }
        if (state == SessionState.ABORTED) {
            if (receipt == null) {
                throw new SessionStateError("aborted session has no receipt");
            }
            return receipt;
        }
        if (state == SessionState.CLOSED) {
            if (receipt != null && HostOutcome.ABORTED.value().equals(receipt.outcome())) {
                return receipt;
            }
            throw new SessionStateError("closed session has no abort receipt");
        }
        if (state == SessionState.COMPLETED) {
            throw new SessionStateError("cannot abort a completed session");
        }
        String typeName = error.getClass().getName();
        receipt = makeReceipt(HostOutcome.ABORTED.value(), null, null, typeName, error);
        state = SessionState.ABORTED;
        return receipt;
    }

    public synchronized RecoveredSource recover() {
        return recover(view);
    }

    public synchronized RecoveredSource recover(ContextView selected) {
        if (state != SessionState.EXECUTING && state != SessionState.COMPLETED
                && state != SessionState.ABORTED) {
            throw new SessionStateError("recover requires an executing or terminal session");
        }
        if (selected == null || plan == null) {
            throw new RecoveryUnavailableError("no validated view is available for recovery");
        }
        if (selected != view) {
            if (view == null || !Objects.equals(selected.recoveryBinding(), view.recoveryBinding())) {
                throw new RecoveryUnavailableError("recovery view is not bound to this session");
            }
        }
        if (selected.recoveryRef() == null) {
            throw new RecoveryUnavailableError("view has no recovery binding");
        }
        RecoveredSource result = engine.recover(plan.source().projectRoot(),
                plan.source().relativePath(), selected.recoveryRef(), selected.sourceRef(),
                selected.sourceDigest());
        if (result == null || !Objects.equals(result.recoveryRef(), selected.recoveryRef())
                || !Objects.equals(result.sourceRef(), selected.sourceRef())
                || !Objects.equals(result.sourceDigest(), selected.sourceDigest())) {
            throw new ArtifactIntegrityError("recovery binding differs from the validated view");
        }
        return result;
    }

    public synchronized void close() {
        if (state == SessionState.CLOSED) {
            return;
        }
        if (state != SessionState.COMPLETED && state != SessionState.ABORTED) {
            throw new SessionStateError("close requires a terminal receipt");
        }
        state = SessionState.CLOSED;
    }

    private void ensureNotTerminal() {
        if (state == SessionState.COMPLETED || state == SessionState.ABORTED
                || state == SessionState.CLOSED) {
            throw new SessionStateError("planning is not legal after terminal completion");
        }
    }

    private void addDegradation(String value) {
        if (!degradations.contains(value)) {
            degradations.add(value);
        }
    }

    private void abortEngineFailure(Throwable error) {
        if (error instanceof EngineRejected rejected && rejected.view() != null) {
            view = rejected.view();
        } else if (error instanceof EngineExecutionError execution && execution.view() != null) {
            view = execution.view();
        }
        String code = error instanceof EngineError engineError
                ? engineError.code() : "engine_error";
        addDegradation("engine:" + code);
        receipt = makeReceipt(HostOutcome.ABORTED.value(), null, null, null, null);
        state = SessionState.ABORTED;
    }

    private ContextReceipt makeReceipt(String outcome, Object hostResult,
                                       Map<String, ?> usage, String exceptionType,
                                       Throwable exception) {
        String integrity = view == null ? Integrity.UNSEALED.value()
                : view.integrityStatus().value();
        return new ContextReceipt(sessionId, taskId, plan == null ? null : plan.planId(), view,
                outcome, integrity, degradations, usage, exceptionType, hostResult, exception);
    }

    private static boolean sameCompletion(ContextReceipt current, String outcome,
                                          Map<String, ?> usage) {
        try {
            return Objects.equals(current.outcome(), outcome)
                    && Objects.equals(Json.canonical(current.usage()),
                    Json.canonical(usage));
        } catch (RuntimeException exception) {
            return false;
        }
    }

    private static boolean isNonAbortedOutcome(String value) {
        if (value == null || HostOutcome.ABORTED.value().equals(value)) {
            return false;
        }
        for (HostOutcome outcome : HostOutcome.values()) {
            if (outcome.value().equals(value)) {
                return true;
            }
        }
        return false;
    }

    private static String runtimeId(String prefix) {
        return prefix + "-" + UUID.randomUUID().toString().replace("-", "");
    }
}
