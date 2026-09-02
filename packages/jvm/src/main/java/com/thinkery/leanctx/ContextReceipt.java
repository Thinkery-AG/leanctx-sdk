package com.thinkery.leanctx;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Truthful host/evaluator receipt projection. */
public final class ContextReceipt {
    private final String sessionId;
    private final String taskId;
    private final String planId;
    private final ContextView view;
    private final String outcome;
    private final String integrityStatus;
    private final List<String> degradations;
    private final Map<String, Object> usage;
    private final String hostExceptionType;
    private final Object hostResult;
    private final Throwable hostException;
    private final int schemaVersion;

    public ContextReceipt(String sessionId, String taskId, String planId, ContextView view,
                          String outcome, String integrityStatus) {
        this(sessionId, taskId, planId, view, outcome, integrityStatus,
                List.of(), null, null, null, null);
    }

    public ContextReceipt(String sessionId, String taskId, String planId, ContextView view,
                          String outcome, String integrityStatus,
                          List<String> degradations, Map<String, ?> usage,
                          String hostExceptionType, Object hostResult,
                          Throwable hostException) {
        this(sessionId, taskId, planId, view, outcome, integrityStatus, degradations,
                usage, hostExceptionType, hostResult, hostException,
                LeanCtx.SCHEMA_VERSION);
    }

    public ContextReceipt(String sessionId, String taskId, String planId, ContextView view,
                          String outcome, String integrityStatus,
                          List<String> degradations, Map<String, ?> usage,
                          String hostExceptionType, Object hostResult,
                          Throwable hostException, int schemaVersion) {
        this.sessionId = Protocol.text(sessionId, "session_id", 512);
        this.taskId = Protocol.text(taskId, "task_id", 512);
        this.planId = planId == null ? null : Protocol.planRef(planId);
        if (view != null && !(view instanceof ContextView)) {
            throw new ValidationError("receipt view must be ContextView");
        }
        if (!isHostOutcome(outcome)) {
            throw new ValidationError("invalid host outcome");
        }
        if (!isIntegrity(integrityStatus)) {
            throw new ValidationError("invalid integrity status");
        }
        if (degradations == null || degradations.stream().anyMatch(item -> item == null || item.isEmpty())) {
            throw new ValidationError("degradations must be non-empty strings");
        }
        List<String> checkedDegradations = new ArrayList<>(degradations);
        String checkedExceptionType = hostExceptionType;
        if (checkedExceptionType != null) {
            Protocol.text(checkedExceptionType, "host_exception_type", 512);
            if (checkedExceptionType.indexOf(':') >= 0 || checkedExceptionType.indexOf('\n') >= 0) {
                throw new ValidationError("host_exception_type must be a safe type name");
            }
        }
        if (hostException != null) {
            if (!HostOutcome.ABORTED.value().equals(outcome)) {
                throw new ValidationError("host_exception requires an aborted outcome");
            }
            String expectedType = hostException.getClass().getName();
            if (checkedExceptionType == null || !checkedExceptionType.equals(expectedType)) {
                throw new ValidationError("host_exception_type does not match host_exception");
            }
        }
        if (schemaVersion != LeanCtx.SCHEMA_VERSION) {
            throw new ValidationError("receipt schema_version must be 1");
        }
        Map<String, Object> checkedUsage = null;
        if (usage != null) {
            try {
                Json.canonical(usage);
            } catch (ValidationError exception) {
                throw new ValidationError("usage must be deterministic JSON data", exception);
            }
            checkedUsage = Json.immutableMap(usage);
        }
        if (Integrity.SEALED.value().equals(integrityStatus)
                && (view == null || !view.verify())) {
            throw new ValidationError("sealed receipt requires verified Engine evidence");
        }
        this.view = view;
        this.outcome = outcome;
        this.integrityStatus = integrityStatus;
        this.degradations = Collections.unmodifiableList(checkedDegradations);
        this.usage = checkedUsage;
        this.hostExceptionType = checkedExceptionType;
        this.hostResult = hostResult;
        this.hostException = hostException;
        this.schemaVersion = schemaVersion;
    }

    private static boolean isHostOutcome(String value) {
        if (value == null) {
            return false;
        }
        for (HostOutcome outcome : HostOutcome.values()) {
            if (outcome.value().equals(value)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isIntegrity(String value) {
        if (value == null) {
            return false;
        }
        for (Integrity integrity : Integrity.values()) {
            if (integrity.value().equals(value)) {
                return true;
            }
        }
        return false;
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

    public String planId() {
        return planId;
    }

    public String getPlanId() {
        return planId;
    }

    public String plan_id() {
        return planId;
    }

    public ContextView view() {
        return view;
    }

    public ContextView getView() {
        return view;
    }

    public String outcome() {
        return outcome;
    }

    public String getOutcome() {
        return outcome;
    }

    public String integrityStatus() {
        return integrityStatus;
    }

    public String getIntegrityStatus() {
        return integrityStatus;
    }

    public String integrity_status() {
        return integrityStatus;
    }

    public List<String> degradations() {
        return degradations;
    }

    public List<String> getDegradations() {
        return degradations;
    }

    public Map<String, Object> usage() {
        return usage;
    }

    public Map<String, Object> getUsage() {
        return usage;
    }

    public String hostExceptionType() {
        return hostExceptionType;
    }

    public String getHostExceptionType() {
        return hostExceptionType;
    }

    public String host_exception_type() {
        return hostExceptionType;
    }

    public Object hostResult() {
        return hostResult;
    }

    public Object getHostResult() {
        return hostResult;
    }

    public Throwable hostException() {
        return hostException;
    }

    public Throwable getHostException() {
        return hostException;
    }

    public Throwable exception() {
        return hostException;
    }

    public int schemaVersion() {
        return schemaVersion;
    }

    public boolean sealed() {
        return Integrity.SEALED.value().equals(integrityStatus);
    }

    public boolean isSealed() {
        return sealed();
    }

    public String status() {
        return view == null ? null : view.status();
    }

    public ContextSource source() {
        return view == null ? null : view.source();
    }

    public Map<String, Object> invocation() {
        return view == null ? null : view.invocation();
    }

    public Map<String, Object> observation() {
        return view == null ? null : view.observation();
    }

    public ContextReceiptLink receiptLink() {
        return view == null ? null : view.receiptLink();
    }

    public String recoveryRef() {
        return view == null ? null : view.recoveryRef();
    }

    public String outputDigest() {
        return view == null ? null : view.outputDigest();
    }

    public boolean verify() {
        return sealed() && view != null && view.verify();
    }

    public void requireVerified() {
        if (!verify()) {
            throw new ArtifactIntegrityError("receipt evidence is not sealed");
        }
    }

    public void require_verified() {
        requireVerified();
    }

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", schemaVersion);
        result.put("session_id", sessionId);
        result.put("task_id", taskId);
        result.put("plan_id", planId);
        result.put("outcome", outcome);
        result.put("integrity_status", integrityStatus);
        result.put("degradations", degradations);
        result.put("usage", usage);
        result.put("host_exception_type", hostExceptionType);
        result.put("status", status());
        result.put("source", source() == null ? null : source().toMap());
        result.put("invocation", invocation());
        result.put("observation", observation());
        result.put("receipt_link", receiptLink() == null ? null : receiptLink().toMap());
        result.put("recovery_ref", recoveryRef());
        result.put("output_digest", outputDigest());
        return Json.immutableMap(result);
    }

    public Map<String, Object> toDict() {
        return toMap();
    }

    public Map<String, Object> to_dict() {
        return toMap();
    }
}
