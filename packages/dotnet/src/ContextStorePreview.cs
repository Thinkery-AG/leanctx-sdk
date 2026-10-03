// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
using System.Collections.ObjectModel;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Thinkery.LeanCtx.Preview;

/// <summary>A plan's task class, dominant language and budget bucket.</summary>
public sealed record Workload(string TaskClass, string Language, string Size);

/// <summary>Critical retention and recovery; null counts mean "not measured", never zero.</summary>
public sealed record QualityEvidence(
    bool Measured,
    long? Retained,
    long? Recoverable,
    long? Lost,
    long? HandlesEmitted,
    long? HandlesVerified,
    long? Failures,
    long? CriticalFailures);

/// <summary>Deliveries that left without full inspection; unmeasured is not zero.</summary>
public sealed record SecurityEvidence(bool Measured, long? Regressions);

/// <summary>One read strategy on one workload, on one UTC day.</summary>
public sealed record StrategyOutcomeRecord(
    Workload Workload,
    string Strategy,
    long ObservedDay,
    long Samples,
    long Accepted,
    long Rejected,
    long ExplicitOverrides,
    long TokenSamples,
    long SignalSamples,
    long BounceTasks,
    long ExpandTasks,
    long EditFailureTasks,
    long TokensOriginal,
    long TokensDelivered,
    QualityEvidence Quality,
    SecurityEvidence Security);

/// <summary>A paired task evaluation (lean-ctx eval frontier).</summary>
public sealed record StrategyEvaluation(
    string Strategy,
    string EvidenceTier,
    string Verdict,
    long Pairs,
    bool Powered,
    long DeltaMilli,
    long CiLowMilli,
    long CiHighMilli,
    long MarginMilli);

/// <summary>One scope's ContextPolicyEvidenceV1.</summary>
public sealed record ContextPolicyEvidence(
    IReadOnlyList<StrategyOutcomeRecord> Records,
    IReadOnlyList<StrategyEvaluation> Evaluations);

/// <summary>One ledger observation: identifiers and counts, no content.</summary>
public sealed record LineageStep(long Sequence, string Kind, string Timestamp, IReadOnlyDictionary<string, string> Fields);

/// <summary>A verified Decision Receipt, summarized.</summary>
public sealed record DeliverySummary(
    string Outcome,
    string Destination,
    string? PolicyDigest,
    long Inspected,
    long Delivered,
    long Withheld,
    long Redactions,
    long TokensOriginal,
    long TokensDelivered,
    string? FinalContext);

/// <summary>One governed delivery; unverified ones name their error.</summary>
public sealed record LineageDelivery(string Digest, bool Verified, string? Error, DeliverySummary? Summary);

/// <summary>One task's lineage; Gaps lists every missing link.</summary>
public sealed record TaskLineage(
    string TaskId,
    string? Scope,
    IReadOnlyList<LineageStep> Steps,
    IReadOnlyList<LineageDelivery> Deliveries,
    string? LedgerError,
    string Outcome,
    IReadOnlyList<string> Gaps)
{
    public bool IsComplete => Gaps.Count == 0;
}

/// <summary>The tenant/project scope of a read; without a project ID the Engine uses the project root.</summary>
public sealed record ContextStoreScope(string? ProjectId = null, string? TenantId = null);

/// <summary>
/// Preview: read-only access to the local Engine's Context Store. ReadPolicyEvidenceAsync returns the
/// scope's content-free read-strategy evidence (lean-ctx engine context-policy-evidence);
/// ReadTaskLineageAsync one task's decision lineage (lean-ctx engine context-lineage) with every missing
/// link named as a gap. Parsing mirrors the Engine's validation: unknown fields or values and inconsistent
/// counts are rejected, and "unmeasured" never reads as a passing measurement. Contract
/// leanctx-context-store-preview 0.1 — may change in minor releases.
/// </summary>
public static class ContextStorePreview
{
    public const string Contract = "leanctx-context-store-preview";
    public const string Version = "0.1.0";
    public const int MaxStoreRequestBytes = 16 * 1024;
    public const int MaxStoreResponseBytes = 8 * 1024 * 1024;

    private const int MaxRecords = 4096;
    private const int MaxLineageItems = 4096;
    private const int MaxRefBytes = 512;
    private const int MaxTextBytes = 4096;
    private const long MaxSafe = (1L << 53) - 1;
    private static readonly Regex DigestPattern = new("^sha256:[0-9a-f]{64}$", RegexOptions.CultureInvariant);
    private static readonly Regex FieldPattern = new("^[a-z][a-z0-9_]{0,63}$", RegexOptions.CultureInvariant);

