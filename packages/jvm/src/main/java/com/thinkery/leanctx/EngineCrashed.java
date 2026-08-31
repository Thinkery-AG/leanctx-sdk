package com.thinkery.leanctx;

/** A persistent Agent Tools Engine process exited unexpectedly. */
public class EngineCrashed extends EngineError {
    public EngineCrashed() {
        this(null);
    }

    public EngineCrashed(String message) {
        this(message, null);
    }

    public EngineCrashed(String message, Throwable cause) {
        super(message, cause, "engine_crashed",
                "create a new AgentContext; mutation and execution calls are never retried",
                false, false, true, false, false);
    }
}
