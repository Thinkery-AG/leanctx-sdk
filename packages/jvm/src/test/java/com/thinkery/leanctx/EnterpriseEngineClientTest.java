package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

/** Local loopback transport contract tests; these do not exercise an installed Engine or remote tenant. */
class EnterpriseEngineClientTest {
    private static final String TENANT = "00000000-0000-0000-0000-000000000010";
    private static final String OTHER_TENANT = "00000000-0000-0000-0000-000000000099";
    private static final String SOURCE = "00000000-0000-0000-0000-000000000011";
    private static final String OTHER_SOURCE = "00000000-0000-0000-0000-000000000012";
    private static final String CREDENTIAL = "local-contract-token";

    @Test
    void contextPlanAndMaterializationUseAuthenticatedBoundedContracts() throws Exception {
        EnginePlanningRequest planning = planningRequest();
        Map<String, Object> sourcePlan = sourcePlan(planning, SOURCE, "permitted");
        String content = "prepared issue context\n";
        Map<String, Object> materialization = materializationResponse(
                TENANT, BigInteger.valueOf(7), sourcePlan, content, BigInteger.valueOf(24));
        try (LocalServer server = new LocalServer(request -> request.path().equals(
                "/v1/engine/context-plan")
                ? json(200, sourcePlanResponse(TENANT, BigInteger.valueOf(7), sourcePlan))
                : json(200, materialization))) {
            EnterpriseEngineClient client = client(server, Duration.ofSeconds(2));
            Map<String, Object> planned = client.contextPlan(planning, List.of(SOURCE));
            assertEquals(TENANT, planned.get("tenant_id"));
            assertEquals(BigInteger.valueOf(7), planned.get("governance_revision"));
            Map<String, Object> canonicalPlan = object(planned.get("plan"));
            assertTrue(((String) canonicalPlan.get("binding_digest")).matches("sha256:[0-9a-f]{64}"));
            assertEquals("preserve", object(object(canonicalPlan.get("result")).get("plan"))
                    .get("x_contract_note"));

            Map<String, Object> materialized = client.contextMaterialize(planning, List.of(SOURCE),
                    7L, (String) canonicalPlan.get("binding_digest"), "2026-02-03T04:05:06Z");
            assertEquals(TENANT, materialized.get("tenant_id"));
            Map<String, Object> body = object(materialized.get("materialization"));
            assertEquals(content, body.get("content"));
            assertEquals(Protocol.sha256Digest(content), body.get("materialized_digest"));

            List<Request> requests = server.requests();
            assertEquals(2, requests.size());
            assertEquals("POST", requests.get(0).method());
            assertEquals("/v1/engine/context-plan", requests.get(0).path());
            assertEquals("/v1/engine/context-materialize", requests.get(1).path());
            for (Request captured : requests) {
                assertEquals("Bearer " + CREDENTIAL, captured.headers().get("authorization"));
                assertEquals("application/json", captured.headers().get("content-type"));
                Map<String, Object> requestBody = object(Json.parse(captured.body(), "captured request"));
                assertFalse(requestBody.containsKey("tenant_id"), "tenant must stay an expected response binding");
            }
            Map<String, Object> materializeBody = object(Json.parse(requests.get(1).body(), "captured request"));
            assertEquals(BigInteger.valueOf(7), materializeBody.get("expected_governance_revision"));
            assertEquals("2026-02-03T04:05:06Z", materializeBody.get("planning_evaluation_time"));
        }
    }

    @Test
    void requestValidationRejectsInvalidSourceAndBindingValuesBeforeNetwork() throws Exception {
        try (LocalServer server = new LocalServer(ignored -> json(200, Map.of()))) {
            EnterpriseEngineClient client = client(server, Duration.ofSeconds(2));
            assertThrows(ValidationError.class,
                    () -> client.contextPlan(planningRequest(), List.of(SOURCE, SOURCE)));
            assertThrows(ValidationError.class,
                    () -> client.contextPlan(planningRequest(), List.of("00000000-0000-0000-0000-000000000000")));
            List<String> tooManySources = new ArrayList<>();
            for (int index = 0; index < 65; index++) {
                tooManySources.add(String.format(Locale.ROOT,
                        "00000000-0000-0000-0000-%012d", index + 100));
            }
            assertThrows(ValidationError.class,
                    () -> client.contextPlan(planningRequest(), tooManySources));
            assertThrows(ValidationError.class,
                    () -> client.contextMaterialize(planningRequest(), List.of(SOURCE), -1L,
                            "sha256:" + "a".repeat(64)));
            assertThrows(ValidationError.class,
                    () -> client.contextMaterialize(planningRequest(), List.of(SOURCE), 7L,
                            "wrong-digest"));
            assertThrows(ValidationError.class,
                    () -> client.contextMaterialize(planningRequest(), List.of(SOURCE), 7L,
                            "sha256:" + "a".repeat(64), "2026-02-30T04:05:06Z"));
            assertEquals(0, server.callCount());
        }
    }

