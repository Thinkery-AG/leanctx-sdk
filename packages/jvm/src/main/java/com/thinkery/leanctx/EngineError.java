package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceError}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (EngineError e)} and {@code catch (InstanceError e)} match.
 *
 * @deprecated since 1.3.0, use {@link InstanceError}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public class EngineError extends InstanceError {
    public EngineError() {
        super();
    }

    public EngineError(String message) {
        super(message);
    }

    public EngineError(String message, Throwable cause) {
        super(message, cause);
    }

    protected EngineError(String message, Throwable cause, String code, String guidance,
                     boolean retryable, boolean degradeAllowed, boolean abortRequired,
                     boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed, abortRequired,
                configurationFix, versionChange);
    }
}
