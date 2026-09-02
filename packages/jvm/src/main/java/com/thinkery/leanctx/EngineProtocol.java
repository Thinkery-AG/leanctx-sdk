package com.thinkery.leanctx;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Internal strict parser for the Engine Interface v1 response contract. */
final class EngineProtocol {
    private static final Set<String> TOP_LEVEL = Set.of(
            "schema_version", "transport_version", "engine_interface_version",
            "view", "invocation", "observation", "recovery");
    private static final Set<String> VIEW = Set.of("text", "output_ref", "output_digest");
    private static final Set<String> RECOVERY = Set.of("recovery_ref", "source_ref", "source_digest");
    private static final Set<String> INVOCATION = Set.of(
            "schema_version", "invocation_id", "engine", "operation", "input_ref",
            "input_digest", "source_refs", "policy_admission");
    private static final Set<String> ENGINE = Set.of("engine_id", "engine_version");
    private static final Set<String> OPERATION = Set.of("capability_id", "capability_version");
    private static final Set<String> POLICY = Set.of("policy_ref", "decision");
    private static final Set<String> OBSERVATION = Set.of(
            "schema_version", "invocation_id", "status", "output_ref", "output_digest",
            "source_lineage", "measurements", "failure", "receipt_link");
    private static final Set<String> OBSERVATION_REQUIRED = Set.of(
            "schema_version", "invocation_id", "status", "source_lineage", "measurements");
    private static final Set<String> MEASUREMENT = Set.of("name", "unit", "classification", "value");
    private static final Set<String> FAILURE = Set.of("code", "retryable_by_host", "recovery_ref");
    private static final Set<String> RECEIPT_LINK = Set.of(
            "schema_version", "receipt_id", "receipt_ref", "receipt_digest", "invocation_id");

    private EngineProtocol() {
    }

    static ParsedResponse parseResponse(byte[] raw) {
        if (raw == null || raw.length > Protocol.MAX_RESPONSE_BYTES) {
            throw new EngineProtocolError("Engine response exceeds the bound");
        }
        Map<String, Object> top = Json.object(Json.parse(raw, "Engine response"), "Engine response");
        exact(top, TOP_LEVEL, "Engine response");
        if (Json.integer(top.get("schema_version"), "response.schema_version")
                != LeanCtx.SCHEMA_VERSION) {
            throw new CompatibilityError("unsupported schema version");
        }
        if (Json.integer(top.get("transport_version"), "response.transport_version")
                != LeanCtx.TRANSPORT_VERSION) {
            throw new CompatibilityError("unsupported transport version");
        }
        String interfaceVersion = Json.string(top.get("engine_interface_version"),
                "response.engine_interface_version", Protocol.MAX_REF_BYTES);
        if (!LeanCtx.ENGINE_INTERFACE_VERSION.equals(interfaceVersion)) {
            throw new CompatibilityError("unsupported Engine Interface version");
        }
        ParsedView view = parseView(top.get("view"));
        RecoveryBinding recovery = parseRecovery(top.get("recovery"));
        Object invocationValue = top.get("invocation");
        Object observationValue = top.get("observation");
        if (invocationValue == null || observationValue == null) {
            if (invocationValue != null || observationValue != null) {
                throw protocol("invocation and observation must both be null or present");
            }
            return new ParsedResponse(view, null, recovery);
        }
        Map<String, Object> invocation = parseInvocation(invocationValue);
        Map<String, Object> observation = parseObservation(observationValue,
                (String) invocation.get("invocation_id"));
        List<?> lineage = list(observation.get("source_lineage"), "observation.source_lineage");
        List<?> sourceRefs = list(invocation.get("source_refs"), "invocation.source_refs");
        if (!lineage.equals(sourceRefs)) {
            throw protocol("observation source lineage does not match invocation");
        }
        if (!java.util.Objects.equals(observation.get("output_ref"), view.outputRef)
                || !java.util.Objects.equals(observation.get("output_digest"), view.outputDigest)) {
            throw protocol("view and observation output binding mismatch");
        }
        return new ParsedResponse(view, new Records(invocation, observation), recovery);
    }

