package com.thinkery.leanctx;

/** Immutable cumulative Agent Tools token metrics. */
public final class AgentMetrics {
    private final long toolCalls;
    private final long originalTokens;
    private final long outputTokens;
    private final long savedTokens;

    public AgentMetrics() {
        this(0, 0, 0, 0);
    }

    public AgentMetrics(long toolCalls, long originalTokens, long outputTokens,
                        long savedTokens) {
        this.toolCalls = nonNegative(toolCalls, "tool_calls");
        this.originalTokens = nonNegative(originalTokens, "original_tokens");
        this.outputTokens = nonNegative(outputTokens, "output_tokens");
        this.savedTokens = nonNegative(savedTokens, "saved_tokens");
        if (outputTokens + savedTokens != originalTokens) {
            throw new ValidationError("saved_tokens must equal original_tokens - output_tokens");
        }
    }

    public long toolCalls() {
        return toolCalls;
    }

    public long getToolCalls() {
        return toolCalls;
    }

    public long tool_calls() {
        return toolCalls;
    }

    public long originalTokens() {
        return originalTokens;
    }

    public long getOriginalTokens() {
        return originalTokens;
    }

    public long original_tokens() {
        return originalTokens;
    }

    public long outputTokens() {
        return outputTokens;
    }

    public long getOutputTokens() {
        return outputTokens;
    }

    public long output_tokens() {
        return outputTokens;
    }

    public long savedTokens() {
        return savedTokens;
    }

    public long getSavedTokens() {
        return savedTokens;
    }

    public long saved_tokens() {
        return savedTokens;
    }

    public double savedRatio() {
        return originalTokens == 0 ? 0.0 : (double) savedTokens / originalTokens;
    }

    public double getSavedRatio() {
        return savedRatio();
    }

    public double saved_ratio() {
        return savedRatio();
    }

    private static long nonNegative(long value, String field) {
        if (value < 0) {
            throw new ValidationError(field + " must be non-negative");
        }
        return value;
    }
}
