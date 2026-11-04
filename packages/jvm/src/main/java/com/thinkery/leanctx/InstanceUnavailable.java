package com.thinkery.leanctx;

/** The configured LeanCTX instance could not be started or resolved. */
@SuppressWarnings("removal")
public class InstanceUnavailable extends EngineError {
    public InstanceUnavailable() {
        this(null);
    }

    public InstanceUnavailable(String message) {
        this(message, null);
    }

    public InstanceUnavailable(String message, Throwable cause) {
        super(message, cause, "engine_unavailable",
                "restore the configured LeanCTX instance binary or use explicit bounded fail-open",
                true, true, false, true, false);
    }
}
