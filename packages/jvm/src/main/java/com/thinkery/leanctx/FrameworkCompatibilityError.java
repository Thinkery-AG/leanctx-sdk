package com.thinkery.leanctx;

/** The installed host framework is outside the certified compatibility matrix. */
public class FrameworkCompatibilityError extends FrameworkIntegrationError {
    public FrameworkCompatibilityError() {
        this(null);
    }

    public FrameworkCompatibilityError(String message) {
        this(message, null);
    }

    public FrameworkCompatibilityError(String message, Throwable cause) {
        super(message, cause, "framework_compatibility_error",
                "install the exact certified framework version",
                false, false, true, true, true);
    }
}
