package com.thinkery.leanctx;

/** SDK or adapter configuration must be corrected before retrying. */
public class ConfigurationError extends SDKError {
    public ConfigurationError() {
        this(null);
    }

    public ConfigurationError(String message) {
        this(message, null);
    }

    public ConfigurationError(String message, Throwable cause) {
        super(message, cause, "configuration_error", "fix SDK configuration before retrying",
                false, false, true, true, false);
    }
}
