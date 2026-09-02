package com.thinkery.leanctx;

/** A supported host framework is missing or violates its adapter contract. */
public class FrameworkIntegrationError extends SDKError {
    public FrameworkIntegrationError() {
        this(null);
    }

    public FrameworkIntegrationError(String message) {
        this(message, null);
    }

    public FrameworkIntegrationError(String message, Throwable cause) {
        super(message, cause, "framework_integration_error",
                "fix the framework installation or adapter lifecycle before retrying",
                false, false, true, true, false);
    }

    protected FrameworkIntegrationError(String message, Throwable cause, String code,
                                        String guidance, boolean retryable,
                                        boolean degradeAllowed, boolean abortRequired,
                                        boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed,
                abortRequired, configurationFix, versionChange);
    }
}
