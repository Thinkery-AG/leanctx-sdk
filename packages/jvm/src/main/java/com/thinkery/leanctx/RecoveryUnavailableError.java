package com.thinkery.leanctx;

/** Exact recovery cannot be completed from the available binding. */
public class RecoveryUnavailableError extends EngineExecutionError {
    public RecoveryUnavailableError() {
        this(null);
    }

    public RecoveryUnavailableError(String message) {
        super(message, null, null, null, "recovery_unavailable",
                "abort and restore the exact source and recovery binding",
                false, false, true, false, false);
    }
}
