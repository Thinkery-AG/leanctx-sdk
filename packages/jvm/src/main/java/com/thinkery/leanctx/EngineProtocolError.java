package com.thinkery.leanctx;

/** The Engine response or process boundary violated the wire contract. */
public class EngineProtocolError extends EngineError {
    public EngineProtocolError() {
        this(null);
    }

    public EngineProtocolError(String message) {
        this(message, null);
    }

    public EngineProtocolError(String message, Throwable cause) {
        super(message, cause, "engine_protocol_error",
                "fail closed and verify Engine interface, schema, and transport",
                false, false, true, false, false);
    }

    protected EngineProtocolError(String message, Throwable cause, String code,
                                  String guidance, boolean retryable,
                                  boolean degradeAllowed, boolean abortRequired,
                                  boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed,
                abortRequired, configurationFix, versionChange);
    }
}