    @Test
    void queryBlankValidationMatchesPythonUnicodeWhitespace() {
        assertThrows(ValidationError.class,
                () -> new EnginePlanningRequest("gitlab-issue-42", "\u00a0", 1));
        assertThrows(ValidationError.class,
                () -> new EnginePlanningRequest("gitlab-issue-42", "\u0085", 1));
    }

    @Test
    void acceptsTheMaximumNumberOfDistinctSourceIds() throws Exception {
        EnginePlanningRequest request = planningRequest();
        List<String> sourceIds = new ArrayList<>();
        sourceIds.add(SOURCE);
        for (int index = 0; index < 63; index++) {
            sourceIds.add(String.format(Locale.ROOT,
                    "00000000-0000-0000-0000-%012d", index + 100));
        }
        Map<String, Object> sourcePlan = sourcePlan(request, SOURCE, "permitted");
        try (LocalServer server = new LocalServer(ignored -> json(
                200, sourcePlanResponse(TENANT, BigInteger.valueOf(7), sourcePlan)))) {
            EnterpriseEngineClient client = client(server, Duration.ofSeconds(2));
            client.contextPlan(request, sourceIds);
            Map<String, Object> requestBody = object(Json.parse(
                    server.requests().getFirst().body(), "captured request"));
            assertEquals(64, ((List<?>) requestBody.get("source_ids")).size());
        }
    }

    @Test
    void acceptsProjectionDigestFormsForUnselectedSourceEntries() throws Exception {
        EnginePlanningRequest request = planningRequest();
        Map<String, Object> plan = sourcePlanWithUnselectedProjectionDigest(request);
        try (LocalServer server = new LocalServer(ignored -> json(
                200, sourcePlanResponse(TENANT, BigInteger.valueOf(7), plan)))) {
            Map<String, Object> response = client(server, Duration.ofSeconds(2))
                    .contextPlan(request, List.of(SOURCE, OTHER_SOURCE));
            Map<String, Object> projection = object(object(object(response.get("plan"))
                    .get("result")).get("plan"));
            List<?> selections = (List<?>) projection.get("selections");
            assertEquals("A".repeat(64), object(selections.get(1)).get("sha256_digest"));
        }
    }

