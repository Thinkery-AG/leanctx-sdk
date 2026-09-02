package com.thinkery.leanctx;

/** The connected Engine did not negotiate the requested capability. */
public class UnsupportedCapabilityError extends EngineError {
    public UnsupportedCapabilityError() {
        this(null);
    }

    public UnsupportedCapabilityError(String message) {
        this(message, null);
    }

    public UnsupportedCapabilityError(String message, Throwable cause) {
        super(message, cause, "unsupported_capability",
                "install a compatible Engine or choose a negotiated capability",
                false, false, true, false, true);
    }
}
