package com.thinkery.leanctx;

import java.time.Duration;

/**
 * Deprecated alias of {@link EnterpriseInstanceClient}, removed in SDK 2.0.
 *
 * @deprecated since 1.3.0, use {@link EnterpriseInstanceClient}.
 */
@Deprecated(since = "1.3.0", forRemoval = true)
public final class EnterpriseEngineClient extends EnterpriseInstanceClient {
    public EnterpriseEngineClient(String baseUrl, String credential, String tenantId) {
        super(baseUrl, credential, tenantId);
    }

    public EnterpriseEngineClient(String baseUrl, String credential, String tenantId,
                                  Duration timeout) {
        super(baseUrl, credential, tenantId, timeout);
    }

    public EnterpriseEngineClient(String baseUrl, String credential, String tenantId,
                                  Duration timeout, boolean allowLoopbackHttp) {
        super(baseUrl, credential, tenantId, timeout, allowLoopbackHttp);
    }
}
