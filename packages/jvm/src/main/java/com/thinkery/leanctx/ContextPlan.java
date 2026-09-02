package com.thinkery.leanctx;

import java.util.LinkedHashMap;
import java.util.Map;

/** Deterministic preparation intent for one source and task. */
public final class ContextPlan {
    private final String sessionId;
    private final String taskId;
    private final String task;
    private final ContextSource source;
    private final String mode;
    private final String freshness;
    private final String planId;

    public ContextPlan(String sessionId, String taskId, String task, ContextSource source) {
        this(sessionId, taskId, task, source, "aggressive", Freshness.REUSE.value());
    }

    public ContextPlan(String sessionId, String taskId, String task, ContextSource source,
                       String mode, String freshness) {
        this.sessionId = Protocol.text(sessionId, "session_id", Protocol.MAX_REF_BYTES);
        this.taskId = Protocol.text(taskId, "task_id", Protocol.MAX_REF_BYTES);
        this.task = Protocol.text(task, "task", Protocol.MAX_TASK_BYTES, false);
        if (source == null) {
            throw new ValidationError("source must be ContextSource");
        }
        this.source = source;
        this.mode = mode == null ? "aggressive" : mode;
        this.freshness = freshness == null ? Freshness.REUSE.value() : freshness;
        if (!"aggressive".equals(this.mode)) {
            throw new ValidationError("mode must be aggressive in Engine Interface v1");
        }
        if (!Freshness.REUSE.value().equals(this.freshness)
                && !Freshness.REFRESH.value().equals(this.freshness)) {
            throw new ValidationError("freshness must be reuse or refresh");
        }
        this.planId = Protocol.canonicalPlanId(toIntent());
    }

    public ContextPlan(String sessionId, String taskId, String task, ContextSource source,
                       String mode, Freshness freshness) {
        this(sessionId, taskId, task, source, mode,
                freshness == null ? null : freshness.value());
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

    public String task() {
        return task;
    }

    public String getTask() {
        return task;
    }

    public ContextSource source() {
        return source;
    }

    public ContextSource getSource() {
        return source;
    }

    public String mode() {
        return mode;
    }

    public String getMode() {
        return mode;
    }

    public String freshness() {
        return freshness;
    }

    public String getFreshness() {
        return freshness;
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

    public Map<String, Object> toIntent() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("intent_version", 1);
        result.put("session_id", sessionId);
        result.put("task_id", taskId);
        result.put("task", task);
        result.put("source", source.descriptor());
        result.put("mode", mode);
        result.put("freshness", freshness);
        return Json.immutableMap(result);
    }

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>(toIntent());
        result.put("plan_id", planId);
        return Json.immutableMap(result);
    }

    public Map<String, Object> toDict() {
        return toMap();
    }

    public Map<String, Object> to_intent() {
        return toIntent();
    }

    public Map<String, Object> to_dict() {
        return toMap();
    }
}
