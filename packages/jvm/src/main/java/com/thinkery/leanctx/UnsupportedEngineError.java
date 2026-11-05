package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link UnsupportedInstanceError}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (UnsupportedEngineError e)} and {@code catch (UnsupportedInstanceError e)} match.
 *
 * @deprecated since 1.3.0, use {@link UnsupportedInstanceError}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public final class UnsupportedEngineError extends UnsupportedInstanceError {
    public UnsupportedEngineError() {
        super();
    }

    public UnsupportedEngineError(String message) {
        super(message);
    }

    public UnsupportedEngineError(String message, Throwable cause) {
        super(message, cause);
    }
}
