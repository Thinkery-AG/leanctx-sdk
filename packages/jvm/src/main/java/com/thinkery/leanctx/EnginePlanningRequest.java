package com.thinkery.leanctx;

import java.util.LinkedHashMap;
import java.util.Map;

/** Canonical non-executing Engine source-planning request. */
public final class EnginePlanningRequest {
    public static final int MAX_QUERY_BYTES = 16 * 1024;
    public static final long MAX_BUDGET_TOKENS = 1_048_576L;
    public static final long MAX_CANDIDATES = 256L;

    private final String taskId;
    private final String query;
    private final long budgetTokens;
    private final long maxCandidates;

    public EnginePlanningRequest(String taskId, String query, long budgetTokens) {
        this(taskId, query, budgetTokens, 64L);
    }

    public EnginePlanningRequest(String taskId, String query, long budgetTokens, long maxCandidates) {
        this.taskId = EnterprisePlanningProtocol.inputText(taskId, "task_id", 256, true, true);
        this.query = EnterprisePlanningProtocol.inputText(
                query, "query", MAX_QUERY_BYTES, false, true);
        if (EnterprisePlanningProtocol.isBlankLikePython(query)) {
            throw new ValidationError("query must not be blank");
        }
        if (budgetTokens < 1 || budgetTokens > MAX_BUDGET_TOKENS) {
            throw new ValidationError("budget_tokens is outside its protocol bounds");
        }
        if (maxCandidates < 1 || maxCandidates > MAX_CANDIDATES) {
            throw new ValidationError("max_candidates is outside its protocol bounds");
        }
        this.budgetTokens = budgetTokens;
        this.maxCandidates = maxCandidates;
    }

    public String taskId() {
        return taskId;
    }

    public String getTaskId() {
        return taskId;
    }

    public String query() {
        return query;
    }

    public String getQuery() {
        return query;
    }

    public long budgetTokens() {
        return budgetTokens;
    }

    public long getBudgetTokens() {
        return budgetTokens;
    }

    public long maxCandidates() {
        return maxCandidates;
    }

    public long getMaxCandidates() {
        return maxCandidates;
    }

    /** Return the detached canonical v1 Engine planning projection. */
    public Map<String, Object> toDict() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", LeanCtx.SCHEMA_VERSION);
        result.put("transport_version", LeanCtx.TRANSPORT_VERSION);
        result.put("engine_interface_version", LeanCtx.ENGINE_INTERFACE_VERSION);
        result.put("task_id", taskId);
        result.put("query", query);
        result.put("budget_tokens", budgetTokens);
        result.put("max_candidates", maxCandidates);
        if (Json.canonicalBytes(result).length > 64 * 1024) {
            throw new ValidationError("Engine context-plan request exceeds its byte bound");
        }
        return Json.immutableMap(result);
    }
}
