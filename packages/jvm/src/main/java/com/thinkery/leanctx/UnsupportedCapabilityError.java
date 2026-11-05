package com.thinkery.leanctx;

/** The connected LeanCTX instance did not negotiate the requested capability. */
@SuppressWarnings("removal")
public class UnsupportedCapabilityError extends EngineError {
    public UnsupportedCapabilityError() {
        this(null);
    }

    public UnsupportedCapabilityError(String message) {
        this(message, null);
    }

    public UnsupportedCapabilityError(String message, Throwable cause) {
        super(message, cause, "unsupported_capability",
                "install a compatible LeanCTX instance or choose a negotiated capability",
                false, false, true, false, true);
    }
}
