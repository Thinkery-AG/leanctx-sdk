package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;

class ProductConformanceTest {
    @Test
    void planIdentityMatchesIndependentFixture() {
        ContextSource source = new ContextSource("fixture/source.txt", "/PROJECT");
        ContextPlan plan = new ContextPlan("fixture-session-r1", "fixture-task-r1",
                "inspect the synthetic fixture", source);
        assertEquals("plan:sha256:25f29db61cbb19986896152ecf2c8b1b60a1187c83a8e4ceefb0b7203542296e",
                plan.planId());
    }

    @Test
    void allFiveProductPrimitivesMatchFrozenFingerprints() throws IOException {
        ContextSource source = new ContextSource("fixture/source.txt", "/PROJECT");
        ContextPlan plan = new ContextPlan("session-fixed", "task-fixed", "inspect", source);
        ContextView view = serializationFixtureView(source);
        ContextReceipt receipt = new ContextReceipt("session-fixed", "task-fixed",
                plan.planId(), view, HostOutcome.COMPLETED.value(), Integrity.SEALED.value(),
                List.of(), Map.of("requests", 1), null, null, null);
        Map<String, Object> session = new LinkedHashMap<>();
        session.put("session_id", "session-fixed");
        session.put("task_id", "task-fixed");
        session.put("task", "inspect");
        session.put("state", SessionState.CREATED.value());

        var stream = getClass().getResourceAsStream("/sdk-v1/serialization-sha256.json");
        assertNotNull(stream);
        String fixture = new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        assertEquals(fixtureFingerprint(fixture, "ContextSource"), digest(source.toDict()));
        assertEquals(fixtureFingerprint(fixture, "ContextPlan"), digest(plan.toDict()));
        assertEquals(fixtureFingerprint(fixture, "ContextView"), digest(view.toDict()));
        assertEquals(fixtureFingerprint(fixture, "ContextReceipt"), digest(receipt.toDict()));
        assertEquals(fixtureFingerprint(fixture, "ContextSession"), digest(session));
    }

    @Test
    void canonicalJsonUsesCodePointOrderingAndRejectsAmbiguousNumbers() {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("𐀀", 1);
        value.put("", 2);
        assertEquals("{\"\":2,\"𐀀\":1}", Json.canonical(value));
        assertThrows(ValidationError.class, () -> Json.canonical(Map.of("value", 1.5)));
        assertThrows(ValidationError.class, () -> Json.canonical(
                Map.of("value", Double.longBitsToDouble(0x8000000000000000L))));
    }

    @Test
    void productValuesAreImmutableAndBindingsAreChecked() {
        ContextSource source = new ContextSource("fixture/source.txt", "/PROJECT");
        ContextPlan plan = new ContextPlan("session-fixed", "task-fixed", "inspect", source);
        assertThrows(UnsupportedOperationException.class,
                () -> plan.toMap().put("task", "changed"));
        assertThrows(ValidationError.class, () -> new ContextReceiptLink(1, "receipt",
                "receipt:sha256:" + "a".repeat(64), "sha256:" + "b".repeat(64), "invocation"));
    }

    @Test
    void optionalRealEngineV1RoundTrip() {
        String binary = System.getenv("LEANCTX_ENGINE_BIN");
        Assumptions.assumeTrue(binary != null && !binary.isBlank());
        String root = System.getenv("LEANCTX_TEST_ROOT");
        Assumptions.assumeTrue(root != null && !root.isBlank());
        ContextSource source = new ContextSource("README.md", Path.of(root));
        ContextPlan plan = new ContextPlan("jvm-real-engine", "jvm-real-task", "inspect", source);
        ContextView view = new SubprocessEngineClient(binary, 30.0).contextView(plan);
        assertEquals(Integrity.SEALED.value(), view.integrityStatus().value());
    }

    private static String digest(Object value) {
        return Protocol.sha256Hex(Json.canonicalBytes(value));
    }

    private static String fixtureFingerprint(String fixture, String name) {
        Matcher matcher = Pattern.compile("\\\"" + Pattern.quote(name)
                + "\\\"\\s*:\\s*\\\"([0-9a-f]{64})\\\"").matcher(fixture);
        if (!matcher.find()) {
            throw new AssertionError("missing frozen fingerprint for " + name);
        }
        return matcher.group(1);
    }

    private static ContextView serializationFixtureView(ContextSource source) {
        String text = "fresh synthetic view\n";
        String sourceText = "fresh synthetic source\n";
        String outputDigest = Protocol.sha256Digest(text);
        String sourceDigest = Protocol.sha256Digest(sourceText);
        String sourceRef = "source:synthetic-path-sha256:" + "a".repeat(64);
        String inputRef = "input:synthetic-request-sha256:" + "b".repeat(64);
        String invocationId = "engine-invocation-synthetic";
        List<ContextMeasurement> measurements = List.of(
                new ContextMeasurement("input_tokens", "token", "measured", 1),
                new ContextMeasurement("output_tokens", "token", "measured", 2));
        ContextReceiptLink receiptLink = new ContextReceiptLink(1,
                "engine-receipt-synthetic", "receipt:sha256:" + "d".repeat(64),
                "sha256:" + "d".repeat(64), invocationId);
        Map<String, Object> invocation = new LinkedHashMap<>();
        invocation.put("schema_version", 1);
        invocation.put("invocation_id", invocationId);
        invocation.put("engine", Map.of("engine_id", "lean-ctx-local", "engine_version", "3.9.20"));
        invocation.put("operation", Map.of("capability_id", "capability://leanctx/context-optimization",
                "capability_version", "1.0.0"));
        invocation.put("input_ref", inputRef);
        invocation.put("input_digest", "sha256:" + "c".repeat(64));
        invocation.put("source_refs", List.of(inputRef, sourceRef));
        invocation.put("policy_admission", Map.of("policy_ref", "policy:synthetic",
                "decision", "admitted"));
        Map<String, Object> observation = new LinkedHashMap<>();
        observation.put("schema_version", 1);
        observation.put("invocation_id", invocationId);
        observation.put("status", "succeeded");
        observation.put("output_ref", "output:" + outputDigest.substring("sha256:".length()));
        observation.put("output_digest", outputDigest);
        observation.put("source_lineage", List.of(inputRef, sourceRef));
        observation.put("measurements", measurements);
        observation.put("failure", null);
        observation.put("receipt_link", receiptLink);
        return new ContextView(source, text,
                "output:" + outputDigest.substring("sha256:".length()), outputDigest,
                sourceRef, sourceDigest, inputRef, "succeeded", measurements, null,
                receiptLink, invocation, observation);
    }
}
