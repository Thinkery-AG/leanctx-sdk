package com.thinkery.leanctx;

/** Product lifecycle states. */
public enum SessionState implements HasValue {
    CREATED("created"),
    PLANNED("planned"),
    EXECUTING("executing"),
    COMPLETED("completed"),
    ABORTED("aborted"),
    CLOSED("closed");

    private final String value;

    SessionState(String value) {
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
