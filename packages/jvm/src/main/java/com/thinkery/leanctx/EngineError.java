package com.thinkery.leanctx;

/** Base class for failures at the public Engine process boundary. */
public class EngineError extends SDKError {
    public EngineError() {
        this(null);
    }

    public EngineError(String message) {
        this(message, null);
    }

    public EngineError(String message, Throwable cause) {
        super(message, cause, "engine_error",
                "preserve Engine evidence and classify the concrete error",
                false, false, true, false, false);
    }

    protected EngineError(String message, Throwable cause, String code, String guidance,
                          boolean retryable, boolean degradeAllowed, boolean abortRequired,
                          boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed, abortRequired,
                configurationFix, versionChange);
    }
}
