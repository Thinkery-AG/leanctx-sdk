package com.thinkery.leanctx;

/** An operation is not legal in the current session state. */
public class SessionStateError extends SDKError {
    public SessionStateError() {
        this(null);
    }

    public SessionStateError(String message) {
        this(message, null);
    }

    public SessionStateError(String message, Throwable cause) {
        super(message, cause, "session_state_error",
                "fix lifecycle ordering or create a new session",
                false, false, true, false, false);
    }
}
