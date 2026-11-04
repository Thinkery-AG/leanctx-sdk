package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceExecutionError}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (EngineExecutionError e)} and {@code catch (InstanceExecutionError e)} match.
 *
 * @deprecated since 1.3.0, use {@link InstanceExecutionError}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public class EngineExecutionError extends InstanceExecutionError {
    public EngineExecutionError() {
        super();
    }

    public EngineExecutionError(String message) {
        super(message);
    }

    public EngineExecutionError(String message, ContextFailure failure, ContextView view) {
        super(message, failure, view);
    }

    public EngineExecutionError(String message, ContextFailure failure, ContextView view, Throwable cause) {
        super(message, failure, view, cause);
    }

    protected EngineExecutionError(String message, ContextFailure failure, ContextView view,
                     Throwable cause, String code, String guidance,
                     boolean retryable, boolean degradeAllowed,
                     boolean abortRequired, boolean configurationFix,
                     boolean versionChange) {
        super(message, failure, view, cause, code, guidance, retryable, degradeAllowed,
                abortRequired, configurationFix, versionChange);
    }
}
