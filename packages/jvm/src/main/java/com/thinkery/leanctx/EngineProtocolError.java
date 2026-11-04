package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceProtocolError}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (EngineProtocolError e)} and {@code catch (InstanceProtocolError e)} match.
 *
 * @deprecated since 1.3.0, use {@link InstanceProtocolError}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public class EngineProtocolError extends InstanceProtocolError {
    public EngineProtocolError() {
        super();
    }

    public EngineProtocolError(String message) {
        super(message);
    }

    public EngineProtocolError(String message, Throwable cause) {
        super(message, cause);
    }

    protected EngineProtocolError(String message, Throwable cause, String code, String guidance,
                     boolean retryable, boolean degradeAllowed, boolean abortRequired,
                     boolean configurationFix, boolean versionChange) {
        super(message, cause, code, guidance, retryable, degradeAllowed, abortRequired,
                configurationFix, versionChange);
    }
}
