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
import java.util.Optional;
import java.util.function.Function;
import org.junit.jupiter.api.Test;

class ContextStorePreviewTest {
    private static final Path FIXTURES = Path.of("../../fixtures/context-store-preview-v1");

    private static Object load(String kind, String validity, String name) throws IOException {
        return Json.parse(Files.readAllBytes(FIXTURES.resolve(kind).resolve(validity).resolve(name + ".json")),
                "fixture");
    }

    @SuppressWarnings("unchecked")
    private static List<Object> names(String kind, String validity) throws IOException {
        Map<String, Object> manifest =
                (Map<String, Object>) Json.parse(Files.readAllBytes(FIXTURES.resolve("manifest.json")), "manifest");
        Map<String, Object> documents = (Map<String, Object>) manifest.get("documents");
        return (List<Object>) ((Map<String, Object>) documents.get(kind)).get(validity);
    }

    @Test
    void everyValidDocumentParsesAndEveryInvalidOneIsRejected() throws IOException {
        Map<String, Function<Object, Object>> parsers = Map.of(
                "evidence", ContextStorePreview::parsePolicyEvidence,
                "lineage", ContextStorePreview::parseTaskLineage);
        for (Map.Entry<String, Function<Object, Object>> parser : parsers.entrySet()) {
            String kind = parser.getKey();
            for (Object name : names(kind, "valid")) {
                parser.getValue().apply(load(kind, "valid", (String) name));
            }
            for (Object name : names(kind, "invalid")) {
                Object document = load(kind, "invalid", (String) name);
                assertThrows(EngineProtocolError.class, () -> parser.getValue().apply(document),
                        kind + "/" + name);
            }
        }
    }

    @Test
    void unmeasuredNeverReadsAsMeasured() throws IOException {
        ContextStorePreview.ContextPolicyEvidence evidence =
                ContextStorePreview.parsePolicyEvidence(load("evidence", "valid", "rich"));
        ContextStorePreview.StrategyOutcomeRecord unmeasured = evidence.records().get(1);
        assertFalse(unmeasured.quality().measured());
        assertTrue(unmeasured.quality().retained().isEmpty());
        assertTrue(unmeasured.security().regressions().isEmpty());
        assertEquals(Optional.of(0L), evidence.records().get(0).security().regressions());
        ContextStorePreview.TaskLineage gapped =
                ContextStorePreview.parseTaskLineage(load("lineage", "valid", "gapped"));
        assertFalse(gapped.isComplete());
        assertTrue(gapped.deliveries().get(0).summary().isEmpty());
    }

    @Test
    void liveEngineFreshScopeAndUnknownTask() throws IOException {
        String binary = System.getenv("LEANCTX_ENGINE_BINARY");
        assumeTrue(binary != null && !binary.isBlank(), "needs a real Engine");
        SubprocessEngineClient engine = new SubprocessEngineClient(binary);
        // Private Engine storage refuses symlinked ancestors (macOS /var).
        String root = Files.createTempDirectory("leanctx-store-").toRealPath().toString();
        ContextStorePreview.ContextPolicyEvidence evidence = ContextStorePreview.readPolicyEvidence(
                engine, root, ContextStorePreview.Scope.project("sdk-preview-fresh"));
        assertTrue(evidence.records().isEmpty() && evidence.evaluations().isEmpty());
        ContextStorePreview.TaskLineage lineage = ContextStorePreview.readTaskLineage(engine, root,
                "sdk-preview-unknown-task",
                new ContextStorePreview.Scope(Optional.of("sdk-preview-fresh"), Optional.of("tenant-a")));
        assertEquals("unknown", lineage.outcome());
        assertTrue(lineage.gaps().contains("no_plan_recorded"));
        assertTrue(lineage.scope().orElse("").contains("tenant-a"));
    }
}
