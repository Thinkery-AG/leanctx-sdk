package com.thinkery.leanctx;

/** The configured LeanCTX instance identity or capability is unsupported. */
public class UnsupportedInstanceError extends CompatibilityError {
    public UnsupportedInstanceError() {
        this(null);
    }

    public UnsupportedInstanceError(String message) {
        this(message, null);
    }

    public UnsupportedInstanceError(String message, Throwable cause) {
        super(message, cause, "unsupported_engine",
                "install a LeanCTX instance identity and capability supported by this SDK",
                false, false, true, false, true);
    }
}
