package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceTimeout}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (EngineTimeout e)} and {@code catch (InstanceTimeout e)} match.
 *
 * @deprecated since 1.3.0, use {@link InstanceTimeout}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public final class EngineTimeout extends InstanceTimeout {
    public EngineTimeout() {
        super();
    }

    public EngineTimeout(String message) {
        super(message);
    }

    public EngineTimeout(String message, Throwable cause) {
        super(message, cause);
    }
}
