package com.thinkery.leanctx;

/** Caller supplied an invalid Product or protocol value. */
public class ValidationError extends SDKError {
    public ValidationError() {
        this(null);
    }

    public ValidationError(String message) {
        this(message, null);
    }

    public ValidationError(String message, Throwable cause) {
        super(message, cause, "validation_error", "fix caller input before retrying",
                false, false, true, false, false);
    }
}
