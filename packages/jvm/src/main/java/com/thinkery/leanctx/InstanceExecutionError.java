package com.thinkery.leanctx;

/** The LeanCTX instance returned a valid failed observation. */
@SuppressWarnings("removal")
public class InstanceExecutionError extends EngineError {
    private final ContextFailure failure;
    private final ContextView view;

    public InstanceExecutionError() {
        this(null, null, null, null);
    }

    public InstanceExecutionError(String message) {
        this(message, null, null, null);
    }

    public InstanceExecutionError(String message, ContextFailure failure, ContextView view) {
        this(message, failure, view, null);
    }

    public InstanceExecutionError(String message, ContextFailure failure, ContextView view,
                                Throwable cause) {
        super(message, cause, "engine_execution_error",
                "fail closed and retain the factual LeanCTX instance failure evidence",
                failure != null && failure.retryableByHost(), false, true, false, false);
        this.failure = failure;
        this.view = view;
    }

    protected InstanceExecutionError(String message, ContextFailure failure, ContextView view,
                                   Throwable cause, String code, String guidance,
                                   boolean retryable, boolean degradeAllowed,
                                   boolean abortRequired, boolean configurationFix,
                                   boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed,
                abortRequired, configurationFix, versionChange);
        this.failure = failure;
        this.view = view;
    }

    public ContextFailure failure() {
        return failure;
    }

    public ContextFailure getFailure() {
        return failure;
    }

    public ContextView view() {
        return view;
    }

    public ContextView getView() {
        return view;
    }
}
