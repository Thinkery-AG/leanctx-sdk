// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;

/**
 * Preview: read-only access to the local Engine's Context Store.
 *
 * <p>{@link #readPolicyEvidence} returns the scope's content-free
 * read-strategy evidence ({@code lean-ctx engine context-policy-evidence});
 * {@link #readTaskLineage} one task's decision lineage ({@code lean-ctx engine
 * context-lineage}) with every missing link named as a gap. Both reads are
 * confined to one tenant/project scope. Parsing mirrors the Engine's
 * validation: unknown fields or values and inconsistent counts are rejected,
 * and "unmeasured" never reads as a passing measurement. Contract
 * {@code leanctx-context-store-preview} 0.1 — may change in minor releases.
 */
public final class ContextStorePreview {
    public static final String CONTRACT = "leanctx-context-store-preview";
    public static final String VERSION = "0.1.0";
    public static final int MAX_STORE_REQUEST_BYTES = 16 * 1024;
    public static final int MAX_STORE_RESPONSE_BYTES = 8 * 1024 * 1024;

    private static final int MAX_RECORDS = 4096;
    private static final int MAX_LINEAGE_ITEMS = 4096;
    private static final int MAX_REF_BYTES = 512;
    private static final int MAX_TEXT_BYTES = 4096;
    private static final long MAX_SAFE = (1L << 53) - 1;
    private static final Pattern DIGEST = Pattern.compile("sha256:[0-9a-f]{64}");
    private static final Pattern FIELD = Pattern.compile("[a-z][a-z0-9_]{0,63}");

    private static final List<String> TASK_CLASSES = List.of(
            "bug_fix", "refactor", "test_addition", "documentation", "investigation");
    private static final List<String> LANGUAGES = List.of(
            "rust", "python", "type_script", "java_script", "go", "java", "c", "cpp", "c_sharp",
            "swift", "kotlin", "ruby", "php", "shell", "other", "none");
    private static final List<String> SIZES = List.of("tiny", "small", "medium", "large", "very_large");
    private static final List<String> STRATEGIES = List.of(
            "full", "map", "signatures", "aggressive", "entropy", "task", "reference", "diff",
            "lines", "auto", "other");
    private static final List<String> EVIDENCE_TIERS = List.of(
            "mechanism", "deterministic_quality", "recorded_regression", "live_task_evaluation",
            "production_outcome");
    private static final List<String> VERDICTS = List.of("improved", "non_inferior", "regressed", "underpowered");
    private static final List<String> STEP_KINDS = List.of(
            "task_started", "plan_created", "context_delivered", "model_invoked", "engine_invoked",
            "receipt_signed", "canonical_receipt_recorded", "outcome_recorded", "decision_recorded");
    private static final List<String> OUTCOMES = List.of("accepted", "rejected", "unknown");
    private static final List<String> DELIVERY_OUTCOMES = List.of("delivered", "withheld", "failed");
    private static final List<String> DELIVERY_ERRORS = List.of("missing", "unreadable", "tampered");
    private static final List<String> GAPS = List.of(
            "ledger_unverified", "no_plan_recorded", "no_delivery_recorded", "delivery_unverified",
            "deliveries_incomplete", "no_outcome_recorded");
    private static final List<String> RECORD_COUNTS = List.of(
            "observed_day", "samples", "accepted", "rejected", "explicit_overrides", "token_samples",
            "tokens_original", "tokens_delivered");
    private static final List<String> SIGNAL_COUNTS = List.of(
            "signal_samples", "bounce_tasks", "expand_tasks", "edit_failure_tasks");

    private ContextStorePreview() {
    }

    /** A plan's task class, dominant language and budget bucket. */
    public record Workload(String taskClass, String language, String size) {
    }

    /** Critical retention and recovery; empty counts mean "not measured", never zero. */
    public record QualityEvidence(boolean measured, Optional<Long> retained, Optional<Long> recoverable,
                                  Optional<Long> lost, Optional<Long> handlesEmitted,
                                  Optional<Long> handlesVerified, Optional<Long> failures,
                                  Optional<Long> criticalFailures) {
    }

