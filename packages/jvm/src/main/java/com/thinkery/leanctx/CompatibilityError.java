package com.thinkery.leanctx;

/** SDK and dependency expose incompatible versioned contracts. */
public class CompatibilityError extends EngineProtocolError {
    public CompatibilityError() {
        this(null);
    }

    public CompatibilityError(String message) {
        this(message, null);
    }

    public CompatibilityError(String message, Throwable cause) {
        super(message, cause, "compatibility_error",
                "install a supported version from the compatibility matrix",
                false, false, true, false, true);
    }

    protected CompatibilityError(String message, Throwable cause, String code,
                                 String guidance, boolean retryable,
                                 boolean degradeAllowed, boolean abortRequired,
                                 boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed,
                abortRequired, configurationFix, versionChange);
    }
}