    private static readonly string[] TaskClasses = { "bug_fix", "refactor", "test_addition", "documentation", "investigation" };
    private static readonly string[] Languages =
    {
        "rust", "python", "type_script", "java_script", "go", "java", "c", "cpp", "c_sharp",
        "swift", "kotlin", "ruby", "php", "shell", "other", "none",
    };
    private static readonly string[] Sizes = { "tiny", "small", "medium", "large", "very_large" };
    private static readonly string[] Strategies =
        { "full", "map", "signatures", "aggressive", "entropy", "task", "reference", "diff", "lines", "auto", "other" };
    private static readonly string[] EvidenceTiers =
        { "mechanism", "deterministic_quality", "recorded_regression", "live_task_evaluation", "production_outcome" };
    private static readonly string[] Verdicts = { "improved", "non_inferior", "regressed", "underpowered" };
    private static readonly string[] StepKinds =
    {
        "task_started", "plan_created", "context_delivered", "model_invoked", "engine_invoked",
        "receipt_signed", "canonical_receipt_recorded", "outcome_recorded", "decision_recorded",
    };
    private static readonly string[] Outcomes = { "accepted", "rejected", "unknown" };
    private static readonly string[] DeliveryOutcomes = { "delivered", "withheld", "failed" };
    private static readonly string[] DeliveryErrors = { "missing", "unreadable", "tampered" };
    private static readonly string[] GapNames =
    {
        "ledger_unverified", "no_plan_recorded", "no_delivery_recorded", "delivery_unverified",
        "deliveries_incomplete", "no_outcome_recorded",
    };
    private static readonly string[] RecordCounts =
        { "observed_day", "samples", "accepted", "rejected", "explicit_overrides", "token_samples", "tokens_original", "tokens_delivered" };
    private static readonly string[] SignalCounts = { "signal_samples", "bounce_tasks", "expand_tasks", "edit_failure_tasks" };

    private static EngineProtocolError Bad(string message) => new("context store: " + message);

