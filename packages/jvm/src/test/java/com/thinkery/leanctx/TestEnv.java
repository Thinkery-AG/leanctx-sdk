package com.thinkery.leanctx;

import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

/**
 * Test-harness environment lookup: the 1.3 instance name first, the deprecated
 * engine name as a fallback with a one-time warning per name (removed in 2.0).
 */
final class TestEnv {
    static final String INSTANCE_BIN = "LEANCTX_INSTANCE_BIN";
    static final String INSTANCE_BINARY = "LEANCTX_INSTANCE_BINARY";

    private static final Map<String, String> DEPRECATED = Map.of(
            INSTANCE_BIN, "LEANCTX_ENGINE_BIN",
            INSTANCE_BINARY, "LEANCTX_ENGINE_BINARY",
            "LEANCTX_INSTANCE_PROOF_FILE", "LEANCTX_ENGINE_PROOF_FILE",
            "LEANCTX_REQUIRE_INSTANCE", "LEANCTX_REQUIRE_ENGINE",
            "LEANCTX_TEST_INSTANCE_BINARY", "LEANCTX_TEST_ENGINE_BINARY");
    private static final Set<String> WARNED = ConcurrentHashMap.newKeySet();

    private TestEnv() {
    }

    static String get(String name) {
        return get(name, System::getenv);
    }

    static String get(String name, Function<String, String> env) {
        String value = env.apply(name);
        if (value != null && !value.isEmpty()) {
            return value;
        }
        String old = DEPRECATED.get(name);
        if (old == null) {
            return value;
        }
        String fallback = env.apply(old);
        if (fallback != null && !fallback.isEmpty() && WARNED.add(old)) {
            System.err.println("warning: " + old + " is deprecated since LeanCTX SDK 1.3.0; use "
                    + name + " (the old name is removed in 2.0)");
        }
        return fallback;
    }
}
