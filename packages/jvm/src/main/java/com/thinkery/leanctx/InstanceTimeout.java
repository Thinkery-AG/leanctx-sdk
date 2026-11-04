package com.thinkery.leanctx;

/** The LeanCTX instance exceeded its bounded deadline. */
@SuppressWarnings("removal")
public class InstanceTimeout extends EngineError {
    public InstanceTimeout() {
        this(null);
    }

    public InstanceTimeout(String message) {
        this(message, null);
    }

    public InstanceTimeout(String message, Throwable cause) {
        super(message, cause, "engine_timeout",
                "retry within host policy or use explicit bounded fail-open",
                true, true, false, false, false);
    }
}
