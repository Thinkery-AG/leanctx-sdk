// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
// Source union retains private preparation/report/refusal behavior; Context Store is separate.
package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class GatewayPreviewTest {
    private static final Path FIXTURES = Path.of("../../fixtures/gateway-preview-v1");

    private static Object load(String kind, String name) throws IOException {
        return Json.parse(Files.readAllBytes(FIXTURES.resolve(kind).resolve(name + ".json")), "fixture");
    }

    @Test
    void egressPreparePreservesTextAndReportIsHostReported() throws Exception {
        String prepared = "{ \"model\" : \"m\" }";
        String finalContext = "sha256:" + HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(prepared.getBytes(StandardCharsets.UTF_8)));
        var receipt = Map.<String, Object>ofEntries(
                Map.entry("schema_version", 1), Map.entry("receipt_id", "egress-test"), Map.entry("mode", "governed"),
                Map.entry("principal", Map.of("kind", "unknown")), Map.entry("destination", Map.of("provider", "openai", "locality", "remote")),
                Map.entry("sources", Map.of("inspected", 0, "permitted", 0, "selected", 0, "blocked", 0)),
                Map.entry("security", Map.of("redactions", 0, "blocked_objects", 0, "quarantined_objects", 0,
                        "injection_signals", 0, "incomplete_coverage", 0)),
                Map.entry("tokens", Map.of("original", 0, "delivered", 0)), Map.entry("outcome", "delivered"),
                Map.entry("duration_us", 0), Map.entry("final_context", finalContext));
        var decision = Map.<String, Object>of("schema_version", 1, "disposition", "forward",
                "body", Map.of("model", "m"), "classification", "internal", "receipt", receipt);
        var prep = Map.<String, Object>of("schema_version", 2, "decision", decision, "prepared_body", prepared,
                "admission_receipt_digest", "sha256:" + "b".repeat(64));
        var preparation = GatewayPreview.parseEgressPreparation(prep);
        assertEquals(Optional.of(prepared), preparation.preparedBody());
        assertTrue(preparation.maySend());
        assertFalse(GatewayPreview.parseEgressPreparation(Map.of("schema_version", 2,
                "decision", Map.of("schema_version", 1, "disposition", "refused", "refusal", "withheld"))).maySend());
        assertTrue(GatewayPreview.parseEgressPreparation(Map.of("schema_version", 2,
                "decision", Map.of("schema_version", 1, "disposition", "forward", "body", Map.of("model", "m")),
                "prepared_body", prepared)).maySend());
        var rewrittenDecision = new java.util.LinkedHashMap<String, Object>(decision);
        rewrittenDecision.put("disposition", "rewritten");
        var rewrittenPreparation = new java.util.LinkedHashMap<String, Object>(prep);
        rewrittenPreparation.put("decision", rewrittenDecision);
        assertTrue(GatewayPreview.parseEgressPreparation(rewrittenPreparation).maySend());
        for (String disposition : List.of("forward", "rewritten")) {
            for (String outcome : List.of("withheld", "failed")) {
                var badReceipt = new java.util.LinkedHashMap<String, Object>(receipt);
                badReceipt.put("outcome", outcome);
                badReceipt.remove("final_context");
                var badDecision = new java.util.LinkedHashMap<String, Object>(decision);
                badDecision.put("disposition", disposition);
                badDecision.put("receipt", badReceipt);
                var badPreparation = new java.util.LinkedHashMap<String, Object>(prep);
                badPreparation.put("decision", badDecision);
                assertThrows(InstanceProtocolError.class, () -> GatewayPreview.parseEgressPreparation(badPreparation),
                        disposition + " with " + outcome + " receipt was accepted");
            }
        }
        assertThrows(InstanceProtocolError.class, () -> GatewayPreview.parseEgressPreparation(Map.of(
                "schema_version", 2, "decision", Map.of("schema_version", 1, "disposition", "refused",
                        "refusal", "withheld", "receipt", receipt))),
                "refused decision with delivered receipt was accepted");

        var request = new GatewayPreview.EgressHostReportRequest("sha256:" + "b".repeat(64), 2, "http",
                "no_response_observed", Optional.empty(), Optional.of("timeout"));
        var observation = Map.<String, Object>of("schema_version", 1,
                "admission_receipt_digest", request.admissionReceiptDigest(), "attempt", 2,
                "transport", "http", "observer", "host_reported", "result", "no_response_observed", "failure", "timeout");
        String canonicalObservation = "{\"admission_receipt_digest\":\"" + request.admissionReceiptDigest()
                + "\",\"attempt\":2,\"failure\":\"timeout\",\"observer\":\"host_reported\","
                + "\"result\":\"no_response_observed\",\"schema_version\":1,\"transport\":\"http\"}";
        String observationDigest = "sha256:" + HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(canonicalObservation.getBytes(StandardCharsets.UTF_8)));
        var report = GatewayPreview.parseEgressHostReport(Map.of("schema_version", 1,
                "observation_digest", observationDigest, "observation", observation), request);
        assertEquals("host_reported", report.observation().get("observer"));
        assertEquals("no_response_observed", report.observation().get("result"));
        var engineObservation = new java.util.LinkedHashMap<>(observation);
        engineObservation.put("observer", "engine_observed");
        assertThrows(InstanceProtocolError.class, () -> GatewayPreview.parseEgressHostReport(Map.of("schema_version", 1,
                "observation_digest", observationDigest, "observation", engineObservation), request));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> manifest() throws IOException {
        return (Map<String, Object>) Json.parse(Files.readAllBytes(FIXTURES.resolve("manifest.json")), "manifest");
    }

    @Test
    @SuppressWarnings("unchecked")
    void receiptV2ObjectsAreKeyedLikeTheEngineKeysThem() throws Exception {
        Map<String, Object> vector = (Map<String, Object>) manifest().get("keyed_v2");
        byte[] key = HexFormat.of().parseHex((String) vector.get("key_hex"));
        String plain = "sha256:" + HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(((String) vector.get("object_text")).getBytes(StandardCharsets.UTF_8)));
        String keyed = (String) vector.get("keyed_object");
        assertEquals(vector.get("key_id"), GatewayPreview.receiptKeyId(key));
        assertEquals(keyed, GatewayPreview.keyedObjectDigest(key, plain));
        assertFalse(keyed.equals(GatewayPreview.keyedObjectDigest(new byte[32], plain)));
        var receipt = GatewayPreview.parseEgressAdmission(load("valid", (String) vector.get("fixture")))
                .receipt().orElseThrow();
        assertEquals(2L, receipt.schemaVersion());
        var objectDigest = receipt.objectDigest().orElseThrow();
        assertEquals("hmac-sha256", objectDigest.algorithm());
        assertEquals(vector.get("key_id"), objectDigest.keyId());
        var objects = receipt.decisions().stream().map(GatewayPreview.ContextDecision::object).toList();
        assertEquals(List.of(keyed), objects);
        assertFalse(objects.contains(plain), "a plain-SHA-256 guess confirms nothing");
        var v1 = GatewayPreview.parseEgressAdmission(load("valid", "masked")).receipt().orElseThrow();
        assertEquals(1L, v1.schemaVersion());
        assertTrue(v1.objectDigest().isEmpty());
    }

    @Test
    @SuppressWarnings("unchecked")
    void realEngineResponsesParseWithTheirRecordedShape() throws IOException {
        Map<String, Object> valid = (Map<String, Object>) manifest().get("valid");
        for (Map.Entry<String, Object> entry : valid.entrySet()) {
            Map<String, Object> expected = (Map<String, Object>) entry.getValue();
            GatewayPreview.EgressAdmission admission = GatewayPreview.parseEgressAdmission(load("valid", entry.getKey()));
            GatewayPreview.ContextDecisionReceipt receipt = admission.receipt().orElseThrow();
            String name = entry.getKey();
            assertEquals(expected.get("disposition"), admission.disposition(), name);
            assertEquals(expected.get("classification"), admission.classification().orElse(null), name);
            assertEquals(expected.get("outcome"), receipt.outcome(), name);
            assertEquals(((Number) expected.get("decisions")).longValue(), receipt.decisions().size(), name);
            assertEquals(((Number) expected.get("denied")).longValue(),
                    receipt.decisions().stream().filter(d -> d.disposition().equals("deny")).count(), name);
            assertEquals(((Number) expected.get("redactions")).longValue(), receipt.security().get("redactions"), name);
            assertEquals(((Number) expected.get("signals")).longValue(), receipt.signals().size(), name);
            assertTrue(admission.maySend(), name);
            assertFalse(receipt.principal().isKnown(), name);
        }
    }

    @Test
    @SuppressWarnings("unchecked")
    void everyInvalidCaseIsRejected() throws IOException {
        for (Object name : (List<Object>) manifest().get("invalid")) {
            Object document = load("invalid", (String) name);
            assertThrows(InstanceProtocolError.class, () -> GatewayPreview.parseEgressAdmission(document), (String) name);
        }
    }

    @Test
    @SuppressWarnings("unchecked")
    void liveEngineMasksWithholdsAndClassifies() throws Exception {
        String binary = TestEnv.get(TestEnv.INSTANCE_BINARY);
        assumeTrue(binary != null && !binary.isEmpty(), "needs a real LeanCTX instance (LEANCTX_INSTANCE_BINARY)");
        SubprocessInstanceClient engine = new SubprocessInstanceClient(binary);
        String root = Files.createTempDirectory("leanctx-gateway-jvm").toString();
        String credential = "AKIA" + "Q3EGRZ7LIVEX4KEY";

        GatewayPreview.EgressAdmission masked = admit(engine, root, "deploy fails with " + credential,
                "openai", "https://api.openai.com");
        assertEquals("rewritten", masked.disposition());
        assertFalse(masked.body().orElseThrow().toString().contains(credential));
        assertEquals(1L, masked.receipt().orElseThrow().security().get("redactions"));

        GatewayPreview.EgressAdmission restricted = admit(engine, root,
                "Classification: Secret\nroot cause and customer list", "anthropic", "https://api.anthropic.com");
        assertEquals("restricted", restricted.classification().orElseThrow());
        assertEquals("withheld", restricted.receipt().orElseThrow().outcome());
        assertEquals("refused", restricted.disposition());
        assertFalse(restricted.maySend());
        assertTrue(restricted.body().isEmpty());
        assertTrue(restricted.receipt().orElseThrow().decisions().get(0).reasonCodes()
                .contains("destination.remote_restricted"));

        GatewayPreview.EgressAdmission marked = admit(engine, root, "Classification: Confidential\nboard minutes",
                "openai", "https://api.openai.com");
        assertEquals("forward", marked.disposition());
        assertEquals("confidential", marked.classification().orElseThrow());
        assertEquals("remote", marked.receipt().orElseThrow().destination().locality());

        String model = "host-selected-model-2026";
        String credentialValue = "AKIA" + "Q3EGRZ7LIVEX4KEY";
        GatewayPreview.EgressPreparation preparation = GatewayPreview.prepareEgress(engine, root,
                new GatewayPreview.EgressRequest("openai", "https://api.openai.com",
                        Map.of("model", model, "messages", List.of(Map.of("role", "user",
                                "content", "deploy fails with " + credentialValue)))));
        assertTrue(preparation.maySend());
        String preparedBody = preparation.preparedBody().orElseThrow();
        String admissionDigest = preparation.admissionReceiptDigest().orElseThrow();
        Map<?, ?> prepared = (Map<?, ?>) Json.parse(preparedBody.getBytes(StandardCharsets.UTF_8), "prepared egress body");
        assertEquals(model, prepared.get("model"));
        assertFalse(preparedBody.contains(credentialValue));
        GatewayPreview.ContextDecisionReceipt receipt = preparation.decision().receipt().orElseThrow();
        assertEquals(1L, receipt.security().get("redactions"));
        assertEquals("openai", receipt.destination().provider());
        assertEquals(Optional.of(model), receipt.destination().model());
        String bodyDigest = "sha256:" + HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(preparedBody.getBytes(StandardCharsets.UTF_8)));
        assertEquals(bodyDigest, receipt.finalContext().orElseThrow());

        GatewayPreview.EgressHostReportRequest report = new GatewayPreview.EgressHostReportRequest(
                admissionDigest, 1, "http", "no_response_observed", Optional.empty(), Optional.of("timeout"));
        GatewayPreview.EgressHostReportResponse first = GatewayPreview.reportEgress(engine, root, report);
        GatewayPreview.EgressHostReportResponse replay = GatewayPreview.reportEgress(engine, root, report);
        assertEquals("host_reported", first.observation().get("observer"));
        assertEquals("no_response_observed", first.observation().get("result"));
        assertEquals("timeout", first.observation().get("failure"));
        assertEquals(first.observationDigest(), replay.observationDigest());

        GatewayPreview.EgressHostReportRequest conflict = new GatewayPreview.EgressHostReportRequest(
                admissionDigest, 1, "http", "response_observed", Optional.of(503), Optional.empty());
        InstanceExecutionError conflictError = assertThrows(InstanceExecutionError.class,
                () -> GatewayPreview.reportEgress(engine, root, conflict));
        assertTrue(conflictError.getMessage().contains("egress_observation_unavailable"));
    }

    private static GatewayPreview.EgressAdmission admit(SubprocessInstanceClient engine, String root, String text,
                                                        String provider, String base) {
        Map<String, Object> body = Map.of("model", "m", "temperature", 0.2,
                "messages", List.of(Map.of("role", "user", "content", text)));
        return GatewayPreview.admitEgress(engine, root, new GatewayPreview.EgressRequest(provider, base, body));
    }
}
