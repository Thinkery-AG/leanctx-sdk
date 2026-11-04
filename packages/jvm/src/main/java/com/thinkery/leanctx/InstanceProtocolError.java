package com.thinkery.leanctx;

/** The LeanCTX instance response or process boundary violated the wire contract. */
@SuppressWarnings("removal")
public class InstanceProtocolError extends EngineError {
    public InstanceProtocolError() {
        this(null);
    }

    public InstanceProtocolError(String message) {
        this(message, null);
    }

    public InstanceProtocolError(String message, Throwable cause) {
        super(message, cause, "engine_protocol_error",
                "fail closed and verify LeanCTX instance interface, schema, and transport",
                false, false, true, false, false);
    }

    protected InstanceProtocolError(String message, Throwable cause, String code,
                                  String guidance, boolean retryable,
                                  boolean degradeAllowed, boolean abortRequired,
                                  boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed,
                abortRequired, configurationFix, versionChange);
    }
}
