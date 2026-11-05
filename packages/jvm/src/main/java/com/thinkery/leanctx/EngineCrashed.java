package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceCrashed}, removed in SDK 2.0.
 *
 * <p>Until 2.0 the SDK still throws this subtype, so both
 * {@code catch (EngineCrashed e)} and {@code catch (InstanceCrashed e)} match.
 *
 * @deprecated since 1.3.0, use {@link InstanceCrashed}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public final class EngineCrashed extends InstanceCrashed {
    public EngineCrashed() {
        super();
    }

    public EngineCrashed(String message) {
        super(message);
    }

    public EngineCrashed(String message, Throwable cause) {
        super(message, cause);
    }
}
