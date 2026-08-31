package com.thinkery.leanctx;

/** The Engine exceeded its bounded deadline. */
public class EngineTimeout extends EngineError {
    public EngineTimeout() {
        this(null);
    }

    public EngineTimeout(String message) {
        this(message, null);
    }

    public EngineTimeout(String message, Throwable cause) {
        super(message, cause, "engine_timeout",
                "retry within host policy or use explicit bounded fail-open",
                true, true, false, false, false);
    }
}
