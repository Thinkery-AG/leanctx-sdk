package com.thinkery.leanctx;

/** The immutable AgentContext policy rejected a tool call. */
public class AgentPermissionError extends EngineError {
    public AgentPermissionError() {
        this(null);
    }

    public AgentPermissionError(String message) {
        this(message, null);
    }

    public AgentPermissionError(String message, Throwable cause) {
        super(message, cause, "agent_permission_denied",
                "create a new AgentContext with the required explicit permission",
                false, false, true, true, false);
    }
}
