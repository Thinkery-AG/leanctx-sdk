package com.thinkery.leanctx;

/** Host-owned completion outcomes. */
public enum HostOutcome implements HasValue {
    UNKNOWN("unknown"),
    ACCEPTED("accepted"),
    REJECTED("rejected"),
    COMPLETED("completed"),
    FAILED("failed"),
    ABORTED("aborted");

    private final String value;

    HostOutcome(String value) {
        this.value = value;
    }

    @Override
    public String value() {
        return value;
    }

    @Override
    public String toString() {
        return value;
    }
}
