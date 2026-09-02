package com.thinkery.leanctx;

/** Whether Engine evidence is fully bound and verified. */
public enum Integrity implements HasValue {
    SEALED("sealed"),
    UNSEALED("unsealed");

    private final String value;

    Integrity(String value) {
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
