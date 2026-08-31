package com.thinkery.leanctx;

/** The selected source cannot currently be read by the Engine. */
public class SourceUnavailableError extends EngineExecutionError {
    public SourceUnavailableError() {
        this(null, null, null, null);
    }

    public SourceUnavailableError(String message) {
        this(message, null, null, null);
    }

    public SourceUnavailableError(String message, ContextFailure failure, ContextView view) {
        this(message, failure, view, null);
    }

    public SourceUnavailableError(String message, ContextFailure failure, ContextView view,
                                  Throwable cause) {
        super(message, failure, view, cause, "source_unavailable",
                "restore source access or select another source before retrying",
                failure != null && failure.retryableByHost(), false, true, false, false);
    }
}
