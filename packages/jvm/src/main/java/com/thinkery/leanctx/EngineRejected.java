package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceRejected}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (EngineRejected e)} and {@code catch (InstanceRejected e)} match.
 *
 * @deprecated since 1.3.0, use {@link InstanceRejected}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public class EngineRejected extends InstanceRejected {
    public EngineRejected() {
        super();
    }

    public EngineRejected(String message) {
        super(message);
    }

    public EngineRejected(String message, ContextFailure failure, ContextView view) {
        super(message, failure, view);
    }

    public EngineRejected(String message, ContextFailure failure, ContextView view, Throwable cause) {
        super(message, failure, view, cause);
    }

    protected EngineRejected(String message, ContextFailure failure, ContextView view,
                     Throwable cause, String code, String guidance,
                     boolean retryable, boolean degradeAllowed,
                     boolean abortRequired, boolean configurationFix,
                     boolean versionChange) {
        super(message, failure, view, cause, code, guidance, retryable, degradeAllowed,
                abortRequired, configurationFix, versionChange);
    }
}
