package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.DisabledOnOs;
import org.junit.jupiter.api.condition.OS;
import org.junit.jupiter.api.io.TempDir;

/**
 * Session containment (Q13): what the SDK asks of the Engine binary and what it
 * reports. Stand-ins follow {@code AgentTerminalConditionsTest}: POSIX shell
 * scripts in a temp root. Nothing here starts a LeanCTX instance except the last
 * test, which needs {@code LEANCTX_INSTANCE_BIN}.
 */
@DisabledOnOs(value = OS.WINDOWS, disabledReason = "POSIX shell stand-in; session containment is requested on POSIX only")
class SessionContainmentTest {
    private static final String ACK = "{\"leanctx_session_containment\":1}";
    private static final String HELLO = "{\"agent_tools_interface_version\":\"1.0.0\",\"allow_exec\":false,"
            + "\"allow_write\":false,\"capabilities\":[\"ctx_compose\",\"ctx_glob\",\"ctx_read\",\"ctx_search\","
            + "\"ctx_symbol\",\"ctx_tree\"],\"engine_version\":\"3.11.0\",\"schema_version\":1,\"transport_version\":1}";

    @TempDir Path root;

    private Path script(String name, String source) throws Exception {
        Path script = root.resolve(name);
        Files.writeString(script, source, StandardCharsets.UTF_8);
        assertEquals(0, new ProcessBuilder("/bin/sh", "-n", script.toString()).start().waitFor(), source);
        assertTrue(script.toFile().setExecutable(true));
        return script;
    }

    /** Answers the hello and one further request (the close); optionally acknowledges first. */
    private Path agentStandIn(boolean acknowledge) throws Exception {
        return script("stand-in-engine.sh", "#!/bin/sh\n"
                + (acknowledge ? "printf '%s\\n' '" + ACK + "'\n" : "")
                + "IFS= read -r line\n"
                + "printf '%s\\n' '{\"id\":\"1\",\"ok\":true,\"result\":" + HELLO + "}'\n"
                + "IFS= read -r line\n"
                + "printf '%s\\n' '{\"id\":\"2\",\"ok\":true,\"result\":{}}'\n");
    }

    private AgentContext open(Path binary) throws Exception {
        return new AgentContext(root.toRealPath().toString(), "containment", new AgentPermissions(),
                new ExecutionPolicy(), binary.toString(), 10.0);
    }

    private static void closeQuietly(AgentContext context) {
        try {
            context.close();
        } catch (RuntimeException ignored) {
            // The subject of these tests is the reported state, not the close.
        }
    }

    @Test
    void agentWithoutAcknowledgementIsUnavailable() throws Exception {
        AgentContext context = open(agentStandIn(false));
        try {
            assertEquals(SessionContainment.UNAVAILABLE, context.sessionContainment());
            assertEquals(6, context.capabilities().size());
        } finally {
            closeQuietly(context);
        }
    }

    @Test
    void agentAcknowledgementIsReportedAndIsNotAResponse() throws Exception {
        AgentContext context = open(agentStandIn(true));
        try {
            assertEquals(SessionContainment.ENGINE_STARTER, context.sessionContainment());
            // The hello that followed the acknowledgement was accepted unchanged.
            assertEquals(6, context.capabilities().size());
            assertEquals("engine-starter", context.sessionContainment().wireName());
        } finally {
            closeQuietly(context);
        }
    }

    @Test
    void oneShotOutputWithoutAcknowledgementIsLeftAlone() {
        byte[] payload = "{\"payload\":\"é\",\"n\":1}\n".getBytes(StandardCharsets.UTF_8);
        assertEquals(0, SessionContainment.ackPrefixLength(payload));
        assertEquals(0, SessionContainment.ackPrefixLength(new byte[0]));
        // The line alone, without its newline, is not a complete acknowledgement.
        assertEquals(0, SessionContainment.ackPrefixLength(ACK.getBytes(StandardCharsets.UTF_8)));
        assertFalse(SessionContainment.isAck(payload));
    }

