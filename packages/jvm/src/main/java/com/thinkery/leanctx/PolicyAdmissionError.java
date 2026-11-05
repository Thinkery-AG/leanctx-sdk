package com.thinkery.leanctx;

/** The LeanCTX instance rejected the request at policy admission. */
@SuppressWarnings("removal")
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
                "abort or change the request to satisfy the reported LeanCTX instance policy",
                failure != null && failure.retryableByHost(), false, true, true, false);
    }
}
