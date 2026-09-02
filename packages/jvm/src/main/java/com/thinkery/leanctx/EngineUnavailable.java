package com.thinkery.leanctx;

/** The configured Engine could not be started or resolved. */
public class EngineUnavailable extends EngineError {
    public EngineUnavailable() {
        this(null);
    }

    public EngineUnavailable(String message) {
        this(message, null);
    }

    public EngineUnavailable(String message, Throwable cause) {
        super(message, cause, "engine_unavailable",
                "restore the configured Engine binary or use explicit bounded fail-open",
                true, true, false, true, false);
    }
}
