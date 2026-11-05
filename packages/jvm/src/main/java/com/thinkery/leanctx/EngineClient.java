package com.thinkery.leanctx;

/**
 * Deprecated alias of {@link InstanceClient}, removed in SDK 2.0. Existing
 * implementations keep working wherever an {@code InstanceClient} is accepted.
 *
 * @deprecated since 1.3.0, implement {@link InstanceClient}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public interface EngineClient extends InstanceClient {
}
