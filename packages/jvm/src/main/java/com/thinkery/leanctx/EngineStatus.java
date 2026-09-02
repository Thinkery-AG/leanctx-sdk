package com.thinkery.leanctx;

/** Status values in an Engine observation. */
public enum EngineStatus implements HasValue {
    SUCCEEDED("succeeded"),
    DEGRADED("degraded"),
    REJECTED("rejected"),
    FAILED("failed");

    private final String value;

    EngineStatus(String value) {
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
