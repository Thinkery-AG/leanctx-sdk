package com.thinkery.leanctx;

/** The Engine returned a valid failed observation. */
public class EngineExecutionError extends EngineError {
    private final ContextFailure failure;
    private final ContextView view;

    public EngineExecutionError() {
        this(null, null, null, null);
    }

    public EngineExecutionError(String message) {
        this(message, null, null, null);
    }

    public EngineExecutionError(String message, ContextFailure failure, ContextView view) {
        this(message, failure, view, null);
    }

    public EngineExecutionError(String message, ContextFailure failure, ContextView view,
                                Throwable cause) {
        super(message, cause, "engine_execution_error",
                "fail closed and retain the factual Engine failure evidence",
                failure != null && failure.retryableByHost(), false, true, false, false);
        this.failure = failure;
        this.view = view;
    }

    protected EngineExecutionError(String message, ContextFailure failure, ContextView view,
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
