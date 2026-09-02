package com.thinkery.leanctx;

/** Stable read shaping modes understood by Agent Tools 1.1. */
public enum ReadMode implements HasValue {
    AUTO("auto"),
    FULL("full"),
    RAW("raw"),
    SIGNATURES("signatures"),
    MAP("map"),
    DIFF("diff"),
    REFERENCE("reference"),
    TASK("task"),
    ANCHORED("anchored");

    private final String value;

    ReadMode(String value) {
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
