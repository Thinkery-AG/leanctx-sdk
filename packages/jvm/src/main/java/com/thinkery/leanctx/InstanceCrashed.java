package com.thinkery.leanctx;

/** A persistent Agent Tools LeanCTX instance process exited unexpectedly. */
@SuppressWarnings("removal")
public class InstanceCrashed extends EngineError {
    public InstanceCrashed() {
        this(null);
    }

    public InstanceCrashed(String message) {
        this(message, null);
    }

    public InstanceCrashed(String message, Throwable cause) {
        super(message, cause, "engine_crashed",
                "create a new AgentContext; mutation and execution calls are never retried",
                false, false, true, false, false);
    }
}
