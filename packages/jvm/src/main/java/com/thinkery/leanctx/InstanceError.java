package com.thinkery.leanctx;

/** Base class for failures at the public LeanCTX instance process boundary. */
public class InstanceError extends SDKError {
    public InstanceError() {
        this(null);
    }

    public InstanceError(String message) {
        this(message, null);
    }

    public InstanceError(String message, Throwable cause) {
        super(message, cause, "engine_error",
                "preserve LeanCTX instance evidence and classify the concrete error",
                false, false, true, false, false);
    }

    protected InstanceError(String message, Throwable cause, String code, String guidance,
                          boolean retryable, boolean degradeAllowed, boolean abortRequired,
                          boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed, abortRequired,
                configurationFix, versionChange);
    }
}
