package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstancePlanningRequest}, removed in SDK 2.0.
 *
 * @deprecated since 1.3.0, use {@link InstancePlanningRequest}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public final class EnginePlanningRequest extends InstancePlanningRequest {
    public EnginePlanningRequest(String taskId, String query, long budgetTokens) {
        super(taskId, query, budgetTokens);
    }

    public EnginePlanningRequest(String taskId, String query, long budgetTokens, long maxCandidates) {
        super(taskId, query, budgetTokens, maxCandidates);
    }
}
