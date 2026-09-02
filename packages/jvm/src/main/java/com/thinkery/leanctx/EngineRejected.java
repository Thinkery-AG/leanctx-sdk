package com.thinkery.leanctx;

/** The Engine validly rejected a request for policy or security reasons. */
public class EngineRejected extends EngineError {
    private final ContextFailure failure;
    private final ContextView view;

    public EngineRejected() {
        this(null, null, null, null);
    }

    public EngineRejected(String message) {
        this(message, null, null, null);
    }

    public EngineRejected(String message, ContextFailure failure, ContextView view) {
        this(message, failure, view, null);
    }

    public EngineRejected(String message, ContextFailure failure, ContextView view,
                          Throwable cause) {
        super(message, cause, "engine_rejected",
                "fail closed and satisfy the reported Engine policy",
                failure != null && failure.retryableByHost(), false, true, false, false);
        this.failure = failure;
        this.view = view;
    }

    protected EngineRejected(String message, ContextFailure failure, ContextView view,
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
