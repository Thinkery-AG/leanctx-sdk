package com.thinkery.leanctx;

import java.nio.file.Path;

/**
 * Deprecated alias of {@link SubprocessInstanceClient}, removed in SDK 2.0.
 *
 * @deprecated since 1.3.0, use {@link SubprocessInstanceClient}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public final class SubprocessEngineClient extends SubprocessInstanceClient implements EngineClient {
    public SubprocessEngineClient() {
        super();
    }

    public SubprocessEngineClient(String engineBinary) {
        super(engineBinary);
    }

    public SubprocessEngineClient(Path engineBinary) {
        super(engineBinary);
    }

    public SubprocessEngineClient(String engineBinary, double timeout) {
        super(engineBinary, timeout);
    }

    public SubprocessEngineClient(Path engineBinary, double timeout) {
        super(engineBinary, timeout);
    }
}
