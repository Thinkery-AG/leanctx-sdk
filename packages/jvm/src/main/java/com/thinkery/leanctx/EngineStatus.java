package com.thinkery.leanctx;

/**
 * Deprecated copy of {@link InstanceStatus} (Java enums cannot alias), removed in
 * SDK 2.0. The wire values are identical.
 *
 * @deprecated since 1.3.0, use {@link InstanceStatus}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public enum EngineStatus implements HasValue {
    SUCCEEDED(InstanceStatus.SUCCEEDED),
    DEGRADED(InstanceStatus.DEGRADED),
    REJECTED(InstanceStatus.REJECTED),
    FAILED(InstanceStatus.FAILED);

    private final InstanceStatus status;

    EngineStatus(InstanceStatus status) {
        this.status = status;
    }

    /** The equivalent primary status value. */
    public InstanceStatus toInstanceStatus() {
        return status;
    }

    @Override
    public String value() {
        return status.value();
    }

    @Override
    public String toString() {
        return status.value();
    }
}