    @Test
    void oneShotAcknowledgementIsStrippedAndThePayloadIsIdentical() {
        byte[] payload = "{\"payload\":\"é\",\"n\":1}\n".getBytes(StandardCharsets.UTF_8);
        byte[] output = (ACK + "\n" + new String(payload, StandardCharsets.UTF_8)).getBytes(StandardCharsets.UTF_8);
        int prefix = SessionContainment.ackPrefixLength(output);
        assertEquals(ACK.length() + 1, prefix);
        assertArrayEquals(payload, Arrays.copyOfRange(output, prefix, output.length));
        assertTrue(SessionContainment.isAck(ACK.getBytes(StandardCharsets.UTF_8)));
    }

    private ContextPlan plan() throws Exception {
        Files.writeString(root.resolve("README.md"), "fixture\n", StandardCharsets.UTF_8);
        return new ContextPlan("fixture-session", "fixture-task", "inspect",
                new ContextSource("README.md", root));
    }

    private static final String VIEW_TEXT = "fresh synthetic view\n";

    /**
     * A complete, valid {@code context-view} response: the same synthetic
     * values as {@code ProductConformanceTest.serializationFixtureView}, in
     * the wire form {@code EngineProtocol.parseResponse} accepts.
     */
    private static String succeedingResponse() {
        String outputDigest = Protocol.sha256Digest(VIEW_TEXT);
        String outputRef = "output:" + outputDigest.substring("sha256:".length());
        String sourceDigest = Protocol.sha256Digest("fresh synthetic source\n");
        String sourceRef = "source:synthetic-path-sha256:" + "a".repeat(64);
        String inputRef = "input:synthetic-request-sha256:" + "b".repeat(64);
        String refs = "[\"" + inputRef + "\",\"" + sourceRef + "\"]";
        return "{\"schema_version\":" + LeanCtx.SCHEMA_VERSION
                + ",\"transport_version\":" + LeanCtx.TRANSPORT_VERSION
                + ",\"engine_interface_version\":\"" + LeanCtx.ENGINE_INTERFACE_VERSION + "\""
                + ",\"view\":{\"text\":\"fresh synthetic view\\n\",\"output_ref\":\"" + outputRef
                + "\",\"output_digest\":\"" + outputDigest + "\"}"
                + ",\"invocation\":{\"schema_version\":1,\"invocation_id\":\"engine-invocation-synthetic\""
                + ",\"engine\":{\"engine_id\":\"lean-ctx-local\",\"engine_version\":\"3.9.20\"}"
                + ",\"operation\":{\"capability_id\":\"capability://leanctx/context-optimization\""
                + ",\"capability_version\":\"1.0.0\"}"
                + ",\"input_ref\":\"" + inputRef + "\",\"input_digest\":\"sha256:" + "c".repeat(64) + "\""
                + ",\"source_refs\":" + refs
                + ",\"policy_admission\":{\"policy_ref\":\"policy:synthetic\",\"decision\":\"admitted\"}}"
                + ",\"observation\":{\"schema_version\":1,\"invocation_id\":\"engine-invocation-synthetic\""
                + ",\"status\":\"succeeded\",\"output_ref\":\"" + outputRef
                + "\",\"output_digest\":\"" + outputDigest + "\""
                + ",\"source_lineage\":" + refs
                + ",\"measurements\":[{\"name\":\"input_tokens\",\"unit\":\"token\",\"classification\":\"measured\",\"value\":1}"
                + ",{\"name\":\"output_tokens\",\"unit\":\"token\",\"classification\":\"measured\",\"value\":2}]"
                + ",\"failure\":null"
                + ",\"receipt_link\":{\"schema_version\":1,\"receipt_id\":\"engine-receipt-synthetic\""
                + ",\"receipt_ref\":\"receipt:sha256:" + "d".repeat(64) + "\""
                + ",\"receipt_digest\":\"sha256:" + "d".repeat(64) + "\""
                + ",\"invocation_id\":\"engine-invocation-synthetic\"}}"
                + ",\"recovery\":{\"recovery_ref\":\"" + inputRef + "\",\"source_ref\":\"" + sourceRef
                + "\",\"source_digest\":\"" + sourceDigest + "\"}}";
    }