    @Test
    void responseValidationChecksTenantTaskSourceGovernanceAndDigests() throws Exception {
        EnginePlanningRequest request = planningRequest();
        List<Map<String, Object>> invalidPlans = List.of(
                sourcePlan(request, SOURCE, "denied"),
                sourcePlan(request, OTHER_SOURCE, "permitted"),
                sourcePlan(new EnginePlanningRequest("different-task", request.query(), 64), SOURCE, "permitted"),
                corruptedProjectionDigest(sourcePlan(request, SOURCE, "permitted")),
                corruptedBindingDigest(sourcePlan(request, SOURCE, "permitted")),
                unsupportedEngineVersion(sourcePlan(request, SOURCE, "permitted")));
        for (Map<String, Object> plan : invalidPlans) {
            try (LocalServer server = new LocalServer(ignored -> json(
                    200, sourcePlanResponse(TENANT, BigInteger.valueOf(7), plan)))) {
                assertThrows(EngineProtocolError.class,
                        () -> client(server, Duration.ofSeconds(2)).contextPlan(request, List.of(SOURCE)));
            }
        }
        Map<String, Object> validPlan = sourcePlan(request, SOURCE, "permitted");
        Map<String, Object> mismatchedBinding = sourcePlanWithMismatchedSelectedBinding(request);
        try (LocalServer server = new LocalServer(ignored -> json(
                200, sourcePlanResponse(TENANT, BigInteger.valueOf(7), mismatchedBinding)))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2))
                            .contextPlan(request, List.of(SOURCE, OTHER_SOURCE)));
        }
        try (LocalServer server = new LocalServer(ignored -> json(
                200, sourcePlanResponse(OTHER_TENANT, BigInteger.valueOf(7), validPlan)))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextPlan(request, List.of(SOURCE)));
        }
        try (LocalServer server = new LocalServer(ignored -> new Response(200,
                ("{\"schema_version\":1.0,\"tenant_id\":\"" + TENANT
                        + "\",\"governance_revision\":7,\"plan\":{}}").getBytes(StandardCharsets.UTF_8),
                Map.of()))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextPlan(request, List.of(SOURCE)));
        }
        try (LocalServer server = new LocalServer(ignored -> new Response(200,
                ("{\"" + CREDENTIAL + "\":1,\"" + CREDENTIAL + "\":2}")
                        .getBytes(StandardCharsets.UTF_8), Map.of()))) {
            EngineProtocolError error = assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextPlan(request, List.of(SOURCE)));
            assertFalse(error.getMessage().contains(CREDENTIAL));
        }

        Map<String, Object> goodMaterialization = materializationResponse(
                TENANT, BigInteger.valueOf(7), validPlan, "prepared", BigInteger.valueOf(20));
        Map<String, Object> badGovernance = replaceTopLevel(goodMaterialization,
                "governance_revision", BigInteger.valueOf(8));
        try (LocalServer server = new LocalServer(ignored -> json(200, badGovernance))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextMaterialize(request,
                            List.of(SOURCE), 7L, bindingDigest(validPlan)));
        }
        try (LocalServer server = new LocalServer(ignored -> json(200, goodMaterialization))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextMaterialize(request,
                            List.of(SOURCE), 7L, "sha256:" + "d".repeat(64)));
        }
        Map<String, Object> badMaterializedDigest = badMaterializationField(
                goodMaterialization, "materialized_digest", "sha256:" + "b".repeat(64));
        try (LocalServer server = new LocalServer(ignored -> json(200, badMaterializedDigest))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextMaterialize(request,
                            List.of(SOURCE), 7L, bindingDigest(validPlan)));
        }
        Map<String, Object> overBudget = materializationResponse(
                TENANT, BigInteger.valueOf(7), validPlan, "prepared", BigInteger.valueOf(65));
        try (LocalServer server = new LocalServer(ignored -> json(200, overBudget))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextMaterialize(request,
                            List.of(SOURCE), 7L, bindingDigest(validPlan)));
        }
    }

    @Test
    void materializationSupportsUnsignedGovernanceAndNormalizedDigestBindings() throws Exception {
        EnginePlanningRequest request = planningRequest();
        Map<String, Object> sourcePlan = sourcePlanWithU64Statistics(request, SOURCE);
        String expectedBindingDigest = bindingDigest(sourcePlan);
        BigInteger aboveSafeInteger = BigInteger.valueOf(Json.MAX_SAFE_INTEGER).add(BigInteger.ONE);
        BigInteger maxU64 = BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE);
        BigInteger beyondU64 = maxU64.add(BigInteger.ONE);
        assertThrows(ValidationError.class,
                () -> Json.canonicalBytes(Map.of("value", aboveSafeInteger)));
        assertThrows(ValidationError.class,
                () -> EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(Map.of("value", beyondU64)));
        assertEquals("{\"value\":" + aboveSafeInteger + "}", new String(
                EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(
                        Map.of("value", aboveSafeInteger)), StandardCharsets.UTF_8));

        for (BigInteger governanceRevision : List.of(aboveSafeInteger, maxU64)) {
            try (LocalServer server = new LocalServer(ignored -> json(200,
                    materializationResponse(TENANT, governanceRevision, sourcePlan,
                            "prepared", BigInteger.valueOf(20))))) {
                Map<String, Object> response = client(server, Duration.ofSeconds(2))
                        .contextMaterialize(request, List.of(SOURCE), governanceRevision, expectedBindingDigest);
                assertEquals(governanceRevision, response.get("governance_revision"));
                Map<String, Object> returnedSourcePlan = object(
                        object(response.get("materialization")).get("plan"));
                assertEquals(expectedBindingDigest, returnedSourcePlan.get("binding_digest"));
                Map<String, Object> returnedProjection = object(
                        object(returnedSourcePlan.get("result")).get("plan"));
                Map<String, Object> returnedStats = object(
                        object(returnedProjection.get("provider_stats")).get(SOURCE));
                assertEquals(aboveSafeInteger, returnedStats.get("candidates_selected"));
                assertEquals(maxU64, returnedStats.get("tokens_used"));

                Request captured = server.requests().getFirst();
                Map<String, Object> requestBody = object(Json.parse(captured.body(), "captured request"));
                assertEquals(governanceRevision, requestBody.get("expected_governance_revision"));
                assertEquals(expectedBindingDigest, requestBody.get("expected_binding_digest"));
            }
        }
    }

    @Test
    void responseParsingRejectsExcessiveNestingAsProtocolError() throws Exception {
        int depth = EnterprisePlanningProtocol.MAX_JSON_NESTING_DEPTH + 32;
        String nested = "[".repeat(depth) + "0" + "]".repeat(depth);
        String response = "{\"schema_version\":1,\"tenant_id\":\"" + TENANT
                + "\",\"governance_revision\":7,\"plan\":{\"x_nested\":" + nested + "}}";
        try (LocalServer server = new LocalServer(ignored -> new Response(
                200, response.getBytes(StandardCharsets.UTF_8), Map.of()))) {
            EngineProtocolError error = assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2))
                            .contextPlan(planningRequest(), List.of(SOURCE)));
            assertTrue(error.getMessage().contains("nesting depth exceeds its bound"));
        }
    }

    @Test
    void materializedContentAndHttpResponsesAreBounded() throws Exception {
        EnginePlanningRequest planning = planningRequest();
        Map<String, Object> plan = sourcePlan(planning, SOURCE, "permitted");
        try (LocalServer server = new LocalServer(ignored -> new Response(200,
                "x".repeat(EnterprisePlanningProtocol.MAX_PLAN_RESPONSE_BYTES + 1)
                        .getBytes(StandardCharsets.UTF_8), Map.of()))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(5))
                            .contextPlan(planning, List.of(SOURCE)));
        }
        Map<String, Object> tooMuchContent = materializationResponse(
                TENANT, BigInteger.valueOf(7), plan,
                "x".repeat(EnterprisePlanningProtocol.MAX_MATERIALIZED_CONTENT_BYTES + 1),
                BigInteger.ONE);
        try (LocalServer server = new LocalServer(ignored -> json(200, tooMuchContent))) {
            assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(4)).contextMaterialize(planning,
                            List.of(SOURCE), 7L, bindingDigest(plan)));
        }
        try (LocalServer server = new LocalServer(ignored -> new Response(200,
                "x".repeat(EnterprisePlanningProtocol.MAX_MATERIALIZATION_RESPONSE_BYTES + 1)
                        .getBytes(StandardCharsets.UTF_8), Map.of()))) {
            EngineProtocolError error = assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(4)).contextMaterialize(planning,
                            List.of(SOURCE), 7L, bindingDigest(plan)));
            assertFalse(error.getMessage().contains(CREDENTIAL));
        }
    }

    @Test
    void transportRejectsRedirectsMapsStatusesAndEnforcesDeadline() throws Exception {
        try (LocalServer destination = new LocalServer(ignored -> json(200, Map.of()));
             LocalServer server = new LocalServer(ignored -> new Response(
                     302, new byte[0], Map.of("Location", destination.baseUrl() + "/redirect-target")))) {
            EngineProtocolError error = assertThrows(EngineProtocolError.class,
                    () -> client(server, Duration.ofSeconds(2)).contextPlan(planningRequest(), List.of(SOURCE)));
            assertTrue(error.getMessage().contains("redirects are not followed"));
            assertEquals(1, server.callCount());
            assertEquals(0, destination.callCount(), "redirect must not receive the bearer credential");
        }
        for (int status : List.of(401, 403, 503)) {
            try (LocalServer server = new LocalServer(ignored -> json(status, Map.of("error", "local test")))) {
                if (status == 401) {
                    EngineRejected error = assertThrows(EngineRejected.class,
                            () -> client(server, Duration.ofSeconds(2))
                                    .contextPlan(planningRequest(), List.of(SOURCE)));
                    assertFalse(error.getMessage().contains(CREDENTIAL));
                } else if (status == 403) {
                    PolicyAdmissionError error = assertThrows(PolicyAdmissionError.class,
                            () -> client(server, Duration.ofSeconds(2))
                                    .contextPlan(planningRequest(), List.of(SOURCE)));
                    assertFalse(error.getMessage().contains(CREDENTIAL));
                } else {
                    EngineUnavailable error = assertThrows(EngineUnavailable.class,
                            () -> client(server, Duration.ofSeconds(2))
                                    .contextPlan(planningRequest(), List.of(SOURCE)));
                    assertFalse(error.getMessage().contains(CREDENTIAL));
                }
            }
        }
        try (LocalServer server = new LocalServer(ignored -> {
            try {
                Thread.sleep(500);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
            }
            return json(200, sourcePlanResponse(TENANT, BigInteger.valueOf(7),
                    sourcePlan(planningRequest(), SOURCE, "permitted")));
        })) {
            assertThrows(EngineTimeout.class,
                    () -> client(server, Duration.ofMillis(120))
                            .contextPlan(planningRequest(), List.of(SOURCE)));
        }
        try (LocalServer server = new LocalServer(ignored -> new Response(
                200, "x".repeat(8192).getBytes(StandardCharsets.UTF_8), Map.of(), 30))) {
            assertThrows(EngineTimeout.class,
                    () -> client(server, Duration.ofMillis(120))
                            .contextPlan(planningRequest(), List.of(SOURCE)));
        }
    }

    @Test
    void httpRequiresExplicitLiteralLoopbackOptInAndVersionsStayFixed() throws Exception {
        assertThrows(ConfigurationError.class, () -> new EnterpriseEngineClient(
                "http://127.0.0.1:1234", CREDENTIAL, TENANT));
        assertThrows(ConfigurationError.class, () -> new EnterpriseEngineClient(
                "http://example.test", CREDENTIAL, TENANT, Duration.ofSeconds(2), true));
        assertThrows(ConfigurationError.class, () -> new EnterpriseEngineClient(
                "http://localhost:1234", CREDENTIAL, TENANT, Duration.ofSeconds(2), true));
        assertThrows(ConfigurationError.class, () -> new EnterpriseEngineClient(
                "https://user@engine.example", CREDENTIAL, TENANT));
        assertThrows(ConfigurationError.class, () -> new EnterpriseEngineClient(
                "https://engine.example", CREDENTIAL, TENANT, Duration.ofMillis(99)));
        try (LocalServer server = new LocalServer(ignored -> json(
                200, sourcePlanResponse(TENANT, BigInteger.valueOf(7),
                        sourcePlan(planningRequest(), SOURCE, "permitted"))))) {
            EnterpriseEngineClient client = client(server, Duration.ofSeconds(2));
            assertEquals(TENANT, client.tenantId());
            Map<String, Object> result = client.contextPlan(planningRequest(), List.of(SOURCE));
            assertEquals(1, result.get("schema_version"));
            Map<String, Object> plan = object(object(result.get("plan")).get("result"));
            assertEquals(1, plan.get("schema_version"));
            assertEquals(1, plan.get("transport_version"));
            assertEquals(LeanCtx.ENGINE_INTERFACE_VERSION, plan.get("engine_interface_version"));
        }
    }

    private static EnginePlanningRequest planningRequest() {
        return new EnginePlanningRequest("gitlab-issue-42", "inspect issue references", 64);
    }

    private static EnterpriseEngineClient client(LocalServer server, Duration timeout) {
        return server.register(new EnterpriseEngineClient(
                server.baseUrl(), CREDENTIAL, TENANT, timeout, true));
    }

    private static Map<String, Object> sourcePlan(EnginePlanningRequest request,
                                                  String sourceId, String permission) {
        String contentDigest = "sha256:" + "c".repeat(64);
        Map<String, Object> selection = new LinkedHashMap<>();
        selection.put("source_ref", sourceId);
        selection.put("provider", sourceId);
        selection.put("disposition", "selected");
        selection.put("token_count", 32);
        selection.put("sha256_digest", contentDigest);
        selection.put("reason_codes", List.of("relevant"));
        Map<String, Object> plan = new LinkedHashMap<>();
        plan.put("schema_version", 1);
        plan.put("context_plan_id", "context-plan-gitlab");
        plan.put("task_id", request.taskId());
        plan.put("budget_tokens", request.budgetTokens());
        plan.put("selections", List.of(selection));
        plan.put("x_contract_note", "preserve");
        plan.put("projection_digest", Protocol.sha256Digest(Json.canonicalBytes(plan)));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", 1);
        result.put("transport_version", 1);
        result.put("engine_interface_version", LeanCtx.ENGINE_INTERFACE_VERSION);
        result.put("plan", plan);
        Map<String, Object> binding = new LinkedHashMap<>();
        binding.put("object_ref", sourceId);
        binding.put("source_id", sourceId);
        binding.put("source_type", "issue_tracker");
        binding.put("content_digest", contentDigest);
        binding.put("revision", null);
        binding.put("owner", "team-gitlab");
        binding.put("observed_at", null);
        binding.put("valid_until", null);
        binding.put("classification", "Internal");
        binding.put("permission", permission);
        List<Map<String, Object>> bindings = List.of(binding);
        Map<String, Object> sourcePlan = new LinkedHashMap<>();
        sourcePlan.put("result", result);
        sourcePlan.put("source_bindings", bindings);
        sourcePlan.put("binding_digest", Protocol.sha256Digest(
                Json.canonicalBytes(List.of(result, bindings))));
        return sourcePlan;
    }

    private static Map<String, Object> sourcePlanWithU64Statistics(
            EnginePlanningRequest request, String sourceId) {
        Map<String, Object> sourcePlan = sourcePlan(request, sourceId, "permitted");
        Map<String, Object> projection = object(object(sourcePlan.get("result")).get("plan"));
        BigInteger aboveSafeInteger = BigInteger.valueOf(Json.MAX_SAFE_INTEGER).add(BigInteger.ONE);
        BigInteger maxU64 = BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE);
        Map<String, Object> stats = new LinkedHashMap<>();
        stats.put("candidates_offered", maxU64);
        stats.put("candidates_selected", aboveSafeInteger);
        stats.put("tokens_used", maxU64);
        projection.put("provider_stats", Map.of(sourceId, stats));
        projection.remove("projection_digest");
        projection.put("projection_digest", Protocol.sha256Digest(
                EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(projection)));
        sourcePlan.put("binding_digest", Protocol.sha256Digest(
                EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(
                        List.of(sourcePlan.get("result"), sourcePlan.get("source_bindings")))));
        return sourcePlan;
    }

    private static Map<String, Object> corruptedProjectionDigest(Map<String, Object> sourcePlan) {
        Map<String, Object> copy = deepCopy(sourcePlan);
        Map<String, Object> plan = object(object(copy.get("result")).get("plan"));
        plan.put("projection_digest", "sha256:" + "b".repeat(64));
        return copy;
    }

    private static Map<String, Object> sourcePlanWithUnselectedProjectionDigest(
            EnginePlanningRequest request) {
        Map<String, Object> copy = deepCopy(sourcePlan(request, SOURCE, "permitted"));
        Map<String, Object> plan = object(object(copy.get("result")).get("plan"));
        Map<String, Object> unselected = new LinkedHashMap<>();
        unselected.put("source_ref", OTHER_SOURCE);
        unselected.put("provider", OTHER_SOURCE);
        unselected.put("disposition", "excluded");
        unselected.put("token_count", 0);
        unselected.put("sha256_digest", "A".repeat(64));
        unselected.put("reason_codes", List.of("lower_utility"));
        List<Object> selections = new ArrayList<>((List<?>) plan.get("selections"));
        selections.add(unselected);
        plan.put("selections", selections);
        plan.remove("projection_digest");
        plan.put("projection_digest", Protocol.sha256Digest(Json.canonicalBytes(plan)));
        copy.put("binding_digest", Protocol.sha256Digest(
                Json.canonicalBytes(List.of(copy.get("result"), copy.get("source_bindings")))));
        return copy;
    }

    private static Map<String, Object> sourcePlanWithMismatchedSelectedBinding(
            EnginePlanningRequest request) {
        Map<String, Object> copy = deepCopy(sourcePlan(request, SOURCE, "permitted"));
        List<Object> bindings = new ArrayList<>((List<?>) copy.get("source_bindings"));
        object(bindings.get(0)).put("source_id", OTHER_SOURCE);
        copy.put("source_bindings", bindings);
        copy.put("binding_digest", Protocol.sha256Digest(
                Json.canonicalBytes(List.of(copy.get("result"), bindings))));
        return copy;
    }

    private static Map<String, Object> corruptedBindingDigest(Map<String, Object> sourcePlan) {
        Map<String, Object> copy = deepCopy(sourcePlan);
        copy.put("binding_digest", "sha256:" + "b".repeat(64));
        return copy;
    }

    private static Map<String, Object> unsupportedEngineVersion(Map<String, Object> sourcePlan) {
        Map<String, Object> copy = deepCopy(sourcePlan);
        object(copy.get("result")).put("engine_interface_version", "9.9.9");
        return copy;
    }

    private static String bindingDigest(Map<String, Object> sourcePlan) {
        return (String) sourcePlan.get("binding_digest");
    }

    private static Map<String, Object> sourcePlanResponse(String tenant, BigInteger governanceRevision,
                                                           Map<String, Object> sourcePlan) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("schema_version", 1);
        response.put("tenant_id", tenant);
        response.put("governance_revision", governanceRevision);
        response.put("plan", sourcePlan);
        return response;
    }

    private static Map<String, Object> materializationResponse(String tenant,
                                                               BigInteger governanceRevision,
                                                               Map<String, Object> sourcePlan,
                                                               String content,
                                                               BigInteger tokenCount) {
        Map<String, Object> materialization = new LinkedHashMap<>();
        materialization.put("schema_version", 1);
        materialization.put("transport_version", 1);
        materialization.put("engine_interface_version", LeanCtx.ENGINE_INTERFACE_VERSION);
        materialization.put("plan", sourcePlan);
        materialization.put("materialized_digest", Protocol.sha256Digest(content));
        materialization.put("materialized_token_count", tokenCount);
        materialization.put("content", content);
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("schema_version", 1);
        response.put("tenant_id", tenant);
        response.put("governance_revision", governanceRevision);
        response.put("materialization", materialization);
        return response;
    }

    private static Map<String, Object> badMaterializationField(Map<String, Object> response,
                                                               String key, Object value) {
        Map<String, Object> copy = deepCopy(response);
        object(copy.get("materialization")).put(key, value);
        return copy;
    }

    private static Map<String, Object> replaceTopLevel(Map<String, Object> response,
                                                       String key, Object value) {
        Map<String, Object> copy = deepCopy(response);
        copy.put(key, value);
        return copy;
    }

    private static Map<String, Object> deepCopy(Map<String, Object> value) {
        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : value.entrySet()) {
            copy.put(entry.getKey(), mutableCopy(entry.getValue()));
        }
        return copy;
    }

    private static Object mutableCopy(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> copy = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                copy.put((String) entry.getKey(), mutableCopy(entry.getValue()));
            }
            return copy;
        }
        if (value instanceof List<?> list) {
            List<Object> copy = new ArrayList<>(list.size());
            for (Object item : list) {
                copy.add(mutableCopy(item));
            }
            return copy;
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value) {
        return (Map<String, Object>) value;
    }

    private static Response json(int status, Map<String, Object> value) {
        return new Response(status,
                EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(value), Map.of());
    }

    private record Request(String method, String path, Map<String, String> headers, byte[] body) {
    }

    private record Response(int status, byte[] body, Map<String, String> headers,
                            int chunkDelayMillis) {
        private Response(int status, byte[] body, Map<String, String> headers) {
            this(status, body, headers, 0);
        }
    }

    @FunctionalInterface
    private interface Handler {
        Response respond(Request request);
    }

    private static final class LocalServer implements AutoCloseable {
        private final ServerSocket listener;
        private final Handler handler;
        private final AtomicInteger calls = new AtomicInteger();
        private final BlockingQueue<Request> received = new LinkedBlockingQueue<>();
        private final List<EnterpriseEngineClient> clients = new CopyOnWriteArrayList<>();
        private final Thread thread;

        private LocalServer(Handler handler) throws IOException {
            this.listener = new ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"));
            this.handler = handler;
            this.thread = new Thread(this::serve, "enterprise-client-local-http-contract");
            this.thread.setDaemon(true);
            this.thread.start();
        }

        private String baseUrl() {
            return "http://127.0.0.1:" + listener.getLocalPort();
        }

        private int callCount() {
            return calls.get();
        }

        private EnterpriseEngineClient register(EnterpriseEngineClient client) {
            clients.add(client);
            return client;
        }

        private List<Request> requests() {
            List<Request> result = new ArrayList<>();
            received.drainTo(result);
            return result;
        }

        private void serve() {
            while (!listener.isClosed()) {
                try (Socket socket = listener.accept()) {
                    socket.setSoTimeout(3000);
                    Request request = readRequest(socket.getInputStream());
                    calls.incrementAndGet();
                    received.add(request);
                    writeResponse(socket.getOutputStream(), handler.respond(request));
                } catch (IOException error) {
                    if (!listener.isClosed()) {
                        // A timed-out or deliberately cancelled local request can close early.
                    }
                } catch (RuntimeException error) {
                    if (!listener.isClosed()) {
                        throw error;
                    }
                }
            }
        }

        @Override
        public void close() throws Exception {
            listener.close();
            clients.forEach(EnterpriseEngineClient::close);
            thread.join(1500);
        }

        private static Request readRequest(InputStream input) throws IOException {
            ByteArrayOutputStream headerBytes = new ByteArrayOutputStream();
            int matched = 0;
            while (headerBytes.size() < 32 * 1024) {
                int next = input.read();
                if (next < 0) {
                    throw new IOException("local test request ended before headers");
                }
                headerBytes.write(next);
                matched = switch (matched) {
                    case 0 -> next == '\r' ? 1 : 0;
                    case 1 -> next == '\n' ? 2 : next == '\r' ? 1 : 0;
                    case 2 -> next == '\r' ? 3 : 0;
                    case 3 -> next == '\n' ? 4 : 0;
                    default -> matched;
                };
                if (matched == 4) {
                    break;
                }
            }
            if (matched != 4) {
                throw new IOException("local test request headers exceeded their bound");
            }
            String[] lines = headerBytes.toString(StandardCharsets.ISO_8859_1).split("\\r\\n");
            String[] requestLine = lines[0].split(" ", 3);
            Map<String, String> headers = new LinkedHashMap<>();
            for (int index = 1; index < lines.length; index++) {
                int separator = lines[index].indexOf(':');
                if (separator > 0) {
                    headers.put(lines[index].substring(0, separator).toLowerCase(Locale.ROOT),
                            lines[index].substring(separator + 1).trim());
                }
            }
            int contentLength = Integer.parseInt(headers.getOrDefault("content-length", "0"));
            byte[] body = input.readNBytes(contentLength);
            if (body.length != contentLength) {
                throw new IOException("local test request body was truncated");
            }
            return new Request(requestLine[0], requestLine[1], Map.copyOf(headers), body);
        }

        private static void writeResponse(OutputStream output, Response response) throws IOException {
            String reason = switch (response.status()) {
                case 200 -> "OK";
                case 302 -> "Found";
                case 401 -> "Unauthorized";
                case 403 -> "Forbidden";
                case 503 -> "Service Unavailable";
                default -> "Test Response";
            };
            StringBuilder headers = new StringBuilder("HTTP/1.1 ")
                    .append(response.status()).append(' ').append(reason).append("\r\n")
                    .append("Content-Type: application/json\r\n")
                    .append("Content-Length: ").append(response.body().length).append("\r\n")
                    .append("Connection: close\r\n");
            for (Map.Entry<String, String> header : response.headers().entrySet()) {
                headers.append(header.getKey()).append(": ").append(header.getValue()).append("\r\n");
            }
            headers.append("\r\n");
            output.write(headers.toString().getBytes(StandardCharsets.ISO_8859_1));
            output.flush();
            for (int offset = 0; offset < response.body().length; offset += 1024) {
                int size = Math.min(1024, response.body().length - offset);
                output.write(response.body(), offset, size);
                output.flush();
                if (response.chunkDelayMillis() > 0) {
                    try {
                        Thread.sleep(response.chunkDelayMillis());
                    } catch (InterruptedException error) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }
        }
    }
}
