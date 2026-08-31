package com.thinkery.leanctx;

/** The configured Engine identity or capability is unsupported. */
public class UnsupportedEngineError extends CompatibilityError {
    public UnsupportedEngineError() {
        this(null);
    }

    public UnsupportedEngineError(String message) {
        this(message, null);
    }

    public UnsupportedEngineError(String message, Throwable cause) {
        super(message, cause, "unsupported_engine",
                "install an Engine identity and capability supported by this SDK",
                false, false, true, false, true);
    }
}
