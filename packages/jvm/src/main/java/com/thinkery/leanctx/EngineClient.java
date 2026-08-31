package com.thinkery.leanctx;

/** The injected transport seam for the stable Product lifecycle. */
public interface EngineClient {
    ContextView contextView(ContextPlan plan);

    RecoveredSource recover(String projectRoot, String path, String recoveryRef,
                            String sourceRef, String sourceDigest);
}
