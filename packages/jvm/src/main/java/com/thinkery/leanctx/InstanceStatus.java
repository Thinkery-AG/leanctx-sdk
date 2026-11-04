package com.thinkery.leanctx;

/** Status values in a LeanCTX instance observation. */
public enum InstanceStatus implements HasValue {
    SUCCEEDED("succeeded"),
    DEGRADED("degraded"),
    REJECTED("rejected"),
    FAILED("failed");

    private final String value;

    InstanceStatus(String value) {
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