    private static ParsedView parseView(Object value) {
        Map<String, Object> item = Json.object(value, "view");
        exact(item, VIEW, "view");
        String text = Json.string(item.get("text"), "view.text", Protocol.MAX_TEXT_BYTES);
        String outputRef = Json.optionalOutputRef(item.get("output_ref"), "view.output_ref");
        String outputDigest = Json.optionalDigest(item.get("output_digest"), "view.output_digest");
        validatePair(outputRef, outputDigest, "view");
        if (outputDigest != null && !Protocol.sha256Digest(text).equals(outputDigest)) {
            throw protocol("view output digest mismatch");
        }
        return new ParsedView(text, outputRef, outputDigest);
    }

    private static RecoveryBinding parseRecovery(Object value) {
        Map<String, Object> item = Json.object(value, "recovery");
        exact(item, RECOVERY, "recovery");
        String digest = Json.optionalDigest(item.get("source_digest"), "recovery.source_digest");
        if (digest == null) {
            throw protocol("recovery.source_digest is required");
        }
        return new RecoveryBinding(
                Json.requiredRef(item.get("recovery_ref"), "recovery.recovery_ref"),
                Json.requiredRef(item.get("source_ref"), "recovery.source_ref"), digest);
    }

    private static Map<String, Object> parseInvocation(Object value) {
        Map<String, Object> item = Json.object(value, "invocation");
        exact(item, INVOCATION, "invocation");
        if (Json.integer(item.get("schema_version"), "invocation.schema_version")
                != LeanCtx.SCHEMA_VERSION) {
            throw protocol("unsupported invocation schema version");
        }
        Map<String, Object> engine = Json.object(item.get("engine"), "invocation.engine");
        Map<String, Object> operation = Json.object(item.get("operation"), "invocation.operation");
        Map<String, Object> policy = Json.object(item.get("policy_admission"),
                "invocation.policy_admission");
        exact(engine, ENGINE, "invocation.engine");
        exact(operation, OPERATION, "invocation.operation");
        exact(policy, POLICY, "invocation.policy_admission");
        String engineId = Json.string(engine.get("engine_id"), "invocation.engine.engine_id",
                Protocol.MAX_REF_BYTES);
        String engineVersion = Json.string(engine.get("engine_version"),
                "invocation.engine.engine_version", Protocol.MAX_REF_BYTES);
        if (!"lean-ctx-local".equals(engineId) || !Protocol.SEMVER.matcher(engineVersion).matches()
                || !engineVersion.startsWith("3.")) {
            throw new UnsupportedEngineError("unsupported Engine identity");
        }
        String capabilityId = Json.string(operation.get("capability_id"),
                "invocation.operation.capability_id", Protocol.MAX_REF_BYTES);
        String capabilityVersion = Json.string(operation.get("capability_version"),
                "invocation.operation.capability_version", Protocol.MAX_REF_BYTES);
        if (!"capability://leanctx/context-optimization".equals(capabilityId)
                || !"1.0.0".equals(capabilityVersion)) {
            throw new UnsupportedEngineError("unsupported Engine capability");
        }
        String decision = Json.string(policy.get("decision"),
                "invocation.policy_admission.decision", Protocol.MAX_REF_BYTES);
        if (!"admitted".equals(decision) && !"rejected".equals(decision)) {
            throw protocol("unknown policy decision");
        }
        String policyRef = Json.requiredRef(policy.get("policy_ref"),
                "invocation.policy_admission.policy_ref");
        String invocationId = Json.string(item.get("invocation_id"),
                "invocation.invocation_id", Protocol.MAX_REF_BYTES);
        String inputRef = Json.requiredRef(item.get("input_ref"), "invocation.input_ref");
        String inputDigest = Json.optionalDigest(item.get("input_digest"), "invocation.input_digest");
        if (inputDigest == null) {
            throw protocol("invocation.input_digest is required");
        }
        List<?> rawRefs = list(item.get("source_refs"), "invocation.source_refs");
        if (rawRefs.isEmpty() || rawRefs.size() > Protocol.MAX_REFS) {
            throw protocol("invocation.source_refs exceeds its bound");
        }
        List<String> refs = new ArrayList<>();
        for (Object rawRef : rawRefs) {
            refs.add(Json.requiredRef(rawRef, "invocation.source_refs"));
        }
        if (new HashSet<>(refs).size() != refs.size()) {
            throw protocol("invocation.source_refs contains duplicates");
        }
        if (!refs.contains(inputRef)) {
            throw protocol("invocation input_ref is not in source_refs");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", LeanCtx.SCHEMA_VERSION);
        result.put("invocation_id", invocationId);
        result.put("engine", Map.of("engine_id", engineId, "engine_version", engineVersion));
        result.put("operation", Map.of("capability_id", capabilityId,
                "capability_version", capabilityVersion));
        result.put("input_ref", inputRef);
        result.put("input_digest", inputDigest);
        result.put("source_refs", List.copyOf(refs));
        result.put("policy_admission", Map.of("policy_ref", policyRef, "decision", decision));
        return Collections.unmodifiableMap(result);
    }

    private static Map<String, Object> parseObservation(Object value, String invocationId) {
        Map<String, Object> item = Json.object(value, "observation");
        if (!item.keySet().containsAll(OBSERVATION_REQUIRED)
                || !OBSERVATION.containsAll(item.keySet())) {
            throw protocol("observation fields do not match the v1 contract");
        }
        String observedId = Json.string(item.get("invocation_id"),
                "observation.invocation_id", Protocol.MAX_REF_BYTES);
        if (!observedId.equals(invocationId)) {
            throw protocol("observation invocation binding mismatch");
        }
        if (Json.integer(item.get("schema_version"), "observation.schema_version")
                != LeanCtx.SCHEMA_VERSION) {
            throw protocol("unsupported observation schema version");
        }
        String status = Json.string(item.get("status"), "observation.status", Protocol.MAX_REF_BYTES);
        try {
            Protocol.checkStatus(status);
        } catch (ValidationError exception) {
            throw protocol(exception.getMessage());
        }
        String outputRef = Json.optionalOutputRef(item.get("output_ref"), "observation.output_ref");
        String outputDigest = Json.optionalDigest(item.get("output_digest"),
                "observation.output_digest");
        validatePair(outputRef, outputDigest, "observation");
        List<?> rawLineage = list(item.get("source_lineage"), "observation.source_lineage");
        if (rawLineage.isEmpty() || rawLineage.size() > Protocol.MAX_REFS) {
            throw protocol("observation.source_lineage exceeds its bound");
        }
        List<String> lineage = new ArrayList<>();
        for (Object entry : rawLineage) {
            lineage.add(Json.requiredRef(entry, "observation.source_lineage"));
        }
        if (new HashSet<>(lineage).size() != lineage.size()) {
            throw protocol("observation.source_lineage contains duplicates");
        }
        List<?> rawMeasurements = list(item.get("measurements"), "observation.measurements");
        if (rawMeasurements.size() > Protocol.MAX_MEASUREMENTS) {
            throw protocol("observation.measurements exceeds its bound");
        }
        List<ContextMeasurement> measurements = new ArrayList<>();
        for (Object measurement : rawMeasurements) {
            measurements.add(parseMeasurement(measurement));
        }
        ContextFailure failure = parseFailure(item.get("failure"));
        ContextReceiptLink receiptLink = parseReceiptLink(item.get("receipt_link"), invocationId);
        if (("succeeded".equals(status) || "degraded".equals(status)) && failure != null) {
            throw protocol("successful/degraded observation cannot contain failure");
        }
        if (("failed".equals(status) || "rejected".equals(status)) && failure == null) {
            throw protocol("failed/rejected observation requires failure");
        }
        if ("succeeded".equals(status) && receiptLink == null) {
            throw protocol("succeeded observation requires receipt_link");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", LeanCtx.SCHEMA_VERSION);
        result.put("invocation_id", observedId);
        result.put("status", status);
        result.put("output_ref", outputRef);
        result.put("output_digest", outputDigest);
        result.put("source_lineage", List.copyOf(lineage));
        result.put("measurements", List.copyOf(measurements));
        result.put("failure", failure);
        result.put("receipt_link", receiptLink);
        return Collections.unmodifiableMap(result);
    }

    private static ContextMeasurement parseMeasurement(Object value) {
        Map<String, Object> item = Json.object(value, "measurement");
        exact(item, MEASUREMENT, "measurement");
        String name = Json.string(item.get("name"), "measurement.name", Protocol.MAX_REF_BYTES);
        String unit = Json.string(item.get("unit"), "measurement.unit", Protocol.MAX_REF_BYTES);
        String classification = Json.string(item.get("classification"),
                "measurement.classification", Protocol.MAX_REF_BYTES);
        Object rawValue = item.get("value");
        try {
            if ("unavailable".equals(classification)) {
                if (rawValue != null) {
                    throw new ValidationError("unavailable measurement value must be null");
                }
                return new ContextMeasurement(name, unit, classification, (Long) null);
            }
            return new ContextMeasurement(name, unit, classification,
                    Json.integer(rawValue, "measurement.value"));
        } catch (ValidationError exception) {
            throw protocol(exception.getMessage());
        }
    }

    private static ContextFailure parseFailure(Object value) {
        if (value == null) {
            return null;
        }
        Map<String, Object> item = Json.object(value, "failure");
        exact(item, FAILURE, "failure");
        String code = Json.string(item.get("code"), "failure.code", Protocol.MAX_REF_BYTES);
        boolean retryable = booleanValue(item.get("retryable_by_host"),
                "failure.retryable_by_host");
        String recoveryRef = item.get("recovery_ref") == null ? null
                : Json.requiredRef(item.get("recovery_ref"), "failure.recovery_ref");
        try {
            return new ContextFailure(code, retryable, recoveryRef);
        } catch (ValidationError exception) {
            throw protocol(exception.getMessage());
        }
    }

    private static ContextReceiptLink parseReceiptLink(Object value, String invocationId) {
        if (value == null) {
            return null;
        }
        Map<String, Object> item = Json.object(value, "receipt_link");
        exact(item, RECEIPT_LINK, "receipt_link");
        String digest = Json.optionalDigest(item.get("receipt_digest"),
                "receipt_link.receipt_digest");
        if (digest == null) {
            throw protocol("receipt_link.receipt_digest is required");
        }
        String ref = Json.requiredRef(item.get("receipt_ref"), "receipt_link.receipt_ref");
        if (!ref.equals("receipt:" + digest)) {
            throw protocol("receipt_link.receipt_ref does not match digest");
        }
        String observedId = Json.string(item.get("invocation_id"),
                "receipt_link.invocation_id", Protocol.MAX_REF_BYTES);
        if (!observedId.equals(invocationId)) {
            throw protocol("receipt_link invocation binding mismatch");
        }
        try {
            return new ContextReceiptLink(
                    Math.toIntExact(Json.integer(item.get("schema_version"),
                            "receipt_link.schema_version")),
                    Json.requiredRef(item.get("receipt_id"), "receipt_link.receipt_id"),
                    ref, digest, observedId);
        } catch (ArithmeticException | ValidationError exception) {
            throw protocol(exception.getMessage());
        }
    }

    private static void validatePair(String outputRef, String outputDigest, String label) {
        if (outputRef != null && outputDigest == null) {
            throw protocol(label + " output reference requires a digest");
        }
        if (outputRef != null && outputDigest != null
                && !outputRef.equals("output:" + outputDigest.substring("sha256:".length()))) {
            throw protocol(label + " output reference does not match digest");
        }
    }

    private static boolean booleanValue(Object value, String field) {
        if (!(value instanceof Boolean bool)) {
            throw protocol(field + " must be boolean");
        }
        return bool;
    }

    private static List<?> list(Object value, String field) {
        if (!(value instanceof List<?> list)) {
            throw protocol(field + " must be an array");
        }
        return list;
    }

    private static void exact(Map<String, Object> value, Set<String> expected, String label) {
        Json.exactKeys(value, expected, label);
    }

    private static EngineProtocolError protocol(String message) {
        return new EngineProtocolError(message);
    }

    static final class ParsedResponse {
        final ParsedView view;
        final Records records;
        final RecoveryBinding recovery;

        ParsedResponse(ParsedView view, Records records, RecoveryBinding recovery) {
            this.view = view;
            this.records = records;
            this.recovery = recovery;
        }
    }

    static final class ParsedView {
        final String text;
        final String outputRef;
        final String outputDigest;

        ParsedView(String text, String outputRef, String outputDigest) {
            this.text = text;
            this.outputRef = outputRef;
            this.outputDigest = outputDigest;
        }
    }

    static final class RecoveryBinding {
        final String recoveryRef;
        final String sourceRef;
        final String sourceDigest;

        RecoveryBinding(String recoveryRef, String sourceRef, String sourceDigest) {
            this.recoveryRef = recoveryRef;
            this.sourceRef = sourceRef;
            this.sourceDigest = sourceDigest;
        }
    }

    static final class Records {
        final Map<String, Object> invocation;
        final Map<String, Object> observation;

        Records(Map<String, Object> invocation, Map<String, Object> observation) {
            this.invocation = invocation;
            this.observation = observation;
        }
    }
}
