package com.thinkery.leanctx;

/** Source freshness policy in Product intent. */
public enum Freshness implements HasValue {
    REUSE("reuse"),
    REFRESH("refresh");

    private final String value;

    Freshness(String value) {
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
