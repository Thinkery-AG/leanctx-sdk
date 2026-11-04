package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** SDK 1.3 keeps every Engine name working (deprecated, removed in 2.0). */
@SuppressWarnings("removal")
class DeprecatedEngineNamesTest {
    private static SubprocessInstanceClient missingBinaryClient() throws Exception {
        Path dir = Files.createTempDirectory("leanctx-missing");
        return new SubprocessInstanceClient(dir.resolve("no-such-lean-ctx").toString(), 2.0);
    }

    private static ContextPlan plan() throws Exception {
        Path root = Files.createTempDirectory("leanctx-compat");
        Files.writeString(root.resolve("README.md"), "x");
        return new ContextPlan("s", "t", "inspect", new ContextSource("README.md", root));
    }

    @Test
    void oldCatchClauseStillCatchesWhatTheSdkThrows() throws Exception {
        SubprocessInstanceClient client = missingBinaryClient();
        ContextPlan plan = plan();
        boolean caughtOld = false;
        try {
            client.contextView(plan);
        } catch (EngineUnavailable e) {
            caughtOld = true;
        }
        assertTrue(caughtOld);
        boolean caughtOldBase = false;
        try {
            client.contextView(plan);
        } catch (EngineError e) {
            caughtOldBase = true;
        }
        assertTrue(caughtOldBase);
    }

    @Test
    void newCatchClauseCatchesWhatTheSdkThrows() throws Exception {
        SubprocessInstanceClient client = missingBinaryClient();
        InstanceUnavailable error = assertThrows(InstanceUnavailable.class,
                () -> client.contextView(plan()));
        assertInstanceOf(InstanceError.class, error);
        assertEquals("engine_unavailable", error.code());
        assertTrue(error.getMessage().contains("LeanCTX instance"), error.getMessage());
    }

    @Test
    void everyOldExceptionIsBothAnOldAndANewType() {
        assertInstanceOf(InstanceProtocolError.class, new EngineProtocolError("x"));
        assertInstanceOf(InstanceExecutionError.class, new EngineExecutionError("x"));
        assertInstanceOf(InstanceTimeout.class, new EngineTimeout("x"));
        assertInstanceOf(InstanceCrashed.class, new EngineCrashed("x"));
        assertInstanceOf(InstanceRejected.class, new EngineRejected("x"));
        assertInstanceOf(UnsupportedInstanceError.class, new UnsupportedEngineError("x"));
        // New subtypes stay catchable by the old base until 2.0.
        assertInstanceOf(EngineError.class, new InstanceUnavailable("x"));
        assertInstanceOf(EngineError.class, new InstanceProtocolError("x"));
        assertInstanceOf(EngineRejected.class, new PolicyAdmissionError("x"));
        assertInstanceOf(InstanceRejected.class, new PolicyAdmissionError("x"));
        assertInstanceOf(EngineExecutionError.class, new SourceUnavailableError("x"));
    }

    @Test
    void oldClientTypesAndAccessorsStillWork() {
        SubprocessEngineClient old = new SubprocessEngineClient("lean-ctx-test");
        assertInstanceOf(SubprocessInstanceClient.class, old);
        assertEquals("lean-ctx-test", old.engineBinary());
        assertEquals("lean-ctx-test", old.getEngineBinary());
        assertEquals("lean-ctx-test", old.instanceBinary());
        EngineClient legacy = old;
        ContextSession session = new ContextSession("task", legacy);
        assertSame(legacy, session.engine());
        assertSame(legacy, session.instance());
        assertEquals(InstanceStatus.DEGRADED, EngineStatus.DEGRADED.toInstanceStatus());
        assertEquals(InstanceStatus.FAILED.value(), EngineStatus.FAILED.value());
        EnginePlanningRequest request = new EnginePlanningRequest("task", "query", 100);
        assertInstanceOf(InstancePlanningRequest.class, request);
        assertEquals(LeanCtx.ENGINE_INTERFACE_VERSION, request.toDict().get("engine_interface_version"));
        try (EnterpriseEngineClient enterprise = new EnterpriseEngineClient(
                "https://example.invalid", "credential-value-123456",
                "00000000-0000-4000-8000-000000000000")) {
            assertInstanceOf(EnterpriseInstanceClient.class, enterprise);
        }
    }

    @Test
    void oldEnvironmentVariableIsAFallback() {
        Map<String, String> onlyOld = Map.of("LEANCTX_ENGINE_BIN", "/old/lean-ctx");
        assertEquals("/old/lean-ctx", TestEnv.get(TestEnv.INSTANCE_BIN, onlyOld::get));
        Map<String, String> both = Map.of("LEANCTX_ENGINE_BIN", "/old", "LEANCTX_INSTANCE_BIN", "/new");
        assertEquals("/new", TestEnv.get(TestEnv.INSTANCE_BIN, both::get));
        assertNull(TestEnv.get(TestEnv.INSTANCE_BINARY, Map.<String, String>of()::get));
    }

    @Test
    void versionIs130() {
        assertEquals("1.3.0", LeanCtx.VERSION);
    }
}
