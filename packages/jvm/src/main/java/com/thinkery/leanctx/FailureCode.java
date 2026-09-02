package com.thinkery.leanctx;

/** Stable failure codes emitted by Engine Interface v1. */
public enum FailureCode implements HasValue {
    POLICY_REJECTED("policy_rejected"),
    SOURCE_UNAVAILABLE("source_unavailable"),
    SOURCE_INTEGRITY_MISMATCH("source_integrity_mismatch"),
    RESOURCE_LIMIT("resource_limit"),
    UNSUPPORTED_OPERATION("unsupported_operation"),
    INTERNAL("internal");

    private final String value;

    FailureCode(String value) {
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

    public static FailureCode fromValue(String value) {
        for (FailureCode code : values()) {
            if (code.value.equals(value)) {
                return code;
            }
        }
        throw new ValidationError("invalid failure code");
    }
}
