package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceUnavailable}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (EngineUnavailable e)} and {@code catch (InstanceUnavailable e)} match.
 *
 * @deprecated since 1.3.0, use {@link InstanceUnavailable}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public final class EngineUnavailable extends InstanceUnavailable {
    public EngineUnavailable() {
        super();
    }

    public EngineUnavailable(String message) {
        super(message);
    }

    public EngineUnavailable(String message, Throwable cause) {
        super(message, cause);
    }
}