    /** Deliveries that left without full inspection; unmeasured is not zero. */
    public record SecurityEvidence(boolean measured, Optional<Long> regressions) {
    }

    /** One read strategy on one workload, on one UTC day. */
    public record StrategyOutcomeRecord(Workload workload, String strategy, long observedDay, long samples,
                                        long accepted, long rejected, long explicitOverrides,
                                        long tokenSamples, long signalSamples, long bounceTasks,
                                        long expandTasks, long editFailureTasks, long tokensOriginal,
                                        long tokensDelivered, QualityEvidence quality,
                                        SecurityEvidence security) {
    }

    /** A paired task evaluation ({@code lean-ctx eval frontier}). */
    public record StrategyEvaluation(String strategy, String evidenceTier, String verdict, long pairs,
                                     boolean powered, long deltaMilli, long ciLowMilli, long ciHighMilli,
                                     long marginMilli) {
    }

    /** One scope's {@code ContextPolicyEvidenceV1}. */
    public record ContextPolicyEvidence(List<StrategyOutcomeRecord> records,
                                        List<StrategyEvaluation> evaluations) {
    }

    /** One ledger observation: identifiers and counts, no content. */
    public record LineageStep(long sequence, String kind, String timestamp, Map<String, String> fields) {
    }

    /** A verified Decision Receipt, summarized. */
    public record DeliverySummary(String outcome, String destination, Optional<String> policyDigest,
                                  long inspected, long delivered, long withheld, long redactions,
                                  long tokensOriginal, long tokensDelivered, Optional<String> finalContext) {
    }

    /** One governed delivery; unverified ones name their error. */
    public record LineageDelivery(String digest, boolean verified, Optional<String> error,
                                  Optional<DeliverySummary> summary) {
    }

    /** One task's lineage; {@code gaps} lists every missing link. */
    public record TaskLineage(String taskId, Optional<String> scope, List<LineageStep> steps,
                              List<LineageDelivery> deliveries, Optional<String> ledgerError,
                              String outcome, List<String> gaps) {
        public boolean isComplete() {
            return gaps.isEmpty();
        }
    }

    /** The tenant/project scope of a read; without a project ID the Engine uses the project root. */
    public record Scope(Optional<String> projectId, Optional<String> tenantId) {
        public static Scope project(String projectId) {
            return new Scope(Optional.of(projectId), Optional.empty());
        }
    }