    /** Writes the response from a file, so no JSON passes through shell quoting. */
    private Path succeedingStandIn(boolean acknowledge) throws Exception {
        Path response = root.resolve("response.json");
        Files.writeString(response, succeedingResponse(), StandardCharsets.UTF_8);
        return script(acknowledge ? "one-shot-ack.sh" : "one-shot-plain.sh", "#!/bin/sh\n"
                + (acknowledge ? "printf '%s\\n' '" + ACK + "'\n" : "")
                + "cat '" + response + "'\n");
    }

    /**
     * B16: a successful one-shot run end to end. With and without the
     * acknowledgement the client hands the same payload to the parser — the
     * view verifies against its digest either way — and reports what the
     * first line said.
     */
    @Test
    void oneShotSuccessfulRunStripsAndRecordsTheAcknowledgement() throws Exception {
        ContextPlan plan = plan();

        SubprocessInstanceClient plain = new SubprocessInstanceClient(succeedingStandIn(false), 10.0);
        ContextView unacknowledged = plain.contextView(plan);
        assertEquals(VIEW_TEXT, unacknowledged.text());
        assertEquals(SessionContainment.UNAVAILABLE, plain.lastSessionContainment());

        SubprocessInstanceClient contained = new SubprocessInstanceClient(succeedingStandIn(true), 10.0);
        assertEquals(SessionContainment.UNAVAILABLE, contained.lastSessionContainment());
        ContextView acknowledged = contained.contextView(plan);
        assertEquals(SessionContainment.ENGINE_STARTER, contained.lastSessionContainment());
        assertEquals(VIEW_TEXT, acknowledged.text());
        assertEquals(unacknowledged.outputDigest(), acknowledged.outputDigest());
        assertEquals(Integrity.SEALED.value(), acknowledged.integrityStatus().value());
    }

    @Test
    void oneShotFailedRunStillRecordsTheAcknowledgement() throws Exception {
        Path failing = script("one-shot-engine.sh", "#!/bin/sh\n"
                + "printf '%s\\n' '" + ACK + "'\n"
                + "echo 'engine: nonzero_exit' >&2\n"
                + "exit 3\n");
        SubprocessInstanceClient client = new SubprocessInstanceClient(failing, 10.0);
        assertEquals(SessionContainment.UNAVAILABLE, client.lastSessionContainment());
        ContextPlan plan = plan();
        assertThrows(InstanceError.class, () -> client.contextView(plan));
        assertEquals(SessionContainment.ENGINE_STARTER, client.lastSessionContainment());
    }

    @Test
    void oneShotFailedRunWithoutAcknowledgementStaysUnavailable() throws Exception {
        Path failing = script("one-shot-engine.sh", "#!/bin/sh\n"
                + "echo 'engine: nonzero_exit' >&2\n"
                + "exit 3\n");
        SubprocessInstanceClient client = new SubprocessInstanceClient(failing, 10.0);
        ContextPlan plan = plan();
        assertThrows(InstanceError.class, () -> client.contextView(plan));
        assertEquals(SessionContainment.UNAVAILABLE, client.lastSessionContainment());
    }

    /** Real Engine; skipped without it, as {@code GatewayPreviewTest} does for its variable. */
    @Test
    void realEngineSessionStarterAcknowledges() throws Exception {
        String engine = TestEnv.get(TestEnv.INSTANCE_BIN);
        assumeTrue(engine != null && !engine.isEmpty(), "needs a real LeanCTX instance (LEANCTX_INSTANCE_BIN)");
        Path standIn = agentStandIn(false);
        // The stand-in is started through the real binary's session starter.
        Path wrapper = script("contained-engine.sh", "#!/bin/sh\n"
                + "exec '" + Path.of(engine).toRealPath() + "' __session-starter --ack --watch-parent -- '"
                + standIn + "' \"$@\"\n");
        AgentContext context = open(wrapper);
        try {
            assertEquals(SessionContainment.ENGINE_STARTER, context.sessionContainment());
        } finally {
            closeQuietly(context);
        }
    }
}
