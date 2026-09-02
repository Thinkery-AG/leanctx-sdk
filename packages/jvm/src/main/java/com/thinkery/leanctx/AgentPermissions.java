package com.thinkery.leanctx;

/** Immutable host-granted Agent Tools permissions. */
public final class AgentPermissions {
    private final boolean write;
    private final boolean execute;

    public AgentPermissions() {
        this(false, false);
    }

    public AgentPermissions(boolean write, boolean execute) {
        this.write = write;
        this.execute = execute;
    }

    public boolean write() {
        return write;
    }

    public boolean isWrite() {
        return write;
    }

    public boolean allowWrite() {
        return write;
    }

    public boolean allow_write() {
        return write;
    }

    public boolean execute() {
        return execute;
    }

    public boolean isExecute() {
        return execute;
    }

    public boolean allowExecute() {
        return execute;
    }

    public boolean allow_exec() {
        return execute;
    }
}
