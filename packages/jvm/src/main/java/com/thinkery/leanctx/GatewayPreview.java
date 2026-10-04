// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Preview: the information gateway's egress admission and decision receipts.
 *
 * <p>{@link #admitEgress} runs one model request through the local Engine's
 * egress admission ({@code lean-ctx engine egress-admit}) before the caller
 * sends it: secrets are masked, restricted content is withheld, and the request
 * is classified. Parsing mirrors the Engine's validation; unknown fields and
 * values are rejected. Contract {@code leanctx-gateway-preview} 0.1 — may
 * change in minor releases.
 */
public final class GatewayPreview {
    public static final String CONTRACT = "leanctx-gateway-preview";
    public static final String VERSION = "0.1.0";
    public static final int EGRESS_SCHEMA_VERSION = 1;
    public static final int MAX_EGRESS_REQUEST_BYTES = 8 * 1024 * 1024;

    private static final int MAX_REF_BYTES = 512;
    private static final int MAX_DECISIONS = 4096;
    private static final int MAX_REASON_CODES = 32;
    private static final int MAX_SIGNALS = 32;
    private static final long U32 = 0xFFFF_FFFFL;
    private static final long MAX_SAFE = (1L << 53) - 1;
    private static final Pattern DIGEST = Pattern.compile("sha256:[0-9a-f]{64}");
    private static final Pattern REASON = Pattern.compile("[a-z][a-z0-9_.]{2,63}");

    private static final List<String> CLASSIFICATIONS = List.of("public", "internal", "confidential", "restricted");
    private static final List<String> DISPOSITIONS = List.of("forward", "rewritten", "refused");
    private static final List<String> MODES = List.of("developer", "governed", "sovereign");
    private static final List<String> PRINCIPAL_KINDS = List.of(
            "person", "team", "organization", "project", "agent", "session", "workload", "unknown");
    private static final List<String> LOCALITIES = List.of("local", "remote", "unknown");
    private static final List<String> OUTCOMES = List.of("delivered", "withheld", "failed");
    private static final List<String> CONTEXT_DISPOSITIONS = List.of(
            "allow", "allow_minimized", "allow_redacted", "allow_summary_only",
            "allow_local_model_only", "allow_with_approval", "quarantine", "deny");
    private static final List<String> TRANSFORMATIONS = List.of(
            "redaction", "classification", "selection", "deduplication",
            "structural_extraction", "compression", "summarization", "recovery", "reranking");
    private static final List<String> CATEGORIES = List.of(
            "secret", "pii", "prompt_injection", "classification", "policy", "custom");
    private static final List<String> SEVERITIES = List.of("info", "low", "medium", "high", "critical");
    private static final List<String> COVERAGE_KINDS = List.of(
            "complete", "partial", "unsupported", "failed", "not_required");
    private static final List<String> DETECTOR_STATUSES = List.of("completed", "failed", "timed_out", "skipped");

    private GatewayPreview() {
    }

    /** Who requested the context; {@code unknown} is explicit and never authorizes. */
    public record ContextPrincipal(String kind, Optional<String> id) {
        public boolean isKnown() {
            return !kind.equals("unknown");
        }
    }

    /** Where the context goes; organisation management is only ever attested. */
    public record ContextDestination(String provider, String locality, Optional<String> model,
                                     boolean organizationManaged, Optional<String> accountRef,
                                     Optional<String> region) {
    }

    /** What a detector actually inspected; never more than recorded. */
    public record DetectorCoverage(String kind, long bytesTotal, long bytesInspected,
                                   long chunksTotal, long chunksInspected, Optional<String> reason) {
        public boolean isComplete() {
            return kind.equals("complete");
        }
    }

    /** One detector's result: counts only, never the matched value. */
    public record SecuritySignal(String detectorId, String detectorVersion, String category,
                                 String severity, long evidenceCount, DetectorCoverage coverage,
                                 String status, long latencyUs, boolean calibrated,
                                 Optional<Long> confidenceMilli) {
    }

    /** The gateway's decision about one object (by digest, never content). */
    public record ContextDecision(String object, String disposition, List<String> reasonCodes,
                                  List<SecuritySignal> signals, List<String> requiredTransformations) {
        public boolean deliversContent() {
            return CONTEXT_DISPOSITIONS.indexOf(disposition)
                    <= CONTEXT_DISPOSITIONS.indexOf("allow_local_model_only");
        }
    }

    /** One governed delivery: who, where, under which policy, and what was withheld. */
    public record ContextDecisionReceipt(String receiptId, String mode, ContextPrincipal principal,
                                         ContextDestination destination, Map<String, Long> sources,
                                         Map<String, Long> security, Map<String, Long> tokens,
                                         String outcome, long durationUs, List<ContextDecision> decisions,
                                         Optional<Map<String, String>> policy, Optional<String> task,
                                         Optional<String> finalContext,
                                         Optional<Map<String, Object>> quality) {
        public List<SecuritySignal> signals() {
            List<SecuritySignal> signals = new ArrayList<>();
            decisions.forEach(decision -> signals.addAll(decision.signals()));
            return List.copyOf(signals);
        }
    }

    /** What may leave for the model, how sensitive it is, and why. */
    public record EgressAdmission(String disposition, Optional<Map<String, Object>> body,
                                  Optional<String> refusal, Optional<String> classification,
                                  Optional<ContextDecisionReceipt> receipt) {
        /** True when {@code body} may be sent; a refused request must not be. */
        public boolean maySend() {
            return !disposition.equals("refused");
        }
    }

    /** One model request about to leave for a provider. */
    public record EgressRequest(String provider, String upstreamBase, Map<String, Object> body) {
    }

    private static EngineProtocolError bad(String message) {
        return new EngineProtocolError("egress admission: " + message);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?>)) {
            throw bad(label + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    private static void keys(Map<String, Object> value, Set<String> required, Set<String> optional, String label) {
        for (String key : required) {
            if (!value.containsKey(key)) {
                throw bad(label + " lacks " + key);
            }
        }
        for (String key : value.keySet()) {
            if (!required.contains(key) && !optional.contains(key)) {
                throw bad(label + " has unknown field " + key);
            }
        }
    }

    private static long integer(Object value, String label, long maximum) {
        if (!(value instanceof BigInteger || value instanceof Long || value instanceof Integer)) {
            throw bad(label + " must be an integer");
        }
        BigInteger number = new BigInteger(value.toString());
        if (number.signum() < 0 || number.compareTo(BigInteger.valueOf(maximum)) > 0) {
            throw bad(label + " must be an integer in 0.." + maximum);
        }
        return number.longValue();
    }

    private static boolean bool(Object value, String label) {
        if (value == null) {
            return false;
        }
        if (!(value instanceof Boolean flag)) {
            throw bad(label + " must be a boolean");
        }
        return flag;
    }

    private static String ref(Object value, String label) {
        if (!(value instanceof String text) || text.isBlank()
                || text.getBytes(StandardCharsets.UTF_8).length > MAX_REF_BYTES
                || text.chars().anyMatch(ch -> ch < 0x20 || ch == 0x7f)) {
            throw bad(label + " must be a bounded reference");
        }
        return text;
    }

    private static Optional<String> optionalRef(Map<String, Object> value, String key, String label) {
        return value.containsKey(key) ? Optional.of(ref(value.get(key), label)) : Optional.empty();
    }

    private static String oneOf(Object value, List<String> allowed, String label) {
        if (!(value instanceof String text) || !allowed.contains(text)) {
            throw bad(label + " has unknown value " + value);
        }
        return text;
    }

    private static String digest(Object value, String label) {
        if (!(value instanceof String text) || !DIGEST.matcher(text).matches()) {
            throw bad(label + " must be a sha256 digest");
        }
        return text;
    }

    private static List<String> reasons(Object value, String label) {
        if (value == null) {
            return List.of();
        }
        if (!(value instanceof List<?> list) || list.size() > MAX_REASON_CODES) {
            throw bad(label + " must be a bounded list");
        }
        List<String> codes = new ArrayList<>();
        for (Object code : list) {
            if (!(code instanceof String text) || !REASON.matcher(text).matches()) {
                throw bad(label + " holds an invalid reason code");
            }
            codes.add(text);
        }
        return List.copyOf(codes);
    }

    private static List<?> list(Object value, String label, int maximum) {
        if (value == null) {
            return List.of();
        }
        if (!(value instanceof List<?> list) || list.size() > maximum) {
            throw bad(label + " must be a bounded list");
        }
        return list;
    }

    private static ContextPrincipal principal(Object raw) {
        Map<String, Object> value = object(raw, "principal");
        keys(value, Set.of("kind"), Set.of("id"), "principal");
        String kind = oneOf(value.get("kind"), PRINCIPAL_KINDS, "principal.kind");
        boolean hasId = value.containsKey("id");
        if (kind.equals("unknown")) {
            if (hasId) {
                throw bad("an unknown principal must not carry an identity");
            }
            return new ContextPrincipal(kind, Optional.empty());
        }
        if (!hasId) {
            throw bad("a known principal requires an id");
        }
        return new ContextPrincipal(kind, Optional.of(ref(value.get("id"), "principal.id")));
    }

    private static ContextDestination destination(Object raw) {
        Map<String, Object> value = object(raw, "destination");
        keys(value, Set.of("provider", "locality"),
                Set.of("model", "organization_managed", "account_ref", "region"), "destination");
        return new ContextDestination(
                ref(value.get("provider"), "destination.provider"),
                oneOf(value.get("locality"), LOCALITIES, "destination.locality"),
                optionalRef(value, "model", "destination.model"),
                bool(value.get("organization_managed"), "destination.organization_managed"),
                optionalRef(value, "account_ref", "destination.account_ref"),
                optionalRef(value, "region", "destination.region"));
    }

    private static DetectorCoverage coverage(Object raw) {
        Map<String, Object> value = object(raw, "coverage");
        keys(value, Set.of("kind", "bytes_total", "bytes_inspected", "chunks_total", "chunks_inspected"),
                Set.of("reason"), "coverage");
        DetectorCoverage parsed = new DetectorCoverage(
                oneOf(value.get("kind"), COVERAGE_KINDS, "coverage.kind"),
                integer(value.get("bytes_total"), "coverage.bytes_total", MAX_SAFE),
                integer(value.get("bytes_inspected"), "coverage.bytes_inspected", MAX_SAFE),
                integer(value.get("chunks_total"), "coverage.chunks_total", U32),
                integer(value.get("chunks_inspected"), "coverage.chunks_inspected", U32),
                value.containsKey("reason")
                        ? Optional.of(reasons(List.of(value.get("reason")), "coverage.reason").get(0))
                        : Optional.empty());
        if (parsed.bytesInspected() > parsed.bytesTotal() || parsed.chunksInspected() > parsed.chunksTotal()) {
            throw bad("coverage must not inspect more than the object holds");
        }
        boolean allBytes = parsed.bytesInspected() == parsed.bytesTotal();
        if (parsed.kind().equals("complete") && !allBytes) {
            throw bad("complete coverage must inspect every byte");
        }
        if (parsed.kind().equals("partial") && allBytes) {
            throw bad("partial coverage must leave bytes uninspected");
        }
        return parsed;
    }

    private static SecuritySignal signal(Object raw) {
        Map<String, Object> value = object(raw, "signal");
        keys(value, Set.of("detector", "category", "severity", "evidence_count", "coverage", "status", "latency_us"),
                Set.of("calibrated", "confidence_milli"), "signal");
        Map<String, Object> detector = object(value.get("detector"), "signal.detector");
        keys(detector, Set.of("id", "version"), Set.of(), "signal.detector");
        SecuritySignal parsed = new SecuritySignal(
                ref(detector.get("id"), "signal.detector.id"),
                ref(detector.get("version"), "signal.detector.version"),
                oneOf(value.get("category"), CATEGORIES, "signal.category"),
                oneOf(value.get("severity"), SEVERITIES, "signal.severity"),
                integer(value.get("evidence_count"), "signal.evidence_count", U32),
                coverage(value.get("coverage")),
                oneOf(value.get("status"), DETECTOR_STATUSES, "signal.status"),
                integer(value.get("latency_us"), "signal.latency_us", MAX_SAFE),
                bool(value.get("calibrated"), "signal.calibrated"),
                value.containsKey("confidence_milli")
                        ? Optional.of(integer(value.get("confidence_milli"), "signal.confidence_milli", 1000))
                        : Optional.empty());
        if ((parsed.status().equals("failed") || parsed.status().equals("timed_out")) && parsed.coverage().isComplete()) {
            throw bad("a failed or timed-out detector cannot claim complete coverage");
        }
        return parsed;
    }

    private static ContextDecision decision(Object raw) {
        Map<String, Object> value = object(raw, "decision");
        keys(value, Set.of("object", "disposition"),
                Set.of("reason_codes", "signals", "required_transformations"), "decision");
        List<SecuritySignal> signals = new ArrayList<>();
        for (Object signal : list(value.get("signals"), "decision.signals", MAX_SIGNALS)) {
            signals.add(signal(signal));
        }
        List<String> transformations = new ArrayList<>();
        for (Object kind : list(value.get("required_transformations"), "decision.required_transformations",
                Integer.MAX_VALUE)) {
            transformations.add(oneOf(kind, TRANSFORMATIONS, "decision.required_transformations"));
        }
        ContextDecision parsed = new ContextDecision(
                digest(value.get("object"), "decision.object"),
                oneOf(value.get("disposition"), CONTEXT_DISPOSITIONS, "decision.disposition"),
                reasons(value.get("reason_codes"), "decision.reason_codes"),
                List.copyOf(signals), List.copyOf(transformations));
        if (!parsed.disposition().equals("allow") && parsed.reasonCodes().isEmpty()) {
            throw bad("every non-allow decision requires at least one reason code");
        }
        return parsed;
    }

    private static Map<String, Long> counts(Object raw, List<String> names, String label, long maximum) {
        Map<String, Object> value = object(raw, label);
        keys(value, Set.copyOf(names), Set.of(), label);
        Map<String, Long> counts = new LinkedHashMap<>();
        for (String name : names) {
            counts.put(name, integer(value.get(name), label + "." + name, maximum));
        }
        return Map.copyOf(counts);
    }

    /** Parse and validate a {@code ContextDecisionReceiptV1} document. */
    public static ContextDecisionReceipt parseDecisionReceipt(Object raw) {
        Map<String, Object> value = object(raw, "receipt");
        keys(value, Set.of("schema_version", "receipt_id", "mode", "principal", "destination", "sources",
                        "security", "tokens", "outcome", "duration_us"),
                Set.of("task", "policy", "decisions", "final_context", "quality"), "receipt");
        if (integer(value.get("schema_version"), "receipt.schema_version", MAX_SAFE) != 1) {
            throw bad("unsupported receipt schema_version");
        }
        List<ContextDecision> decisions = new ArrayList<>();
        for (Object decision : list(value.get("decisions"), "receipt.decisions", MAX_DECISIONS)) {
            decisions.add(decision(decision));
        }
        Map<String, Long> sources = counts(value.get("sources"),
                List.of("inspected", "permitted", "selected", "blocked"), "receipt.sources", U32);
        if (sources.get("selected") > sources.get("permitted")
                || sources.get("permitted") + sources.get("blocked") > sources.get("inspected")) {
            throw bad("source counts must satisfy selected <= permitted and permitted + blocked <= inspected");
        }
        Map<String, Long> security = counts(value.get("security"), List.of("redactions", "blocked_objects",
                "quarantined_objects", "injection_signals", "incomplete_coverage"), "receipt.security", U32);
        long denied = decisions.stream().filter(d -> d.disposition().equals("deny")).count();
        long quarantined = decisions.stream().filter(d -> d.disposition().equals("quarantine")).count();
        if (security.get("blocked_objects") != denied || security.get("quarantined_objects") != quarantined) {
            throw bad("security counts must equal the recorded deny/quarantine decisions");
        }
        String outcome = oneOf(value.get("outcome"), OUTCOMES, "receipt.outcome");
        Optional<String> finalContext = value.containsKey("final_context")
                ? Optional.of(digest(value.get("final_context"), "receipt.final_context"))
                : Optional.empty();
        if (outcome.equals("delivered") != finalContext.isPresent()) {
            throw bad("exactly a delivered receipt names the delivered context digest");
        }
        Optional<Map<String, String>> policy = Optional.empty();
        if (value.containsKey("policy")) {
            Map<String, Object> raw2 = object(value.get("policy"), "receipt.policy");
            keys(raw2, Set.of("id", "digest"), Set.of("version"), "receipt.policy");
            Map<String, String> parsed = new LinkedHashMap<>();
            parsed.put("id", ref(raw2.get("id"), "receipt.policy.id"));
            parsed.put("digest", digest(raw2.get("digest"), "receipt.policy.digest"));
            if (raw2.containsKey("version")) {
                parsed.put("version", ref(raw2.get("version"), "receipt.policy.version"));
            }
            policy = Optional.of(Map.copyOf(parsed));
        }
        return new ContextDecisionReceipt(
                ref(value.get("receipt_id"), "receipt.receipt_id"),
                oneOf(value.get("mode"), MODES, "receipt.mode"),
                principal(value.get("principal")),
                destination(value.get("destination")),
                sources, security,
                counts(value.get("tokens"), List.of("original", "delivered"), "receipt.tokens", MAX_SAFE),
                outcome,
                integer(value.get("duration_us"), "receipt.duration_us", MAX_SAFE),
                List.copyOf(decisions), policy,
                optionalRef(value, "task", "receipt.task"),
                finalContext,
                value.containsKey("quality")
                        ? Optional.of(object(value.get("quality"), "receipt.quality"))
                        : Optional.empty());
    }

    /** Parse and validate an {@code EngineEgressAdmissionResponseV1} document. */
    public static EgressAdmission parseEgressAdmission(Object raw) {
        Map<String, Object> value = object(raw, "response");
        keys(value, Set.of("schema_version", "disposition"),
                Set.of("body", "refusal", "classification", "receipt"), "response");
        if (integer(value.get("schema_version"), "schema_version", MAX_SAFE) != EGRESS_SCHEMA_VERSION) {
            throw bad("unsupported egress schema_version");
        }
        String disposition = oneOf(value.get("disposition"), DISPOSITIONS, "disposition");
        Object body = value.get("body");
        Object refusal = value.get("refusal");
        if (disposition.equals("refused")) {
            if (value.containsKey("body") || !(refusal instanceof String text) || text.isBlank()) {
                throw bad("a refused request carries a refusal and no body");
            }
        } else if (value.containsKey("refusal") || !(body instanceof Map<?, ?>)) {
            throw bad("an admitted request carries a body object and no refusal");
        }
        return new EgressAdmission(
                disposition,
                body == null ? Optional.empty() : Optional.of(object(body, "body")),
                refusal == null ? Optional.empty() : Optional.of((String) refusal),
                value.containsKey("classification")
                        ? Optional.of(oneOf(value.get("classification"), CLASSIFICATIONS, "classification"))
                        : Optional.empty(),
                value.containsKey("receipt")
                        ? Optional.of(parseDecisionReceipt(value.get("receipt")))
                        : Optional.empty());
    }

    /**
     * Admit one model request through the local Engine before it is sent. Send
     * {@code admission.body()}, never the original body, and only when {@code maySend()}.
     */
    public static EgressAdmission admitEgress(SubprocessEngineClient engine, String projectRoot,
                                              EgressRequest request) {
        if (request.provider() == null || request.provider().isBlank()) {
            throw new ValidationError("provider must be a non-empty string");
        }
        if (request.upstreamBase() == null
                || !(request.upstreamBase().startsWith("https://") || request.upstreamBase().startsWith("http://"))) {
            throw new ValidationError("upstreamBase must be an http(s) URL");
        }
        if (request.body() == null) {
            throw new ValidationError("body must be a JSON object");
        }
        Map<String, Object> document = new LinkedHashMap<>();
        document.put("schema_version", EGRESS_SCHEMA_VERSION);
        document.put("provider", request.provider());
        document.put("upstream_base", request.upstreamBase());
        document.put("body", request.body());
        // Model requests carry fractional numbers (temperature, top_p): plain JSON.
        StringBuilder out = new StringBuilder();
        write(document, out, 0);
        byte[] payload = out.toString().getBytes(StandardCharsets.UTF_8);
        if (payload.length > MAX_EGRESS_REQUEST_BYTES) {
            throw new ValidationError("egress request exceeds its bound");
        }
        byte[] raw = engine.runPayload("egress-admit", projectRoot, payload);
        return parseEgressAdmission(Json.parse(raw, "egress admission"));
    }

    private static void write(Object value, StringBuilder out, int depth) {
        if (depth > 128) {
            throw new ValidationError("body nests too deeply");
        }
        if (value == null) {
            out.append("null");
        } else if (value instanceof String text) {
            out.append('"');
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                switch (c) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    default -> {
                        if (c < 0x20) {
                            out.append(String.format("\\u%04x", (int) c));
                        } else {
                            out.append(c);
                        }
                    }
                }
            }
            out.append('"');
        } else if (value instanceof Boolean flag) {
            out.append(flag);
        } else if (value instanceof Double || value instanceof Float) {
            double number = ((Number) value).doubleValue();
            if (!Double.isFinite(number)) {
                throw new ValidationError("body numbers must be finite");
            }
            out.append(BigDecimal.valueOf(number).toPlainString());
        } else if (value instanceof Number number) {
            out.append(new BigDecimal(number.toString()).toPlainString());
        } else if (value instanceof Map<?, ?> map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String key)) {
                    throw new ValidationError("body keys must be strings");
                }
                if (!first) {
                    out.append(',');
                }
                first = false;
                write(key, out, depth + 1);
                out.append(':');
                write(entry.getValue(), out, depth + 1);
            }
            out.append('}');
        } else if (value instanceof Collection<?> items) {
            out.append('[');
            boolean first = true;
            for (Object item : items) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                write(item, out, depth + 1);
            }
            out.append(']');
        } else {
            throw new ValidationError("body is not JSON data");
        }
    }
}
