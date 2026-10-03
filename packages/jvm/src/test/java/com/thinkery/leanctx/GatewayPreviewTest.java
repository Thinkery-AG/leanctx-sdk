// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class GatewayPreviewTest {
    private static final Path FIXTURES = Path.of("../../fixtures/gateway-preview-v1");

    private static Object load(String kind, String name) throws IOException {
        return Json.parse(Files.readAllBytes(FIXTURES.resolve(kind).resolve(name + ".json")), "fixture");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> manifest() throws IOException {
        return (Map<String, Object>) Json.parse(Files.readAllBytes(FIXTURES.resolve("manifest.json")), "manifest");
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
            assertThrows(EngineProtocolError.class, () -> GatewayPreview.parseEgressAdmission(document), (String) name);
        }
    }

    @Test
    @SuppressWarnings("unchecked")
    void liveEngineMasksWithholdsAndClassifies() throws IOException {
        String binary = System.getenv("LEANCTX_ENGINE_BINARY");
        assumeTrue(binary != null && !binary.isEmpty(), "needs a real Engine (LEANCTX_ENGINE_BINARY)");
        SubprocessEngineClient engine = new SubprocessEngineClient(binary);
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
        assertFalse(restricted.body().orElseThrow().toString().contains("customer list"));
        assertTrue(restricted.receipt().orElseThrow().decisions().get(0).reasonCodes()
                .contains("destination.remote_restricted"));

        GatewayPreview.EgressAdmission marked = admit(engine, root, "Classification: Confidential\nboard minutes",
                "openai", "https://api.openai.com");
        assertEquals("forward", marked.disposition());
        assertEquals("confidential", marked.classification().orElseThrow());
        assertEquals("remote", marked.receipt().orElseThrow().destination().locality());
    }

    private static GatewayPreview.EgressAdmission admit(SubprocessEngineClient engine, String root, String text,
                                                        String provider, String base) {
        Map<String, Object> body = Map.of("model", "m", "temperature", 0.2,
                "messages", List.of(Map.of("role", "user", "content", text)));
        return GatewayPreview.admitEgress(engine, root, new GatewayPreview.EgressRequest(provider, base, body));
    }
}
