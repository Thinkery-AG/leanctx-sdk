package com.thinkery.leanctx;

/** The LeanCTX instance validly rejected a request for policy or security reasons. */
@SuppressWarnings("removal")
public class InstanceRejected extends EngineError {
    private final ContextFailure failure;
    private final ContextView view;

    public InstanceRejected() {
        this(null, null, null, null);
    }

    public InstanceRejected(String message) {
        this(message, null, null, null);
    }

    public InstanceRejected(String message, ContextFailure failure, ContextView view) {
        this(message, failure, view, null);
    }

    public InstanceRejected(String message, ContextFailure failure, ContextView view,
                          Throwable cause) {
        super(message, cause, "engine_rejected",
                "fail closed and satisfy the reported LeanCTX instance policy",
                failure != null && failure.retryableByHost(), false, true, false, false);
        this.failure = failure;
        this.view = view;
    }

    protected InstanceRejected(String message, ContextFailure failure, ContextView view,
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
