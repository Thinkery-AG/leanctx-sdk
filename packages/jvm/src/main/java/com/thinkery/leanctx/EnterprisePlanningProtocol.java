package com.thinkery.leanctx;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeFormatterBuilder;
import java.time.format.ResolverStyle;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict wire validation for authenticated Enterprise source planning. */
final class EnterprisePlanningProtocol {
    static final int MAX_REQUEST_BYTES = 64 * 1024;
    static final int MAX_PLAN_RESPONSE_BYTES = 1024 * 1024;
    static final int MAX_MATERIALIZED_CONTENT_BYTES = 1024 * 1024;
    static final int MAX_MATERIALIZATION_RESPONSE_BYTES =
            MAX_PLAN_RESPONSE_BYTES + MAX_MATERIALIZED_CONTENT_BYTES;
    static final int MAX_SOURCE_IDS = 64;
    static final int MAX_PROTOCOL_ITEMS = 256;
    static final int MAX_IDENTIFIER_BYTES = 256;
    static final int MAX_REFERENCE_BYTES = 1024;
    static final int MAX_EXTENSION_BYTES = 64 * 1024;
    static final int MAX_EXTENSION_DEPTH = 8;
    static final int MAX_JSON_NESTING_DEPTH = 64;
    private static final BigInteger MAX_U64 = BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE);
    private static final Pattern UUID_PATTERN = Pattern.compile(
            "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}");
    private static final Pattern TIMESTAMP_PATTERN = Pattern.compile(
            "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z");
    private static final DateTimeFormatter TIMESTAMP_FORMAT = new DateTimeFormatterBuilder()
            .appendPattern("uuuu-MM-dd'T'HH:mm:ss'Z'")
            .toFormatter()
            .withResolverStyle(ResolverStyle.STRICT);
    private static final Set<String> SOURCE_TYPES = Set.of(
            "filesystem", "issue_tracker", "relational_database", "other");
    private static final Set<String> PERMISSIONS = Set.of("permitted", "denied", "unknown");
    private static final Set<String> CLASSIFICATIONS = Set.of(
            "Public", "Internal", "Confidential", "Restricted");
    private static final Set<String> DISPOSITIONS = Set.of("selected", "excluded", "deferred");
    private static final Set<String> REASON_CODES = Set.of(
            "relevant", "required", "cache_hit", "budget_exceeded", "lower_utility",
            "policy_excluded", "deferred_for_later", "other");
    private static final Set<String> EVIDENCE_KINDS = Set.of(
            "ProviderReceipt", "RuntimeLog", "SignedBatch", "QualityMeasurement", "ExperimentOutcome");
    private static final Set<String> SIGNATURE_STATUSES = Set.of("Verified", "Unverified", "NotSigned");
    private static final Set<String> PLAN_KEYS = Set.of(
            "schema_version", "context_plan_id", "task_id", "projection_digest", "budget_tokens",
            "selections", "provider_stats", "policy_decision_refs", "evidence");
    private static final Set<String> SELECTION_KEYS = Set.of(
            "source_ref", "provider", "disposition", "token_count", "sha256_digest",
            "reason_codes", "reason_detail");
    private static final Set<String> PROVIDER_STATS_KEYS = Set.of(
            "candidates_offered", "candidates_selected", "tokens_used");
    private static final Set<String> EVIDENCE_KEYS = Set.of(
            "schema_version", "kind", "uri", "digest", "signature_status", "media_type");
    private static final Set<String> DESCRIPTOR_KEYS = Set.of(
            "object_ref", "source_id", "source_type", "content_digest", "revision", "owner",
            "observed_at", "valid_until", "classification", "permission");

    private EnterprisePlanningProtocol() {
    }

    static boolean isBlankLikePython(String value) {
        return value.codePoints().allMatch(codePoint -> Character.isWhitespace(codePoint)
                || Character.isSpaceChar(codePoint) || codePoint == 0x85);
    }

    static byte[] canonicalBytesWithUnsignedU64(Object value) {
        return Json.canonicalBytesWithUnsignedU64(value);
    }

    static String inputText(String value, String field, int maximumBytes,
                            boolean controls, boolean nonblank) {
        if (value == null) {
            throw new ValidationError(field + " must be a string");
        }
        Json.validateUnicode(value, field);
        int length = value.getBytes(StandardCharsets.UTF_8).length;
        if (length == 0 || length > maximumBytes) {
            throw new ValidationError(field + " exceeds its UTF-8 byte bound");
        }
        if (value.indexOf('\0') >= 0 || (controls && value.codePoints().anyMatch(Character::isISOControl))) {
            throw new ValidationError(field + " contains a control character");
        }
        if (nonblank && isBlankLikePython(value)) {
            throw new ValidationError(field + " must not be blank");
        }
        return value;
    }

    static String inputUuid(String value, String field, boolean configuration) {
        try {
            return canonicalUuid(value, field);
        } catch (ValidationError error) {
            if (configuration) {
                throw new ConfigurationError(field + " must be a non-nil UUID");
            }
            throw error;
        }
    }

    static List<String> sourceIds(List<String> values) {
        if (values == null) {
            throw new ValidationError("source_ids must be a bounded sequence of UUID strings");
        }
        if (values.size() > MAX_SOURCE_IDS) {
            throw new ValidationError("source_ids exceeds the Engine source bound");
        }
        List<String> result = new ArrayList<>(values.size());
        Set<String> seen = new HashSet<>();
        for (String value : values) {
            String normalized = canonicalUuid(value, "source_id");
            if (!seen.add(normalized)) {
                throw new ValidationError("source_ids must not contain duplicates");
            }
            result.add(normalized);
        }
        return List.copyOf(result);
    }

    static BigInteger inputU64(BigInteger value, String field) {
        if (value == null || value.signum() < 0 || value.compareTo(MAX_U64) > 0) {
            throw new ValidationError(field + " must be an unsigned 64-bit integer");
        }
        return value;
    }

    static String inputDigest(String value, String field) {
        if (value == null) {
            throw new ValidationError(field + " must be a digest");
        }
        return Protocol.digest(value, field);
    }

    static String inputTimestamp(String value, String field) {
        String checked = inputText(value, field, MAX_IDENTIFIER_BYTES, true, true);
        if (!TIMESTAMP_PATTERN.matcher(checked).matches()) {
            throw new ValidationError(field + " must use canonical UTC timestamp syntax");
        }
        try {
            LocalDateTime dateTime = LocalDateTime.parse(checked, TIMESTAMP_FORMAT);
            if (dateTime.getYear() == 0) {
                throw new ValidationError(field + " contains an invalid date or time");
            }
        } catch (RuntimeException error) {
            if (error instanceof ValidationError validationError) {
                throw validationError;
            }
            throw new ValidationError(field + " contains an invalid date or time");
        }
        return checked;
    }

    static Map<String, Object> parsePlanResponse(byte[] raw, EnginePlanningRequest request,
                                                 List<String> sourceIds, String expectedTenant) {
        String label = "Enterprise Engine source-plan response";
        Map<String, Object> response = parseObject(raw, label, MAX_PLAN_RESPONSE_BYTES);
        exactKeys(response, Set.of("schema_version", "tenant_id", "governance_revision", "plan"), label);
        BigInteger schemaVersion = protocolU64(response.get("schema_version"), label + ".schema_version");
        requireSchema(schemaVersion, label + ".schema_version");
        String tenantId = protocolUuid(response.get("tenant_id"), label + ".tenant_id");
        if (!tenantId.equals(expectedTenant)) {
            throw protocol("Enterprise Engine source-plan response tenant binding does not match");
        }
        BigInteger governanceRevision = protocolU64(
                response.get("governance_revision"), label + ".governance_revision");
        Map<String, Object> plan = parseSourcePlan(response.get("plan"), request);
        validateSourceScope(plan, sourceIds);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", LeanCtx.SCHEMA_VERSION);
        result.put("tenant_id", tenantId);
        result.put("governance_revision", governanceRevision);
        result.put("plan", plan);
        return Json.immutableMapPreserving(result);
    }

    static Map<String, Object> parseMaterializationResponse(byte[] raw, EnginePlanningRequest request,
                                                            List<String> sourceIds,
                                                            BigInteger expectedGovernanceRevision,
                                                            String expectedBindingDigest,
                                                            String expectedTenant) {
        String label = "Enterprise Engine materialization response";
        Map<String, Object> response = parseObject(raw, label, MAX_MATERIALIZATION_RESPONSE_BYTES);
        exactKeys(response, Set.of(
                "schema_version", "tenant_id", "governance_revision", "materialization"), label);
        BigInteger schemaVersion = protocolU64(response.get("schema_version"), label + ".schema_version");
        requireSchema(schemaVersion, label + ".schema_version");
        String tenantId = protocolUuid(response.get("tenant_id"), label + ".tenant_id");
        if (!tenantId.equals(expectedTenant)) {
            throw protocol("Enterprise Engine materialization response tenant binding does not match");
        }
        BigInteger governanceRevision = protocolU64(
                response.get("governance_revision"), label + ".governance_revision");
        if (!governanceRevision.equals(expectedGovernanceRevision)) {
            throw protocol("Enterprise Engine materialization governance revision does not match");
        }
        Map<String, Object> materialization = Json.object(
                response.get("materialization"), label + ".materialization");
        exactKeys(materialization, Set.of("schema_version", "transport_version",
                "engine_interface_version", "plan", "materialized_digest",
                "materialized_token_count", "content"), "Enterprise Engine materialization");
        validateHeader(materialization, "Enterprise Engine materialization");
        Map<String, Object> plan = parseSourcePlan(materialization.get("plan"), request);
        validateSourceScope(plan, sourceIds);
        if (!expectedBindingDigest.equals(plan.get("binding_digest"))) {
            throw protocol("Enterprise Engine materialization binding digest does not match");
        }
        String materializedDigest = protocolDigest(
                materialization.get("materialized_digest"), "materialized_digest");
        BigInteger tokenCount = protocolU64(
                materialization.get("materialized_token_count"), "materialized_token_count");
        Map<String, Object> planResult = Json.object(plan.get("result"), "materialization.plan.result");
        Map<String, Object> planProjection = Json.object(
                planResult.get("plan"), "materialization.plan.result.plan");
        BigInteger budget = protocolU64(planProjection.get("budget_tokens"), "plan.budget_tokens");
        if (tokenCount.compareTo(budget) > 0) {
            throw protocol("Enterprise Engine materialized token metric exceeds the plan budget");
        }
        Object contentValue = materialization.get("content");
        if (!(contentValue instanceof String content)) {
            throw protocol("Enterprise Engine materialized content is not a string");
        }
        byte[] encoded = Json.utf8(content, "materialized content");
        if (encoded.length > MAX_MATERIALIZED_CONTENT_BYTES) {
            throw protocol("Enterprise Engine materialized content exceeds its byte bound");
        }
        if (!Protocol.sha256Digest(encoded).equals(materializedDigest)) {
            throw protocol("Enterprise Engine materialized content digest does not match");
        }
        Map<String, Object> normalizedMaterialization = new LinkedHashMap<>();
        normalizedMaterialization.put("schema_version", LeanCtx.SCHEMA_VERSION);
        normalizedMaterialization.put("transport_version", LeanCtx.TRANSPORT_VERSION);
        normalizedMaterialization.put("engine_interface_version", LeanCtx.ENGINE_INTERFACE_VERSION);
        normalizedMaterialization.put("plan", plan);
        normalizedMaterialization.put("materialized_digest", materializedDigest);
        normalizedMaterialization.put("materialized_token_count", tokenCount);
        normalizedMaterialization.put("content", content);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", LeanCtx.SCHEMA_VERSION);
        result.put("tenant_id", tenantId);
        result.put("governance_revision", governanceRevision);
        result.put("materialization", normalizedMaterialization);
        return Json.immutableMapPreserving(result);
    }

    private static Map<String, Object> parseSourcePlan(Object value, EnginePlanningRequest request) {
        Map<String, Object> sourcePlan = Json.object(value, "Engine source-plan response");
        exactKeys(sourcePlan, Set.of("result", "source_bindings", "binding_digest"),
                "Engine source-plan response");
        Map<String, Object> resultRaw = Json.object(sourcePlan.get("result"), "Engine source-plan result");
        exactKeys(resultRaw, Set.of(
                "schema_version", "transport_version", "engine_interface_version", "plan"),
                "Engine context-plan result");
        validateHeader(resultRaw, "Engine context-plan result");
        Map<String, Object> plan = parsePlan(resultRaw.get("plan"));
        if (!plan.containsKey("projection_digest")) {
            throw protocol("Engine source-plan response requires projection_digest");
        }
        if (!request.taskId().equals(plan.get("task_id"))) {
            throw protocol("Engine context-plan response task_id does not bind the request");
        }
        BigInteger returnedBudget = protocolU64(plan.get("budget_tokens"), "plan.budget_tokens");
        if (returnedBudget.compareTo(BigInteger.valueOf(request.budgetTokens())) > 0) {
            throw protocol("Engine context-plan response budget exceeds the request");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", LeanCtx.SCHEMA_VERSION);
        result.put("transport_version", LeanCtx.TRANSPORT_VERSION);
        result.put("engine_interface_version", LeanCtx.ENGINE_INTERFACE_VERSION);
        result.put("plan", plan);

        Object bindingsValue = sourcePlan.get("source_bindings");
        if (!(bindingsValue instanceof List<?> bindingValues)
                || bindingValues.size() > MAX_PROTOCOL_ITEMS) {
            throw protocol("source_bindings has an invalid shape");
        }
        List<Map<String, Object>> bindings = new ArrayList<>(bindingValues.size());
        String previousObjectRef = null;
        for (int index = 0; index < bindingValues.size(); index++) {
            Map<String, Object> binding = descriptor(
                    bindingValues.get(index), "source_bindings[" + index + "]");
            String objectRef = (String) binding.get("object_ref");
            if (previousObjectRef != null && compareCodePoints(previousObjectRef, objectRef) >= 0) {
                throw protocol("source_bindings must be strictly sorted by object_ref");
            }
            previousObjectRef = objectRef;
            bindings.add(binding);
        }
        Map<String, Object> planBody = Json.object(result.get("plan"), "Engine plan");
        Object selectionValue = planBody.get("selections");
        if (!(selectionValue instanceof List<?> selectionValues)) {
            throw protocol("plan.selections has an invalid shape");
        }
        List<Map<String, Object>> selected = new ArrayList<>();
        for (Object item : selectionValues) {
            Map<String, Object> selection = Json.object(item, "plan selection");
            if ("selected".equals(selection.get("disposition"))) {
                selected.add(selection);
            }
        }
        if (selected.size() != bindings.size()) {
            throw protocol("source_bindings do not match selected plan entries");
        }
        for (Map<String, Object> binding : bindings) {
            boolean matched = selected.stream().anyMatch(selection ->
                    selection.get("source_ref").equals(binding.get("object_ref"))
                            && selection.get("provider").equals(binding.get("source_id"))
                            && java.util.Objects.equals(selection.get("sha256_digest"),
                                    binding.get("content_digest")));
            if (!matched) {
                throw protocol("source binding does not match a selected plan entry");
            }
        }
        String bindingDigest = protocolDigest(sourcePlan.get("binding_digest"), "binding_digest");
        String expectedDigest = sha256Canonical(List.of(result, bindings), "source bindings");
        if (!bindingDigest.equals(expectedDigest)) {
            throw protocol("binding_digest does not match canonical source bindings");
        }
        Map<String, Object> normalized = new LinkedHashMap<>();
        normalized.put("result", result);
        normalized.put("source_bindings", bindings);
        normalized.put("binding_digest", bindingDigest);
        return normalized;
    }

    private static Map<String, Object> parsePlan(Object value) {
        Map<String, Object> raw = Json.object(value, "plan");
        Map<String, Object> extensions = extensions(raw, PLAN_KEYS);
        for (String required : List.of(
                "schema_version", "context_plan_id", "task_id", "budget_tokens", "selections")) {
            if (!raw.containsKey(required)) {
                throw protocol("plan is missing a required field");
            }
        }
        requireSchema(protocolU64(raw.get("schema_version"), "plan.schema_version"),
                "plan.schema_version");
        Object selectionValue = raw.get("selections");
        if (!(selectionValue instanceof List<?> selectionRaw)
                || selectionRaw.size() > MAX_PROTOCOL_ITEMS) {
            throw protocol("plan.selections has an invalid shape");
        }
        List<Map<String, Object>> selections = new ArrayList<>(selectionRaw.size());
        Set<String> refs = new HashSet<>();
        BigInteger selectedTokens = BigInteger.ZERO;
        for (int index = 0; index < selectionRaw.size(); index++) {
            Map<String, Object> selection = selection(
                    selectionRaw.get(index), "plan.selections[" + index + "]");
            if (!refs.add((String) selection.get("source_ref"))) {
                throw protocol("plan.selections contains duplicate source_ref values");
            }
            if ("selected".equals(selection.get("disposition"))) {
                selectedTokens = selectedTokens.add((BigInteger) selection.get("token_count"));
            }
            selections.add(selection);
        }
        BigInteger budget = protocolU64(raw.get("budget_tokens"), "plan.budget_tokens");
        if (selectedTokens.compareTo(budget) > 0 || selectedTokens.compareTo(MAX_U64) > 0) {
            throw protocol("plan selected context exceeds budget_tokens");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", LeanCtx.SCHEMA_VERSION);
        result.put("context_plan_id", protocolIdentifier(raw.get("context_plan_id"), "plan.context_plan_id"));
        result.put("task_id", protocolIdentifier(raw.get("task_id"), "plan.task_id"));
        result.put("budget_tokens", budget);
        result.put("selections", selections);

        if (raw.containsKey("projection_digest") && raw.get("projection_digest") != null) {
            String projectionDigest = protocolProjectionDigest(raw.get("projection_digest"),
                    "plan.projection_digest");
            result.put("projection_digest", projectionDigest);
        }
        if (raw.containsKey("provider_stats")) {
            Map<String, Object> providerStatsRaw = Json.object(raw.get("provider_stats"), "plan.provider_stats");
            if (providerStatsRaw.size() > MAX_PROTOCOL_ITEMS) {
                throw protocol("plan.provider_stats exceeds its item bound");
            }
            Map<String, Object> providerStats = new LinkedHashMap<>();
            for (Map.Entry<String, Object> entry : providerStatsRaw.entrySet()) {
                String provider = protocolIdentifier(entry.getKey(), "plan.provider_stats key");
                Map<String, Object> stats = Json.object(
                        entry.getValue(), "plan.provider_stats[" + provider + "]");
                exactKeys(stats, PROVIDER_STATS_KEYS, "plan.provider_stats entry");
                BigInteger offered = protocolU64(stats.get("candidates_offered"),
                        "plan.provider_stats.candidates_offered");
                BigInteger chosen = protocolU64(stats.get("candidates_selected"),
                        "plan.provider_stats.candidates_selected");
                if (chosen.compareTo(offered) > 0) {
                    throw protocol("plan.provider_stats selected exceeds offered");
                }
                Map<String, Object> normalizedStats = new LinkedHashMap<>();
                normalizedStats.put("candidates_offered", offered);
                normalizedStats.put("candidates_selected", chosen);
                normalizedStats.put("tokens_used", protocolU64(
                        stats.get("tokens_used"), "plan.provider_stats.tokens_used"));
                providerStats.put(provider, normalizedStats);
            }
            if (!providerStats.isEmpty()) {
                result.put("provider_stats", providerStats);
            }
        }
        if (raw.containsKey("policy_decision_refs")) {
            Object refsValue = raw.get("policy_decision_refs");
            if (!(refsValue instanceof List<?> policyRefs) || policyRefs.size() > MAX_PROTOCOL_ITEMS) {
                throw protocol("plan.policy_decision_refs has an invalid shape");
            }
            List<String> normalizedRefs = new ArrayList<>(policyRefs.size());
            Set<String> seenRefs = new HashSet<>();
            for (Object ref : policyRefs) {
                String checked = protocolIdentifier(ref, "plan.policy_decision_refs");
                if (!seenRefs.add(checked)) {
                    throw protocol("plan.policy_decision_refs contains duplicates");
                }
                normalizedRefs.add(checked);
            }
            if (!normalizedRefs.isEmpty()) {
                result.put("policy_decision_refs", normalizedRefs);
            }
        }
        if (raw.containsKey("evidence")) {
            Object evidenceValue = raw.get("evidence");
            if (!(evidenceValue instanceof List<?> evidenceRaw)
                    || evidenceRaw.size() > MAX_PROTOCOL_ITEMS) {
                throw protocol("plan.evidence has an invalid shape");
            }
            List<Map<String, Object>> evidence = new ArrayList<>(evidenceRaw.size());
            for (int index = 0; index < evidenceRaw.size(); index++) {
                evidence.add(evidence(evidenceRaw.get(index), "plan.evidence[" + index + "]"));
            }
            if (!evidence.isEmpty()) {
                result.put("evidence", evidence);
            }
        }
        result.putAll(extensions);
        if (result.containsKey("projection_digest")) {
            Map<String, Object> unsigned = new LinkedHashMap<>(result);
            unsigned.remove("projection_digest");
            String expected = sha256Canonical(unsigned, "plan projection");
            if (!expected.equals(result.get("projection_digest"))) {
                throw protocol("plan.projection_digest does not match canonical projection content");
            }
        }
        return result;
    }

    private static Map<String, Object> selection(Object value, String field) {
        Map<String, Object> raw = Json.object(value, field);
        allowedKeys(raw, SELECTION_KEYS, Set.of(
                "source_ref", "provider", "disposition", "token_count", "reason_codes"), field);
        String sourceRef = protocolIdentifier(raw.get("source_ref"), field + ".source_ref");
        String provider = protocolIdentifier(raw.get("provider"), field + ".provider");
        String disposition = protocolEnum(raw.get("disposition"), field + ".disposition", DISPOSITIONS);
        BigInteger tokenCount = protocolU64(raw.get("token_count"), field + ".token_count");
        Object reasonsValue = raw.get("reason_codes");
        if (!(reasonsValue instanceof List<?> reasonsRaw) || reasonsRaw.isEmpty()
                || reasonsRaw.size() > MAX_PROTOCOL_ITEMS) {
            throw protocol(field + ".reason_codes has an invalid shape");
        }
        List<String> reasons = new ArrayList<>(reasonsRaw.size());
        Set<String> seen = new HashSet<>();
        for (Object reasonValue : reasonsRaw) {
            String reason = protocolEnum(reasonValue, field + ".reason_codes", REASON_CODES);
            if (!seen.add(reason)) {
                throw protocol(field + ".reason_codes contains duplicates");
            }
            reasons.add(reason);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("source_ref", sourceRef);
        result.put("provider", provider);
        result.put("disposition", disposition);
        result.put("token_count", tokenCount);
        result.put("reason_codes", reasons);
        if (raw.get("sha256_digest") != null) {
            result.put("sha256_digest", protocolProjectionDigest(
                    raw.get("sha256_digest"), field + ".sha256_digest"));
        }
        if (raw.get("reason_detail") != null) {
            result.put("reason_detail", protocolIdentifier(raw.get("reason_detail"), field + ".reason_detail"));
        }
        return result;
    }

    private static Map<String, Object> evidence(Object value, String field) {
        Map<String, Object> raw = Json.object(value, field);
        Map<String, Object> extra = extensions(raw, EVIDENCE_KEYS);
        for (String required : List.of("kind", "uri", "digest", "signature_status")) {
            if (!raw.containsKey(required)) {
                throw protocol(field + " is missing a required field");
            }
        }
        Map<String, Object> result = new LinkedHashMap<>();
        if (raw.get("schema_version") != null) {
            requireSchema(protocolU64(raw.get("schema_version"), field + ".schema_version"),
                    field + ".schema_version");
            result.put("schema_version", 1);
        }
        result.put("kind", protocolEnum(raw.get("kind"), field + ".kind", EVIDENCE_KINDS));
        result.put("uri", protocolIdentifier(raw.get("uri"), field + ".uri"));
        String digest = protocolIdentifier(raw.get("digest"), field + ".digest");
        if (result.containsKey("schema_version")) {
            String candidate = digest.startsWith("sha256:") ? digest.substring(7)
                    : digest.startsWith("blake3:") ? digest.substring(7) : digest;
            if (!candidate.matches("[0-9a-fA-F]{64}")) {
                throw protocol(field + ".digest is not a supported versioned digest");
            }
        }
        result.put("digest", digest);
        result.put("signature_status", protocolEnum(
                raw.get("signature_status"), field + ".signature_status", SIGNATURE_STATUSES));
        if (raw.get("media_type") != null) {
            result.put("media_type", protocolIdentifier(raw.get("media_type"), field + ".media_type"));
        }
        result.putAll(extra);
        return result;
    }

    private static Map<String, Object> descriptor(Object value, String field) {
        Map<String, Object> raw = Json.object(value, field);
        allowedKeys(raw, DESCRIPTOR_KEYS, Set.of(
                "object_ref", "source_id", "source_type", "content_digest"), field);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("object_ref", protocolReference(raw.get("object_ref"), field + ".object_ref"));
        result.put("source_id", protocolIdentifier(raw.get("source_id"), field + ".source_id"));
        result.put("source_type", protocolEnum(raw.get("source_type"), field + ".source_type", SOURCE_TYPES));
        result.put("content_digest", protocolDigest(raw.get("content_digest"), field + ".content_digest"));
        result.put("revision", optionalReference(raw.get("revision"), field + ".revision"));
        result.put("owner", optionalReference(raw.get("owner"), field + ".owner"));
        result.put("observed_at", optionalTimestamp(raw.get("observed_at"), field + ".observed_at"));
        result.put("valid_until", optionalTimestamp(raw.get("valid_until"), field + ".valid_until"));
        result.put("classification", raw.get("classification") == null ? null
                : protocolEnum(raw.get("classification"), field + ".classification", CLASSIFICATIONS));
        result.put("permission", protocolEnum(raw.containsKey("permission")
                ? raw.get("permission") : "unknown", field + ".permission", PERMISSIONS));
        String observed = (String) result.get("observed_at");
        String validUntil = (String) result.get("valid_until");
        if (observed != null && validUntil != null && compareCodePoints(validUntil, observed) <= 0) {
            throw protocol(field + " validity window is inverted");
        }
        return result;
    }

    private static void validateSourceScope(Map<String, Object> plan, List<String> sourceIds) {
        Set<String> requested = new HashSet<>(sourceIds);
        Map<String, Object> result = Json.object(plan.get("result"), "plan.result");
        Map<String, Object> projection = Json.object(result.get("plan"), "plan.result.plan");
        for (Object item : (List<?>) projection.get("selections")) {
            Map<String, Object> selection = Json.object(item, "plan selection");
            if (!requested.contains(selection.get("source_ref"))
                    || !requested.contains(selection.get("provider"))) {
                throw protocol("Enterprise Engine selection is outside requested sources");
            }
        }
        for (Object item : (List<?>) plan.get("source_bindings")) {
            Map<String, Object> binding = Json.object(item, "source binding");
            if (!requested.contains(binding.get("object_ref"))
                    || !requested.contains(binding.get("source_id"))) {
                throw protocol("Enterprise Engine source binding is outside requested sources");
            }
            if (!"permitted".equals(binding.get("permission"))) {
                throw protocol("Enterprise Engine selected source is not permitted");
            }
        }
    }

    private static Map<String, Object> extensions(Map<String, Object> value, Set<String> reserved) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : value.entrySet()) {
            if (reserved.contains(entry.getKey())) {
                continue;
            }
            protocolIdentifier(entry.getKey(), "extension key");
            extensionValue(entry.getValue(), 0);
            result.put(entry.getKey(), entry.getValue());
        }
        if (result.size() > MAX_PROTOCOL_ITEMS) {
            throw protocol("extensions exceed their field bound");
        }
        return result;
    }

    private static void extensionValue(Object value, int depth) {
        if (depth > MAX_EXTENSION_DEPTH) {
            throw protocol("extension value exceeds its nesting bound");
        }
        if (value instanceof Map<?, ?> map) {
            if (map.size() > MAX_PROTOCOL_ITEMS) {
                throw protocol("extension object exceeds its item bound");
            }
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String key)) {
                    throw protocol("extension object has a non-string key");
                }
                protocolIdentifier(key, "extension object key");
                extensionValue(entry.getValue(), depth + 1);
            }
        } else if (value instanceof List<?> list) {
            if (list.size() > MAX_PROTOCOL_ITEMS) {
                throw protocol("extension array exceeds its item bound");
            }
            for (Object item : list) {
                extensionValue(item, depth + 1);
            }
        } else if (value instanceof String string) {
            if (Json.utf8(string, "extension string").length > MAX_EXTENSION_BYTES) {
                throw protocol("extension string exceeds its byte bound");
            }
        }
        byte[] encoded = canonicalProtocolBytes(value, "extension value");
        if (encoded.length > MAX_EXTENSION_BYTES) {
            throw protocol("extension value exceeds its serialized byte bound");
        }
    }

    private static Map<String, Object> parseObject(byte[] raw, String label, int maximumBytes) {
        if (raw == null || raw.length > maximumBytes) {
            throw protocol(label + " exceeds its byte bound");
        }
        return Json.object(Json.parse(raw, label, MAX_JSON_NESTING_DEPTH), label);
    }

    private static void validateHeader(Map<String, Object> value, String field) {
        requireSchema(protocolU64(value.get("schema_version"), field + ".schema_version"),
                field + ".schema_version");
        requireSchema(protocolU64(value.get("transport_version"), field + ".transport_version"),
                field + ".transport_version");
        Object version = value.get("engine_interface_version");
        if (!(version instanceof String string) || !LeanCtx.ENGINE_INTERFACE_VERSION.equals(string)) {
            throw protocol(field + ".engine_interface_version is unsupported");
        }
    }

    private static void requireSchema(BigInteger value, String field) {
        if (!BigInteger.ONE.equals(value)) {
            throw protocol(field + " is unsupported");
        }
    }

    private static BigInteger protocolU64(Object value, String field) {
        BigInteger integer;
        if (value instanceof BigInteger bigInteger) {
            integer = bigInteger;
        } else if (value instanceof Byte || value instanceof Short || value instanceof Integer
                || value instanceof Long) {
            integer = BigInteger.valueOf(((Number) value).longValue());
        } else if (value instanceof BigDecimal) {
            throw protocol(field + " must be an unsigned integer");
        } else {
            throw protocol(field + " must be an unsigned integer");
        }
        if (integer.signum() < 0 || integer.compareTo(MAX_U64) > 0) {
            throw protocol(field + " must be an unsigned 64-bit integer");
        }
        return integer;
    }

    private static String protocolUuid(Object value, String field) {
        if (!(value instanceof String text)) {
            throw protocol(field + " must be a canonical UUID");
        }
        try {
            return canonicalUuid(text, field);
        } catch (ValidationError error) {
            throw protocol(field + " must be a non-nil canonical UUID");
        }
    }

    private static String canonicalUuid(String value, String field) {
        if (value == null || !UUID_PATTERN.matcher(value).matches()) {
            throw new ValidationError(field + " must be a canonical UUID");
        }
        String normalized = value.toLowerCase(java.util.Locale.ROOT);
        if (normalized.equals("00000000-0000-0000-0000-000000000000")) {
            throw new ValidationError(field + " must be a non-nil canonical UUID");
        }
        return normalized;
    }

    private static String protocolIdentifier(Object value, String field) {
        return protocolText(value, field, MAX_IDENTIFIER_BYTES, true, true);
    }

    private static String protocolReference(Object value, String field) {
        return protocolText(value, field, MAX_REFERENCE_BYTES, true, true);
    }

    private static String optionalReference(Object value, String field) {
        return value == null ? null : protocolReference(value, field);
    }

    private static String protocolText(Object value, String field, int maximumBytes,
                                       boolean controls, boolean nonblank) {
        if (!(value instanceof String text)) {
            throw protocol(field + " must be a string");
        }
        Json.validateUnicode(text, field);
        int length = text.getBytes(StandardCharsets.UTF_8).length;
        if (length == 0 || length > maximumBytes || text.indexOf('\0') >= 0
                || (controls && text.codePoints().anyMatch(Character::isISOControl))) {
            throw protocol(field + " violates its bound");
        }
        if (nonblank && isBlankLikePython(text)) {
            throw protocol(field + " must not be blank");
        }
        return text;
    }

    private static String protocolEnum(Object value, String field, Set<String> allowed) {
        if (!(value instanceof String text) || !allowed.contains(text)) {
            throw protocol(field + " has an unsupported value");
        }
        return text;
    }

    private static String protocolTimestamp(Object value, String field) {
        String text = protocolText(value, field, MAX_IDENTIFIER_BYTES, true, true);
        if (!TIMESTAMP_PATTERN.matcher(text).matches()) {
            throw protocol(field + " must use canonical UTC timestamp syntax");
        }
        try {
            LocalDateTime dateTime = LocalDateTime.parse(text, TIMESTAMP_FORMAT);
            if (dateTime.getYear() == 0) {
                throw protocol(field + " contains an invalid date or time");
            }
        } catch (RuntimeException error) {
            if (error instanceof EngineProtocolError protocolError) {
                throw protocolError;
            }
            throw protocol(field + " contains an invalid date or time");
        }
        return text;
    }

    private static String optionalTimestamp(Object value, String field) {
        return value == null ? null : protocolTimestamp(value, field);
    }

    private static String protocolDigest(Object value, String field) {
        if (!(value instanceof String text)) {
            throw protocol(field + " must be a digest");
        }
        if (!Protocol.DIGEST.matcher(text).matches()) {
            throw protocol(field + " must be sha256:<64 lowercase hex>");
        }
        return text;
    }

    private static String protocolProjectionDigest(Object value, String field) {
        if (!(value instanceof String text)) {
            throw protocol(field + " must contain a 64-digit hexadecimal digest");
        }
        String candidate = text.startsWith("sha256:") ? text.substring(7) : text;
        if (!candidate.matches("[0-9a-fA-F]{64}")) {
            throw protocol(field + " must contain a 64-digit hexadecimal digest");
        }
        return text;
    }

    private static void allowedKeys(Map<String, Object> value, Set<String> allowed,
                                    Set<String> required, String field) {
        for (String key : value.keySet()) {
            if (!allowed.contains(key)) {
                throw protocol(field + " fields do not match the v1 contract");
            }
        }
        for (String key : required) {
            if (!value.containsKey(key)) {
                throw protocol(field + " is missing a required field");
            }
        }
    }

    private static void exactKeys(Map<String, Object> value, Set<String> expected, String field) {
        if (!value.keySet().equals(expected)) {
            throw protocol(field + " fields do not match the v1 contract");
        }
    }

    private static String sha256Canonical(Object value, String label) {
        return Protocol.sha256Digest(canonicalProtocolBytes(value, label));
    }

    private static byte[] canonicalProtocolBytes(Object value, String label) {
        try {
            return canonicalBytesWithUnsignedU64(value);
        } catch (ValidationError error) {
            throw new EngineProtocolError(label + " is not canonical JSON", error);
        }
    }

    private static int compareCodePoints(String left, String right) {
        int[] a = left.codePoints().toArray();
        int[] b = right.codePoints().toArray();
        int length = Math.min(a.length, b.length);
        for (int index = 0; index < length; index++) {
            if (a[index] != b[index]) {
                return Integer.compare(a[index], b[index]);
            }
        }
        return Integer.compare(a.length, b.length);
    }

    private static EngineProtocolError protocol(String message) {
        return new EngineProtocolError(message);
    }
}
