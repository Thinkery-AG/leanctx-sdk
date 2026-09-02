package com.thinkery.leanctx;

/** The Engine rejected the request at policy admission. */
public class PolicyAdmissionError extends EngineRejected {
    public PolicyAdmissionError() {
        this(null, null, null, null);
    }

    public PolicyAdmissionError(String message) {
        this(message, null, null, null);
    }

    public PolicyAdmissionError(String message, ContextFailure failure, ContextView view) {
        this(message, failure, view, null);
    }

    public PolicyAdmissionError(String message, ContextFailure failure, ContextView view,
                                Throwable cause) {
        super(message, failure, view, cause, "policy_admission_rejected",
                "abort or change the request to satisfy the reported Engine policy",
                failure != null && failure.retryableByHost(), false, true, true, false);
    }
}