    private static EngineProtocolError bad(String message) {
        return new EngineProtocolError("context store: " + message);
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

    private static long integer(Object value, String label, boolean signed) {
        if (!(value instanceof BigInteger || value instanceof Long || value instanceof Integer)) {
            throw bad(label + " must be an integer");
        }
        BigInteger number = new BigInteger(value.toString());
        BigInteger minimum = signed ? BigInteger.valueOf(-MAX_SAFE) : BigInteger.ZERO;
        if (number.compareTo(minimum) < 0 || number.compareTo(BigInteger.valueOf(MAX_SAFE)) > 0) {
            throw bad(label + " must be a safe " + (signed ? "" : "non-negative ") + "integer");
        }
        return number.longValue();
    }

    private static boolean bool(Object value, String label) {
        if (!(value instanceof Boolean flag)) {
            throw bad(label + " must be a boolean");
        }
        return flag;
    }

    private static String text(Object value, String label, int maximum) {
        if (!(value instanceof String text) || text.getBytes(StandardCharsets.UTF_8).length > maximum
                || text.chars().anyMatch(ch -> ch < 0x20 || ch == 0x7f)) {
            throw bad(label + " must be bounded text");
        }
        return text;
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

    private static Optional<String> optionalDigest(Object value, String label) {
        return value == null ? Optional.empty() : Optional.of(digest(value, label));
    }

    private static List<?> list(Object value, String label, int maximum) {
        if (!(value instanceof List<?> list) || list.size() > maximum) {
            throw bad(label + " must be a list of at most " + maximum);
        }
        return list;
    }

    private static Set<String> set(List<String> names, String... extra) {
        java.util.HashSet<String> all = new java.util.HashSet<>(names);
        all.addAll(List.of(extra));
        return all;
    }

    private static Workload workload(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        keys(value, Set.of("task_class", "language", "size"), Set.of(), label);
        return new Workload(
                oneOf(value.get("task_class"), TASK_CLASSES, label + ".task_class"),
                oneOf(value.get("language"), LANGUAGES, label + ".language"),
                oneOf(value.get("size"), SIZES, label + ".size"));
    }

    private static QualityEvidence quality(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        Object state = value.get("state");
        if ("unmeasured".equals(state)) {
            keys(value, Set.of("state"), Set.of(), label);
            return new QualityEvidence(false, Optional.empty(), Optional.empty(), Optional.empty(),
                    Optional.empty(), Optional.empty(), Optional.empty(), Optional.empty());
        }
        if (!"measured".equals(state)) {
            throw bad(label + ".state has unknown value " + state);
        }
        keys(value, Set.of("state", "retention", "recovery"), Set.of(), label);
        Map<String, Object> retention = object(value.get("retention"), label + ".retention");
        keys(retention, Set.of("retained", "recoverable", "lost"), Set.of(), label + ".retention");
        Map<String, Object> recovery = object(value.get("recovery"), label + ".recovery");
        keys(recovery, Set.of("handles_emitted", "handles_verified", "failures", "critical_failures"),
                Set.of(), label + ".recovery");
        long emitted = integer(recovery.get("handles_emitted"), label + ".handles_emitted", false);
        long verified = integer(recovery.get("handles_verified"), label + ".handles_verified", false);
        long failures = integer(recovery.get("failures"), label + ".failures", false);
        long critical = integer(recovery.get("critical_failures"), label + ".critical_failures", false);
        if (verified > emitted || failures > emitted || critical > failures) {
            throw bad(label + " has impossible recovery counts");
        }
        return new QualityEvidence(true,
                Optional.of(integer(retention.get("retained"), label + ".retained", false)),
                Optional.of(integer(retention.get("recoverable"), label + ".recoverable", false)),
                Optional.of(integer(retention.get("lost"), label + ".lost", false)),
                Optional.of(emitted), Optional.of(verified), Optional.of(failures), Optional.of(critical));
    }

    private static SecurityEvidence security(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        Object state = value.get("state");
        if ("unmeasured".equals(state)) {
            keys(value, Set.of("state"), Set.of(), label);
            return new SecurityEvidence(false, Optional.empty());
        }
        if (!"measured".equals(state)) {
            throw bad(label + ".state has unknown value " + state);
        }
        keys(value, Set.of("state", "regressions"), Set.of(), label);
        return new SecurityEvidence(true, Optional.of(integer(value.get("regressions"), label, false)));
    }

    private static StrategyOutcomeRecord outcomeRecord(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        keys(value, set(RECORD_COUNTS, "workload", "strategy", "quality", "security"), Set.copyOf(SIGNAL_COUNTS),
                label);
        Map<String, Long> counts = new LinkedHashMap<>();
        for (String name : RECORD_COUNTS) {
            counts.put(name, integer(value.get(name), label + "." + name, false));
        }
        // Signal counts default to zero, like the Engine's own deserializer.
        for (String name : SIGNAL_COUNTS) {
            counts.put(name, value.containsKey(name) ? integer(value.get(name), label + "." + name, false) : 0L);
        }
        StrategyOutcomeRecord record = new StrategyOutcomeRecord(
                workload(value.get("workload"), label + ".workload"),
                oneOf(value.get("strategy"), STRATEGIES, label + ".strategy"),
                counts.get("observed_day"), counts.get("samples"), counts.get("accepted"),
                counts.get("rejected"), counts.get("explicit_overrides"), counts.get("token_samples"),
                counts.get("signal_samples"), counts.get("bounce_tasks"), counts.get("expand_tasks"),
                counts.get("edit_failure_tasks"), counts.get("tokens_original"), counts.get("tokens_delivered"),
                quality(value.get("quality"), label + ".quality"),
                security(value.get("security"), label + ".security"));
        if (record.samples() == 0) {
            throw bad(label + " has no samples");
        }
        if (record.accepted() + record.rejected() != record.samples()) {
            throw bad(label + ": accepted + rejected must equal samples");
        }
        if (record.explicitOverrides() > record.samples() || record.tokenSamples() > record.samples()) {
            throw bad(label + " counts more tasks than samples");
        }
        if (record.signalSamples() > record.samples() || record.bounceTasks() > record.signalSamples()
                || record.expandTasks() > record.signalSamples()
                || record.editFailureTasks() > record.signalSamples()) {
            throw bad(label + " signal counts exceed their attributed tasks");
        }
        if (record.tokenSamples() == 0 && (record.tokensOriginal() > 0 || record.tokensDelivered() > 0)) {
            throw bad(label + " has tokens without token samples");
        }
        if (record.tokensDelivered() > record.tokensOriginal()) {
            throw bad(label + " delivered more tokens than original");
        }
        return record;
    }

    private static StrategyEvaluation evaluation(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        keys(value, Set.of("strategy", "evidence_tier", "verdict", "pairs", "powered", "delta_milli",
                "ci_low_milli", "ci_high_milli", "margin_milli"), Set.of(), label);
        StrategyEvaluation result = new StrategyEvaluation(
                oneOf(value.get("strategy"), STRATEGIES, label + ".strategy"),
                oneOf(value.get("evidence_tier"), EVIDENCE_TIERS, label + ".evidence_tier"),
                oneOf(value.get("verdict"), VERDICTS, label + ".verdict"),
                integer(value.get("pairs"), label + ".pairs", false),
                bool(value.get("powered"), label + ".powered"),
                integer(value.get("delta_milli"), label + ".delta_milli", true),
                integer(value.get("ci_low_milli"), label + ".ci_low_milli", true),
                integer(value.get("ci_high_milli"), label + ".ci_high_milli", true),
                integer(value.get("margin_milli"), label + ".margin_milli", true));
        if (result.strategy().equals("other")) {
            throw bad(label + " must name a known strategy");
        }
        if (result.ciLowMilli() > result.ciHighMilli() || result.marginMilli() < 0) {
            throw bad(label + " has an impossible interval");
        }
        if (result.powered() && result.pairs() == 0) {
            throw bad(label + " is powered without pairs");
        }
        return result;
    }

    private static int compareRecords(StrategyOutcomeRecord left, StrategyOutcomeRecord right) {
        long[] a = {TASK_CLASSES.indexOf(left.workload().taskClass()), LANGUAGES.indexOf(left.workload().language()),
            SIZES.indexOf(left.workload().size()), STRATEGIES.indexOf(left.strategy()), left.observedDay()};
        long[] b = {TASK_CLASSES.indexOf(right.workload().taskClass()), LANGUAGES.indexOf(right.workload().language()),
            SIZES.indexOf(right.workload().size()), STRATEGIES.indexOf(right.strategy()), right.observedDay()};
        for (int i = 0; i < a.length; i++) {
            if (a[i] != b[i]) {
                return Long.compare(a[i], b[i]);
            }
        }
        return 0;
    }

    /** Parse and validate a {@code ContextPolicyEvidenceV1} document. */
    public static ContextPolicyEvidence parsePolicyEvidence(Object raw) {
        Map<String, Object> value = object(raw, "evidence");
        keys(value, Set.of("schema_version", "records"), Set.of("evaluations"), "evidence");
        if (integer(value.get("schema_version"), "schema_version", false) != 1) {
            throw bad("unsupported evidence schema_version");
        }
        List<StrategyOutcomeRecord> records = new ArrayList<>();
        int position = 0;
        for (Object item : list(value.get("records"), "records", MAX_RECORDS)) {
            StrategyOutcomeRecord record = outcomeRecord(item, "records[" + position++ + "]");
            if (!records.isEmpty() && compareRecords(records.get(records.size() - 1), record) >= 0) {
                throw bad("records must be strictly sorted by workload, strategy and day");
            }
            records.add(record);
        }
        List<StrategyEvaluation> evaluations = new ArrayList<>();
        if (value.containsKey("evaluations")) {
            position = 0;
            for (Object item : list(value.get("evaluations"), "evaluations", STRATEGIES.size())) {
                StrategyEvaluation result = evaluation(item, "evaluations[" + position++ + "]");
                if (!evaluations.isEmpty() && STRATEGIES.indexOf(evaluations.get(evaluations.size() - 1).strategy())
                        >= STRATEGIES.indexOf(result.strategy())) {
                    throw bad("evaluations must be one per strategy, sorted");
                }
                evaluations.add(result);
            }
        }
        return new ContextPolicyEvidence(List.copyOf(records), List.copyOf(evaluations));
    }

    private static LineageStep step(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        keys(value, Set.of("sequence", "kind", "timestamp", "fields"), Set.of(), label);
        Map<String, String> fields = new TreeMap<>();
        for (Map.Entry<String, Object> entry : object(value.get("fields"), label + ".fields").entrySet()) {
            if (!FIELD.matcher(entry.getKey()).matches()) {
                throw bad(label + ".fields has an invalid name");
            }
            fields.put(entry.getKey(), text(entry.getValue(), label + ".fields." + entry.getKey(), MAX_REF_BYTES));
        }
        return new LineageStep(
                integer(value.get("sequence"), label + ".sequence", false),
                oneOf(value.get("kind"), STEP_KINDS, label + ".kind"),
                text(value.get("timestamp"), label + ".timestamp", MAX_REF_BYTES),
                Map.copyOf(fields));
    }

    private static DeliverySummary summary(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        keys(value, Set.of("outcome", "destination", "policy_digest", "final_context", "inspected", "delivered",
                "withheld", "redactions", "tokens_original", "tokens_delivered"), Set.of(), label);
        DeliverySummary result = new DeliverySummary(
                oneOf(value.get("outcome"), DELIVERY_OUTCOMES, label + ".outcome"),
                text(value.get("destination"), label + ".destination", MAX_REF_BYTES),
                optionalDigest(value.get("policy_digest"), label + ".policy_digest"),
                integer(value.get("inspected"), label + ".inspected", false),
                integer(value.get("delivered"), label + ".delivered", false),
                integer(value.get("withheld"), label + ".withheld", false),
                integer(value.get("redactions"), label + ".redactions", false),
                integer(value.get("tokens_original"), label + ".tokens_original", false),
                integer(value.get("tokens_delivered"), label + ".tokens_delivered", false),
                optionalDigest(value.get("final_context"), label + ".final_context"));
        if (result.tokensDelivered() > result.tokensOriginal()) {
            throw bad(label + " delivered more tokens than original");
        }
        return result;
    }

    private static LineageDelivery delivery(Object raw, String label) {
        Map<String, Object> value = object(raw, label);
        keys(value, Set.of("digest", "verified"), Set.of("error", "summary"), label);
        boolean verified = bool(value.get("verified"), label + ".verified");
        if (verified != value.containsKey("summary") || verified == value.containsKey("error")) {
            throw bad(label + ": a verified delivery has a summary, an unverified one an error");
        }
        return new LineageDelivery(
                digest(value.get("digest"), label + ".digest"),
                verified,
                value.containsKey("error")
                        ? Optional.of(oneOf(value.get("error"), DELIVERY_ERRORS, label + ".error"))
                        : Optional.empty(),
                verified ? Optional.of(summary(value.get("summary"), label + ".summary")) : Optional.empty());
    }

    /** Parse and validate a {@code TaskLineageV1} document. */
    public static TaskLineage parseTaskLineage(Object raw) {
        Map<String, Object> value = object(raw, "lineage");
        keys(value, Set.of("schema_version", "task_id", "steps", "deliveries", "outcome", "gaps"),
                Set.of("scope", "ledger_error"), "lineage");
        if (integer(value.get("schema_version"), "schema_version", false) != 1) {
            throw bad("unsupported lineage schema_version");
        }
        List<String> gaps = new ArrayList<>();
        for (Object item : list(value.get("gaps"), "gaps", GAPS.size())) {
            String gap = oneOf(item, GAPS, "gaps");
            if (gaps.contains(gap)) {
                throw bad("gaps must not repeat");
            }
            gaps.add(gap);
        }
        Optional<String> ledgerError = value.containsKey("ledger_error")
                ? Optional.of(text(value.get("ledger_error"), "ledger_error", MAX_TEXT_BYTES))
                : Optional.empty();
        if (ledgerError.isPresent() != gaps.contains("ledger_unverified")) {
            throw bad("a ledger error and the ledger_unverified gap go together");
        }
        List<LineageStep> steps = new ArrayList<>();
        int position = 0;
        for (Object item : list(value.get("steps"), "steps", MAX_LINEAGE_ITEMS)) {
            steps.add(step(item, "steps[" + position++ + "]"));
        }
        List<LineageDelivery> deliveries = new ArrayList<>();
        position = 0;
        for (Object item : list(value.get("deliveries"), "deliveries", MAX_LINEAGE_ITEMS)) {
            deliveries.add(delivery(item, "deliveries[" + position++ + "]"));
        }
        if (deliveries.stream().anyMatch(d -> !d.verified()) != gaps.contains("delivery_unverified")) {
            throw bad("an unverified delivery and the delivery_unverified gap go together");
        }
        return new TaskLineage(
                text(value.get("task_id"), "task_id", MAX_REF_BYTES),
                value.containsKey("scope")
                        ? Optional.of(text(value.get("scope"), "scope", MAX_TEXT_BYTES))
                        : Optional.empty(),
                List.copyOf(steps), List.copyOf(deliveries), ledgerError,
                oneOf(value.get("outcome"), OUTCOMES, "outcome"), List.copyOf(gaps));
    }

    private static byte[] request(Scope scope, Optional<String> taskId) {
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("schema_version", 1);
        request.put("transport_version", 1);
        request.put("engine_interface_version", "1.0.0");
        Map<String, Optional<String>> named = new LinkedHashMap<>();
        named.put("project_id", scope.projectId());
        named.put("tenant_id", scope.tenantId());
        named.put("task_id", taskId);
        for (Map.Entry<String, Optional<String>> entry : named.entrySet()) {
            if (entry.getValue().isEmpty()) {
                continue;
            }
            String item = entry.getValue().get();
            if (item.isBlank() || item.getBytes(StandardCharsets.UTF_8).length > MAX_REF_BYTES) {
                throw new ValidationError(entry.getKey() + " must be a non-empty bounded string");
            }
            request.put(entry.getKey(), item);
        }
        return Json.canonicalBytes(request);
    }

    private static Object read(SubprocessEngineClient engine, String operation, String projectRoot,
                               byte[] payload, String body) {
        if (payload.length > MAX_STORE_REQUEST_BYTES) {
            throw new ValidationError("context store request exceeds its bound");
        }
        byte[] raw = engine.runPayload(operation, projectRoot, payload);
        if (raw.length > MAX_STORE_RESPONSE_BYTES) {
            throw bad("response exceeds its bound");
        }
        Map<String, Object> value = object(Json.parse(raw, "context store"), "response");
        keys(value, Set.of("schema_version", "transport_version", "engine_interface_version", body), Set.of(),
                "response");
        if (integer(value.get("schema_version"), "schema_version", false) != 1
                || integer(value.get("transport_version"), "transport_version", false) != 1
                || !"1.0.0".equals(value.get("engine_interface_version"))) {
            throw bad("unsupported response versions");
        }
        return value.get(body);
    }

    /** The scope's read-strategy evidence. */
    public static ContextPolicyEvidence readPolicyEvidence(SubprocessEngineClient engine, String projectRoot,
                                                           Scope scope) {
        return parsePolicyEvidence(read(engine, "context-policy-evidence", projectRoot,
                request(scope, Optional.empty()), "evidence"));
    }

    /** One task's lineage within the scope. */
    public static TaskLineage readTaskLineage(SubprocessEngineClient engine, String projectRoot, String taskId,
                                              Scope scope) {
        if (taskId == null || taskId.isBlank()) {
            throw new ValidationError("taskId must be a non-empty string");
        }
        return parseTaskLineage(read(engine, "context-lineage", projectRoot, request(scope, Optional.of(taskId)),
                "lineage"));
    }
}