    private static Dictionary<string, JsonElement> Fields(JsonElement value, string label)
    {
        if (value.ValueKind != JsonValueKind.Object)
            throw Bad(label + " must be an object");
        var result = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var property in value.EnumerateObject())
        {
            if (!result.TryAdd(property.Name, property.Value))
                throw Bad(label + " has a duplicate field " + property.Name);
        }
        return result;
    }

    private static void Keys(Dictionary<string, JsonElement> value, string[] required, string[] optional, string label)
    {
        foreach (var key in required)
        {
            if (!value.ContainsKey(key))
                throw Bad(label + " lacks " + key);
        }
        foreach (var key in value.Keys)
        {
            if (Array.IndexOf(required, key) < 0 && Array.IndexOf(optional, key) < 0)
                throw Bad(label + " has unknown field " + key);
        }
    }

    private static long Integer(JsonElement value, string label, bool signed = false)
    {
        if (value.ValueKind != JsonValueKind.Number)
            throw Bad(label + " must be an integer");
        var raw = value.GetRawText();
        var minimum = signed ? -MaxSafe : 0;
        if (raw.IndexOfAny(new[] { '.', 'e', 'E' }) >= 0 || !value.TryGetInt64(out var number) ||
            number < minimum || number > MaxSafe)
            throw Bad($"{label} must be an integer in {minimum}..{MaxSafe}");
        return number;
    }

    private static bool Bool(JsonElement value, string label) => value.ValueKind switch
    {
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => throw Bad(label + " must be a boolean"),
    };

    private static string Text(JsonElement value, string label, int maximum)
    {
        if (value.ValueKind != JsonValueKind.String)
            throw Bad(label + " must be bounded text");
        var text = value.GetString() ?? string.Empty;
        if (Encoding.UTF8.GetByteCount(text) > maximum || text.Any(character => character < 0x20 || character == 0x7f))
            throw Bad(label + " must be bounded text");
        return text;
    }

    private static string OneOf(JsonElement value, string[] allowed, string label)
    {
        var text = value.ValueKind == JsonValueKind.String ? value.GetString() : null;
        if (text is null || Array.IndexOf(allowed, text) < 0)
            throw Bad(label + " has unknown value " + value.GetRawText());
        return text;
    }

    private static string Digest(JsonElement value, string label)
    {
        var text = value.ValueKind == JsonValueKind.String ? value.GetString() : null;
        if (text is null || !DigestPattern.IsMatch(text))
            throw Bad(label + " must be a sha256 digest");
        return text;
    }

    private static string? OptionalDigest(JsonElement value, string label) =>
        value.ValueKind == JsonValueKind.Null ? null : Digest(value, label);

    private static List<JsonElement> Items(JsonElement value, string label, int maximum)
    {
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() > maximum)
            throw Bad($"{label} must be a list of at most {maximum}");
        return value.EnumerateArray().ToList();
    }

    private static Workload ParseWorkload(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        Keys(value, new[] { "task_class", "language", "size" }, Array.Empty<string>(), label);
        return new Workload(
            OneOf(value["task_class"], TaskClasses, label + ".task_class"),
            OneOf(value["language"], Languages, label + ".language"),
            OneOf(value["size"], Sizes, label + ".size"));
    }

    private static string State(Dictionary<string, JsonElement> value, string label)
    {
        var state = value.TryGetValue("state", out var raw) && raw.ValueKind == JsonValueKind.String ? raw.GetString() : null;
        if (state is not ("measured" or "unmeasured"))
            throw Bad(label + ".state has an unknown value");
        return state;
    }

    private static QualityEvidence ParseQuality(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        if (State(value, label) == "unmeasured")
        {
            Keys(value, new[] { "state" }, Array.Empty<string>(), label);
            return new QualityEvidence(false, null, null, null, null, null, null, null);
        }
        Keys(value, new[] { "state", "retention", "recovery" }, Array.Empty<string>(), label);
        var retention = Fields(value["retention"], label + ".retention");
        Keys(retention, new[] { "retained", "recoverable", "lost" }, Array.Empty<string>(), label + ".retention");
        var recovery = Fields(value["recovery"], label + ".recovery");
        Keys(recovery, new[] { "handles_emitted", "handles_verified", "failures", "critical_failures" },
            Array.Empty<string>(), label + ".recovery");
        var emitted = Integer(recovery["handles_emitted"], label + ".handles_emitted");
        var verified = Integer(recovery["handles_verified"], label + ".handles_verified");
        var failures = Integer(recovery["failures"], label + ".failures");
        var critical = Integer(recovery["critical_failures"], label + ".critical_failures");
        if (verified > emitted || failures > emitted || critical > failures)
            throw Bad(label + " has impossible recovery counts");
        return new QualityEvidence(true,
            Integer(retention["retained"], label + ".retained"),
            Integer(retention["recoverable"], label + ".recoverable"),
            Integer(retention["lost"], label + ".lost"),
            emitted, verified, failures, critical);
    }

    private static SecurityEvidence ParseSecurity(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        if (State(value, label) == "unmeasured")
        {
            Keys(value, new[] { "state" }, Array.Empty<string>(), label);
            return new SecurityEvidence(false, null);
        }
        Keys(value, new[] { "state", "regressions" }, Array.Empty<string>(), label);
        return new SecurityEvidence(true, Integer(value["regressions"], label));
    }

    private static StrategyOutcomeRecord ParseRecord(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        Keys(value, RecordCounts.Concat(new[] { "workload", "strategy", "quality", "security" }).ToArray(), SignalCounts, label);
        long Count(string key) => Integer(value[key], label + "." + key);
        // Signal counts default to zero, like the Engine's own deserializer.
        long Signal(string key) => value.TryGetValue(key, out var count) ? Integer(count, label + "." + key) : 0;
        var record = new StrategyOutcomeRecord(
            ParseWorkload(value["workload"], label + ".workload"),
            OneOf(value["strategy"], Strategies, label + ".strategy"),
            Count("observed_day"), Count("samples"), Count("accepted"), Count("rejected"),
            Count("explicit_overrides"), Count("token_samples"),
            Signal("signal_samples"), Signal("bounce_tasks"), Signal("expand_tasks"), Signal("edit_failure_tasks"),
            Count("tokens_original"), Count("tokens_delivered"),
            ParseQuality(value["quality"], label + ".quality"),
            ParseSecurity(value["security"], label + ".security"));
        if (record.Samples == 0)
            throw Bad(label + " has no samples");
        if (record.Accepted + record.Rejected != record.Samples)
            throw Bad(label + ": accepted + rejected must equal samples");
        if (record.ExplicitOverrides > record.Samples || record.TokenSamples > record.Samples)
            throw Bad(label + " counts more tasks than samples");
        if (record.SignalSamples > record.Samples || record.BounceTasks > record.SignalSamples ||
            record.ExpandTasks > record.SignalSamples || record.EditFailureTasks > record.SignalSamples)
            throw Bad(label + " signal counts exceed their attributed tasks");
        if (record.TokenSamples == 0 && (record.TokensOriginal > 0 || record.TokensDelivered > 0))
            throw Bad(label + " has tokens without token samples");
        if (record.TokensDelivered > record.TokensOriginal)
            throw Bad(label + " delivered more tokens than original");
        return record;
    }

    private static StrategyEvaluation ParseEvaluation(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        Keys(value, new[]
        {
            "strategy", "evidence_tier", "verdict", "pairs", "powered",
            "delta_milli", "ci_low_milli", "ci_high_milli", "margin_milli",
        }, Array.Empty<string>(), label);
        var evaluation = new StrategyEvaluation(
            OneOf(value["strategy"], Strategies, label + ".strategy"),
            OneOf(value["evidence_tier"], EvidenceTiers, label + ".evidence_tier"),
            OneOf(value["verdict"], Verdicts, label + ".verdict"),
            Integer(value["pairs"], label + ".pairs"),
            Bool(value["powered"], label + ".powered"),
            Integer(value["delta_milli"], label + ".delta_milli", signed: true),
            Integer(value["ci_low_milli"], label + ".ci_low_milli", signed: true),
            Integer(value["ci_high_milli"], label + ".ci_high_milli", signed: true),
            Integer(value["margin_milli"], label + ".margin_milli", signed: true));
        if (evaluation.Strategy == "other")
            throw Bad(label + " must name a known strategy");
        if (evaluation.CiLowMilli > evaluation.CiHighMilli || evaluation.MarginMilli < 0)
            throw Bad(label + " has an impossible interval");
        if (evaluation.Powered && evaluation.Pairs == 0)
            throw Bad(label + " is powered without pairs");
        return evaluation;
    }

    private static int CompareRecords(StrategyOutcomeRecord left, StrategyOutcomeRecord right)
    {
        long[] a = { Array.IndexOf(TaskClasses, left.Workload.TaskClass), Array.IndexOf(Languages, left.Workload.Language),
            Array.IndexOf(Sizes, left.Workload.Size), Array.IndexOf(Strategies, left.Strategy), left.ObservedDay };
        long[] b = { Array.IndexOf(TaskClasses, right.Workload.TaskClass), Array.IndexOf(Languages, right.Workload.Language),
            Array.IndexOf(Sizes, right.Workload.Size), Array.IndexOf(Strategies, right.Strategy), right.ObservedDay };
        for (var i = 0; i < a.Length; i++)
        {
            if (a[i] != b[i])
                return a[i].CompareTo(b[i]);
        }
        return 0;
    }

    /// <summary>Parse and validate a ContextPolicyEvidenceV1 document.</summary>
    public static ContextPolicyEvidence ParsePolicyEvidence(JsonElement raw)
    {
        var value = Fields(raw, "evidence");
        Keys(value, new[] { "schema_version", "records" }, new[] { "evaluations" }, "evidence");
        if (Integer(value["schema_version"], "schema_version") != 1)
            throw Bad("unsupported evidence schema_version");
        var records = new List<StrategyOutcomeRecord>();
        foreach (var item in Items(value["records"], "records", MaxRecords))
        {
            var record = ParseRecord(item, $"records[{records.Count}]");
            if (records.Count > 0 && CompareRecords(records[^1], record) >= 0)
                throw Bad("records must be strictly sorted by workload, strategy and day");
            records.Add(record);
        }
        var evaluations = new List<StrategyEvaluation>();
        if (value.TryGetValue("evaluations", out var rawEvaluations))
        {
            foreach (var item in Items(rawEvaluations, "evaluations", Strategies.Length))
            {
                var evaluation = ParseEvaluation(item, $"evaluations[{evaluations.Count}]");
                if (evaluations.Count > 0 &&
                    Array.IndexOf(Strategies, evaluations[^1].Strategy) >= Array.IndexOf(Strategies, evaluation.Strategy))
                    throw Bad("evaluations must be one per strategy, sorted");
                evaluations.Add(evaluation);
            }
        }
        return new ContextPolicyEvidence(records.AsReadOnly(), evaluations.AsReadOnly());
    }

    private static LineageStep ParseStep(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        Keys(value, new[] { "sequence", "kind", "timestamp", "fields" }, Array.Empty<string>(), label);
        var fields = new SortedDictionary<string, string>(StringComparer.Ordinal);
        foreach (var entry in Fields(value["fields"], label + ".fields"))
        {
            if (!FieldPattern.IsMatch(entry.Key))
                throw Bad(label + ".fields has an invalid name");
            fields[entry.Key] = Text(entry.Value, label + ".fields." + entry.Key, MaxRefBytes);
        }
        return new LineageStep(
            Integer(value["sequence"], label + ".sequence"),
            OneOf(value["kind"], StepKinds, label + ".kind"),
            Text(value["timestamp"], label + ".timestamp", MaxRefBytes),
            new ReadOnlyDictionary<string, string>(new Dictionary<string, string>(fields, StringComparer.Ordinal)));
    }

    private static DeliverySummary ParseSummary(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        Keys(value, new[]
        {
            "outcome", "destination", "policy_digest", "final_context", "inspected", "delivered",
            "withheld", "redactions", "tokens_original", "tokens_delivered",
        }, Array.Empty<string>(), label);
        var summary = new DeliverySummary(
            OneOf(value["outcome"], DeliveryOutcomes, label + ".outcome"),
            Text(value["destination"], label + ".destination", MaxRefBytes),
            OptionalDigest(value["policy_digest"], label + ".policy_digest"),
            Integer(value["inspected"], label + ".inspected"),
            Integer(value["delivered"], label + ".delivered"),
            Integer(value["withheld"], label + ".withheld"),
            Integer(value["redactions"], label + ".redactions"),
            Integer(value["tokens_original"], label + ".tokens_original"),
            Integer(value["tokens_delivered"], label + ".tokens_delivered"),
            OptionalDigest(value["final_context"], label + ".final_context"));
        if (summary.TokensDelivered > summary.TokensOriginal)
            throw Bad(label + " delivered more tokens than original");
        return summary;
    }

    private static LineageDelivery ParseDelivery(JsonElement raw, string label)
    {
        var value = Fields(raw, label);
        Keys(value, new[] { "digest", "verified" }, new[] { "error", "summary" }, label);
        var verified = Bool(value["verified"], label + ".verified");
        if (verified != value.ContainsKey("summary") || verified == value.ContainsKey("error"))
            throw Bad(label + ": a verified delivery has a summary, an unverified one an error");
        return new LineageDelivery(
            Digest(value["digest"], label + ".digest"),
            verified,
            value.TryGetValue("error", out var error) ? OneOf(error, DeliveryErrors, label + ".error") : null,
            verified ? ParseSummary(value["summary"], label + ".summary") : null);
    }

    /// <summary>Parse and validate a TaskLineageV1 document.</summary>
    public static TaskLineage ParseTaskLineage(JsonElement raw)
    {
        var value = Fields(raw, "lineage");
        Keys(value, new[] { "schema_version", "task_id", "steps", "deliveries", "outcome", "gaps" },
            new[] { "scope", "ledger_error" }, "lineage");
        if (Integer(value["schema_version"], "schema_version") != 1)
            throw Bad("unsupported lineage schema_version");
        var gaps = new List<string>();
        foreach (var item in Items(value["gaps"], "gaps", GapNames.Length))
        {
            var gap = OneOf(item, GapNames, "gaps");
            if (gaps.Contains(gap))
                throw Bad("gaps must not repeat");
            gaps.Add(gap);
        }
        var ledgerError = value.TryGetValue("ledger_error", out var rawError)
            ? Text(rawError, "ledger_error", MaxTextBytes)
            : null;
        if ((ledgerError is not null) != gaps.Contains("ledger_unverified"))
            throw Bad("a ledger error and the ledger_unverified gap go together");
        var steps = Items(value["steps"], "steps", MaxLineageItems)
            .Select((item, position) => ParseStep(item, $"steps[{position}]")).ToList();
        var deliveries = Items(value["deliveries"], "deliveries", MaxLineageItems)
            .Select((item, position) => ParseDelivery(item, $"deliveries[{position}]")).ToList();
        if (deliveries.Any(delivery => !delivery.Verified) != gaps.Contains("delivery_unverified"))
            throw Bad("an unverified delivery and the delivery_unverified gap go together");
        return new TaskLineage(
            Text(value["task_id"], "task_id", MaxRefBytes),
            value.TryGetValue("scope", out var scope) ? Text(scope, "scope", MaxTextBytes) : null,
            steps.AsReadOnly(),
            deliveries.AsReadOnly(),
            ledgerError,
            OneOf(value["outcome"], Outcomes, "outcome"),
            gaps.AsReadOnly());
    }

    /// <summary>Parse an evidence document from its UTF-8 bytes.</summary>
    public static ContextPolicyEvidence ParsePolicyEvidence(byte[] utf8)
    {
        using var document = ParseDocument(utf8);
        return ParsePolicyEvidence(document.RootElement);
    }

    /// <summary>Parse a lineage document from its UTF-8 bytes.</summary>
    public static TaskLineage ParseTaskLineage(byte[] utf8)
    {
        using var document = ParseDocument(utf8);
        return ParseTaskLineage(document.RootElement);
    }

    private static JsonDocument ParseDocument(byte[] utf8)
    {
        try
        {
            return JsonDocument.Parse(utf8, new JsonDocumentOptions { AllowTrailingCommas = false, MaxDepth = 64 });
        }
        catch (JsonException error)
        {
            throw new EngineProtocolError("context store: response is not strict JSON", error);
        }
    }

    private static byte[] Request(ContextStoreScope scope, string? taskId)
    {
        var request = new SortedDictionary<string, object>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1,
            ["transport_version"] = 1,
            ["engine_interface_version"] = "1.0.0",
        };
        foreach (var (key, item) in new[] { ("project_id", scope.ProjectId), ("tenant_id", scope.TenantId), ("task_id", taskId) })
        {
            if (item is null)
                continue;
            if (string.IsNullOrWhiteSpace(item) || Encoding.UTF8.GetByteCount(item) > MaxRefBytes)
                throw new ValidationError(key + " must be a non-empty bounded string");
            request[key] = item;
        }
        return JsonSerializer.SerializeToUtf8Bytes(request);
    }

    private static async Task<JsonElement> ReadAsync(
        SubprocessEngineClient engine,
        string operation,
        string projectRoot,
        byte[] payload,
        string body,
        CancellationToken cancellationToken)
    {
        if (engine is null)
            throw new ValidationError("engine is required");
        if (payload.Length > MaxStoreRequestBytes)
            throw new ValidationError("context store request exceeds its bound");
        var root = SubprocessEngineClient.ValidatedRoot(projectRoot);
        var raw = await engine.RunPayloadAsync(operation, root, payload, cancellationToken).ConfigureAwait(false);
        if (raw.Length > MaxStoreResponseBytes)
            throw Bad("response exceeds its bound");
        using var document = ParseDocument(raw);
        var value = Fields(document.RootElement, "response");
        Keys(value, new[] { "schema_version", "transport_version", "engine_interface_version", body },
            Array.Empty<string>(), "response");
        if (Integer(value["schema_version"], "schema_version") != 1 ||
            Integer(value["transport_version"], "transport_version") != 1 ||
            value["engine_interface_version"].ValueKind != JsonValueKind.String ||
            value["engine_interface_version"].GetString() != "1.0.0")
            throw Bad("unsupported response versions");
        return value[body].Clone();
    }

    /// <summary>The scope's read-strategy evidence.</summary>
    public static async Task<ContextPolicyEvidence> ReadPolicyEvidenceAsync(
        SubprocessEngineClient engine,
        string projectRoot,
        ContextStoreScope? scope = null,
        CancellationToken cancellationToken = default)
    {
        var body = await ReadAsync(engine, "context-policy-evidence", projectRoot,
            Request(scope ?? new ContextStoreScope(), null), "evidence", cancellationToken).ConfigureAwait(false);
        return ParsePolicyEvidence(body);
    }

    /// <summary>One task's lineage within the scope.</summary>
    public static async Task<TaskLineage> ReadTaskLineageAsync(
        SubprocessEngineClient engine,
        string projectRoot,
        string taskId,
        ContextStoreScope? scope = null,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(taskId))
            throw new ValidationError("taskId must be a non-empty string");
        var body = await ReadAsync(engine, "context-lineage", projectRoot,
            Request(scope ?? new ContextStoreScope(), taskId), "lineage", cancellationToken).ConfigureAwait(false);
        return ParseTaskLineage(body);
    }
}
