// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
using System.Collections.ObjectModel;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Thinkery.LeanCtx.Preview;

/// <summary>Who requested the context; "unknown" is explicit and never authorizes.</summary>
public sealed record ContextPrincipal(string Kind, string? Id)
{
    public bool IsKnown => Kind != "unknown";
}

/// <summary>Where the context goes; organisation management is only ever attested.</summary>
public sealed record ContextDestination(
    string Provider,
    string Locality,
    string? Model,
    bool OrganizationManaged,
    string? AccountRef,
    string? Region);

/// <summary>What a detector actually inspected; never more than recorded.</summary>
public sealed record DetectorCoverage(
    string Kind,
    long BytesTotal,
    long BytesInspected,
    long ChunksTotal,
    long ChunksInspected,
    string? Reason)
{
    public bool IsComplete => Kind == "complete";
}

/// <summary>One detector's result: counts only, never the matched value.</summary>
public sealed record SecuritySignal(
    string DetectorId,
    string DetectorVersion,
    string Category,
    string Severity,
    long EvidenceCount,
    DetectorCoverage Coverage,
    string Status,
    long LatencyUs,
    bool Calibrated,
    long? ConfidenceMilli);

/// <summary>The gateway's decision about one object (by digest, never content).</summary>
public sealed record ContextDecision(
    string Object,
    string Disposition,
    IReadOnlyList<string> ReasonCodes,
    IReadOnlyList<SecuritySignal> Signals,
    IReadOnlyList<string> RequiredTransformations)
{
    public bool DeliversContent =>
        GatewayPreview.Rank(Disposition) <= GatewayPreview.Rank("allow_local_model_only");
}

/// <summary>One governed delivery: who, where, under which policy, and what was withheld.</summary>
public sealed record ContextDecisionReceipt(
    string ReceiptId,
    string Mode,
    ContextPrincipal Principal,
    ContextDestination Destination,
    IReadOnlyDictionary<string, long> Sources,
    IReadOnlyDictionary<string, long> Security,
    IReadOnlyDictionary<string, long> Tokens,
    string Outcome,
    long DurationUs,
    IReadOnlyList<ContextDecision> Decisions,
    IReadOnlyDictionary<string, string>? Policy,
    string? Task,
    string? FinalContext,
    JsonElement? Quality)
{
    public IReadOnlyList<SecuritySignal> Signals =>
        Decisions.SelectMany(decision => decision.Signals).ToList().AsReadOnly();
}

/// <summary>What may leave for the model, how sensitive it is, and why.</summary>
public sealed record EgressAdmission(
    string Disposition,
    JsonElement? Body,
    string? Refusal,
    string? Classification,
    ContextDecisionReceipt? Receipt)
{
    /// <summary>True when Body may be sent; a refused request must not be.</summary>
    public bool MaySend => Disposition != "refused";
}

/// <summary>One model request about to leave for a provider.</summary>
public sealed record EgressRequest(string Provider, string UpstreamBase, IReadOnlyDictionary<string, object?> Body);

/// <summary>
/// Preview: the information gateway's egress admission and decision receipts.
/// AdmitEgressAsync runs one model request through the local Engine's egress
/// admission (lean-ctx engine egress-admit) before the caller sends it. Parsing
/// mirrors the Engine's validation; unknown fields and values are rejected.
/// Contract leanctx-gateway-preview 0.1 — may change in minor releases.
/// </summary>
public static class GatewayPreview
{
    public const string Contract = "leanctx-gateway-preview";
    public const string Version = "0.1.0";
    public const int EgressSchemaVersion = 1;
    public const int MaxEgressRequestBytes = 8 * 1024 * 1024;

    private const int MaxRefBytes = 512;
    private const int MaxDecisions = 4096;
    private const int MaxReasonCodes = 32;
    private const int MaxSignals = 32;
    private const long U32 = uint.MaxValue;
    private const long MaxSafe = (1L << 53) - 1;
    private static readonly Regex DigestPattern = new("^sha256:[0-9a-f]{64}$", RegexOptions.CultureInvariant);
    private static readonly Regex ReasonPattern = new("^[a-z][a-z0-9_.]{2,63}$", RegexOptions.CultureInvariant);

    private static readonly string[] Classifications = { "public", "internal", "confidential", "restricted" };
    private static readonly string[] Dispositions = { "forward", "rewritten", "refused" };
    private static readonly string[] Modes = { "developer", "governed", "sovereign" };
    private static readonly string[] PrincipalKinds =
        { "person", "team", "organization", "project", "agent", "session", "workload", "unknown" };
    private static readonly string[] Localities = { "local", "remote", "unknown" };
    private static readonly string[] Outcomes = { "delivered", "withheld", "failed" };
    private static readonly string[] ContextDispositions =
    {
        "allow", "allow_minimized", "allow_redacted", "allow_summary_only",
        "allow_local_model_only", "allow_with_approval", "quarantine", "deny",
    };
    private static readonly string[] Transformations =
    {
        "redaction", "classification", "selection", "deduplication",
        "structural_extraction", "compression", "summarization", "recovery", "reranking",
    };
    private static readonly string[] Categories =
        { "secret", "pii", "prompt_injection", "classification", "policy", "custom" };
    private static readonly string[] Severities = { "info", "low", "medium", "high", "critical" };
    private static readonly string[] CoverageKinds = { "complete", "partial", "unsupported", "failed", "not_required" };
    private static readonly string[] DetectorStatuses = { "completed", "failed", "timed_out", "skipped" };

    internal static int Rank(string disposition) => Array.IndexOf(ContextDispositions, disposition);

    private static EngineProtocolError Bad(string message) => new("egress admission: " + message);

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

    private static long Integer(JsonElement value, string label, long maximum)
    {
        if (value.ValueKind != JsonValueKind.Number)
            throw Bad(label + " must be an integer");
        var raw = value.GetRawText();
        if (raw.IndexOfAny(new[] { '.', 'e', 'E', '-' }) >= 0 || !value.TryGetInt64(out var number) ||
            number < 0 || number > maximum)
            throw Bad($"{label} must be an integer in 0..{maximum}");
        return number;
    }

    private static bool Bool(Dictionary<string, JsonElement> value, string key, string label)
    {
        if (!value.TryGetValue(key, out var raw))
            return false;
        return raw.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => throw Bad(label + " must be a boolean"),
        };
    }

    private static string Ref(JsonElement value, string label)
    {
        if (value.ValueKind != JsonValueKind.String)
            throw Bad(label + " must be a bounded reference");
        var text = value.GetString() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(text) || Encoding.UTF8.GetByteCount(text) > MaxRefBytes ||
            text.Any(character => character < 0x20 || character == 0x7f))
            throw Bad(label + " must be a bounded reference");
        return text;
    }

    private static string? OptionalRef(Dictionary<string, JsonElement> value, string key, string label) =>
        value.TryGetValue(key, out var raw) ? Ref(raw, label) : null;

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

    private static IReadOnlyList<string> Reasons(Dictionary<string, JsonElement> value, string key, string label)
    {
        if (!value.TryGetValue(key, out var raw))
            return Array.Empty<string>();
        if (raw.ValueKind != JsonValueKind.Array || raw.GetArrayLength() > MaxReasonCodes)
            throw Bad(label + " must be a bounded list");
        var codes = new List<string>();
        foreach (var code in raw.EnumerateArray())
        {
            var text = code.ValueKind == JsonValueKind.String ? code.GetString() : null;
            if (text is null || !ReasonPattern.IsMatch(text))
                throw Bad(label + " holds an invalid reason code");
            codes.Add(text);
        }
        return codes.AsReadOnly();
    }

    private static List<JsonElement> Items(Dictionary<string, JsonElement> value, string key, string label, int maximum)
    {
        if (!value.TryGetValue(key, out var raw))
            return new List<JsonElement>();
        if (raw.ValueKind != JsonValueKind.Array || raw.GetArrayLength() > maximum)
            throw Bad(label + " must be a bounded list");
        return raw.EnumerateArray().ToList();
    }

    private static ContextPrincipal Principal(JsonElement raw)
    {
        var value = Fields(raw, "principal");
        Keys(value, new[] { "kind" }, new[] { "id" }, "principal");
        var kind = OneOf(value["kind"], PrincipalKinds, "principal.kind");
        var hasId = value.TryGetValue("id", out var id);
        if (kind == "unknown")
        {
            if (hasId)
                throw Bad("an unknown principal must not carry an identity");
            return new ContextPrincipal(kind, null);
        }
        if (!hasId)
            throw Bad("a known principal requires an id");
        return new ContextPrincipal(kind, Ref(id, "principal.id"));
    }

    private static ContextDestination Destination(JsonElement raw)
    {
        var value = Fields(raw, "destination");
        Keys(value, new[] { "provider", "locality" },
            new[] { "model", "organization_managed", "account_ref", "region" }, "destination");
        return new ContextDestination(
            Ref(value["provider"], "destination.provider"),
            OneOf(value["locality"], Localities, "destination.locality"),
            OptionalRef(value, "model", "destination.model"),
            Bool(value, "organization_managed", "destination.organization_managed"),
            OptionalRef(value, "account_ref", "destination.account_ref"),
            OptionalRef(value, "region", "destination.region"));
    }

    private static DetectorCoverage Coverage(JsonElement raw)
    {
        var value = Fields(raw, "coverage");
        Keys(value, new[] { "kind", "bytes_total", "bytes_inspected", "chunks_total", "chunks_inspected" },
            new[] { "reason" }, "coverage");
        string? reason = null;
        if (value.TryGetValue("reason", out var rawReason))
        {
            var text = rawReason.ValueKind == JsonValueKind.String ? rawReason.GetString() : null;
            if (text is null || !ReasonPattern.IsMatch(text))
                throw Bad("coverage.reason holds an invalid reason code");
            reason = text;
        }
        var parsed = new DetectorCoverage(
            OneOf(value["kind"], CoverageKinds, "coverage.kind"),
            Integer(value["bytes_total"], "coverage.bytes_total", MaxSafe),
            Integer(value["bytes_inspected"], "coverage.bytes_inspected", MaxSafe),
            Integer(value["chunks_total"], "coverage.chunks_total", U32),
            Integer(value["chunks_inspected"], "coverage.chunks_inspected", U32),
            reason);
        if (parsed.BytesInspected > parsed.BytesTotal || parsed.ChunksInspected > parsed.ChunksTotal)
            throw Bad("coverage must not inspect more than the object holds");
        var allBytes = parsed.BytesInspected == parsed.BytesTotal;
        if (parsed.Kind == "complete" && !allBytes)
            throw Bad("complete coverage must inspect every byte");
        if (parsed.Kind == "partial" && allBytes)
            throw Bad("partial coverage must leave bytes uninspected");
        return parsed;
    }

    private static SecuritySignal Signal(JsonElement raw)
    {
        var value = Fields(raw, "signal");
        Keys(value, new[] { "detector", "category", "severity", "evidence_count", "coverage", "status", "latency_us" },
            new[] { "calibrated", "confidence_milli" }, "signal");
        var detector = Fields(value["detector"], "signal.detector");
        Keys(detector, new[] { "id", "version" }, Array.Empty<string>(), "signal.detector");
        var parsed = new SecuritySignal(
            Ref(detector["id"], "signal.detector.id"),
            Ref(detector["version"], "signal.detector.version"),
            OneOf(value["category"], Categories, "signal.category"),
            OneOf(value["severity"], Severities, "signal.severity"),
            Integer(value["evidence_count"], "signal.evidence_count", U32),
            Coverage(value["coverage"]),
            OneOf(value["status"], DetectorStatuses, "signal.status"),
            Integer(value["latency_us"], "signal.latency_us", MaxSafe),
            Bool(value, "calibrated", "signal.calibrated"),
            value.TryGetValue("confidence_milli", out var confidence)
                ? Integer(confidence, "signal.confidence_milli", 1000)
                : null);
        if ((parsed.Status == "failed" || parsed.Status == "timed_out") && parsed.Coverage.IsComplete)
            throw Bad("a failed or timed-out detector cannot claim complete coverage");
        return parsed;
    }

    private static ContextDecision Decision(JsonElement raw)
    {
        var value = Fields(raw, "decision");
        Keys(value, new[] { "object", "disposition" },
            new[] { "reason_codes", "signals", "required_transformations" }, "decision");
        var parsed = new ContextDecision(
            Digest(value["object"], "decision.object"),
            OneOf(value["disposition"], ContextDispositions, "decision.disposition"),
            Reasons(value, "reason_codes", "decision.reason_codes"),
            Items(value, "signals", "decision.signals", MaxSignals).Select(Signal).ToList().AsReadOnly(),
            Items(value, "required_transformations", "decision.required_transformations", int.MaxValue)
                .Select(kind => OneOf(kind, Transformations, "decision.required_transformations"))
                .ToList().AsReadOnly());
        if (parsed.Disposition != "allow" && parsed.ReasonCodes.Count == 0)
            throw Bad("every non-allow decision requires at least one reason code");
        return parsed;
    }

    private static IReadOnlyDictionary<string, long> Counts(JsonElement raw, string[] names, string label, long maximum)
    {
        var value = Fields(raw, label);
        Keys(value, names, Array.Empty<string>(), label);
        var counts = new Dictionary<string, long>(StringComparer.Ordinal);
        foreach (var name in names)
            counts[name] = Integer(value[name], label + "." + name, maximum);
        return new ReadOnlyDictionary<string, long>(counts);
    }

    /// <summary>Parse and validate a ContextDecisionReceiptV1 document.</summary>
    public static ContextDecisionReceipt ParseDecisionReceipt(JsonElement raw)
    {
        var value = Fields(raw, "receipt");
        Keys(value, new[]
            {
                "schema_version", "receipt_id", "mode", "principal", "destination", "sources",
                "security", "tokens", "outcome", "duration_us",
            },
            new[] { "task", "policy", "decisions", "final_context", "quality" }, "receipt");
        if (Integer(value["schema_version"], "receipt.schema_version", MaxSafe) != 1)
            throw Bad("unsupported receipt schema_version");
        var decisions = Items(value, "decisions", "receipt.decisions", MaxDecisions)
            .Select(Decision).ToList().AsReadOnly();
        var sources = Counts(value["sources"], new[] { "inspected", "permitted", "selected", "blocked" },
            "receipt.sources", U32);
        if (sources["selected"] > sources["permitted"] ||
            sources["permitted"] + sources["blocked"] > sources["inspected"])
            throw Bad("source counts must satisfy selected <= permitted and permitted + blocked <= inspected");
        var security = Counts(value["security"], new[]
        {
            "redactions", "blocked_objects", "quarantined_objects", "injection_signals", "incomplete_coverage",
        }, "receipt.security", U32);
        if (security["blocked_objects"] != decisions.Count(d => d.Disposition == "deny") ||
            security["quarantined_objects"] != decisions.Count(d => d.Disposition == "quarantine"))
            throw Bad("security counts must equal the recorded deny/quarantine decisions");
        var outcome = OneOf(value["outcome"], Outcomes, "receipt.outcome");
        string? finalContext = value.TryGetValue("final_context", out var rawFinal)
            ? Digest(rawFinal, "receipt.final_context")
            : null;
        if ((outcome == "delivered") != (finalContext is not null))
            throw Bad("exactly a delivered receipt names the delivered context digest");
        IReadOnlyDictionary<string, string>? policy = null;
        if (value.TryGetValue("policy", out var rawPolicy))
        {
            var policyValue = Fields(rawPolicy, "receipt.policy");
            Keys(policyValue, new[] { "id", "digest" }, new[] { "version" }, "receipt.policy");
            var parsed = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["id"] = Ref(policyValue["id"], "receipt.policy.id"),
                ["digest"] = Digest(policyValue["digest"], "receipt.policy.digest"),
            };
            if (policyValue.TryGetValue("version", out var version))
                parsed["version"] = Ref(version, "receipt.policy.version");
            policy = new ReadOnlyDictionary<string, string>(parsed);
        }
        JsonElement? quality = null;
        if (value.TryGetValue("quality", out var rawQuality))
        {
            Fields(rawQuality, "receipt.quality");
            quality = rawQuality.Clone();
        }
        return new ContextDecisionReceipt(
            Ref(value["receipt_id"], "receipt.receipt_id"),
            OneOf(value["mode"], Modes, "receipt.mode"),
            Principal(value["principal"]),
            Destination(value["destination"]),
            sources,
            security,
            Counts(value["tokens"], new[] { "original", "delivered" }, "receipt.tokens", MaxSafe),
            outcome,
            Integer(value["duration_us"], "receipt.duration_us", MaxSafe),
            decisions,
            policy,
            OptionalRef(value, "task", "receipt.task"),
            finalContext,
            quality);
    }

    /// <summary>Parse and validate an EngineEgressAdmissionResponseV1 document.</summary>
    public static EgressAdmission ParseEgressAdmission(JsonElement raw)
    {
        var value = Fields(raw, "response");
        Keys(value, new[] { "schema_version", "disposition" },
            new[] { "body", "refusal", "classification", "receipt" }, "response");
        if (Integer(value["schema_version"], "schema_version", MaxSafe) != EgressSchemaVersion)
            throw Bad("unsupported egress schema_version");
        var disposition = OneOf(value["disposition"], Dispositions, "disposition");
        var hasBody = value.TryGetValue("body", out var body);
        var hasRefusal = value.TryGetValue("refusal", out var refusal);
        string? refusalText = null;
        if (disposition == "refused")
        {
            refusalText = hasRefusal && refusal.ValueKind == JsonValueKind.String ? refusal.GetString() : null;
            if (hasBody || string.IsNullOrWhiteSpace(refusalText))
                throw Bad("a refused request carries a refusal and no body");
        }
        else if (hasRefusal || !hasBody || body.ValueKind != JsonValueKind.Object)
        {
            throw Bad("an admitted request carries a body object and no refusal");
        }
        return new EgressAdmission(
            disposition,
            hasBody ? body.Clone() : null,
            refusalText,
            value.TryGetValue("classification", out var classification)
                ? OneOf(classification, Classifications, "classification")
                : null,
            value.TryGetValue("receipt", out var receipt) ? ParseDecisionReceipt(receipt) : null);
    }

    /// <summary>Parse an egress admission response document from its UTF-8 bytes.</summary>
    public static EgressAdmission ParseEgressAdmission(byte[] utf8)
    {
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(utf8, new JsonDocumentOptions { AllowTrailingCommas = false, MaxDepth = 64 });
        }
        catch (JsonException error)
        {
            throw new EngineProtocolError("egress admission: response is not strict JSON", error);
        }
        using (document)
            return ParseEgressAdmission(document.RootElement);
    }

    /// <summary>
    /// Admit one model request through the local Engine before it is sent. Send
    /// admission.Body, never the original body, and only when MaySend.
    /// </summary>
    public static async Task<EgressAdmission> AdmitEgressAsync(
        SubprocessEngineClient engine,
        string projectRoot,
        EgressRequest request,
        CancellationToken cancellationToken = default)
    {
        if (engine is null)
            throw new ValidationError("engine is required");
        if (request is null || string.IsNullOrWhiteSpace(request.Provider))
            throw new ValidationError("provider must be a non-empty string");
        if (request.UpstreamBase is null ||
            !(request.UpstreamBase.StartsWith("https://", StringComparison.Ordinal) ||
              request.UpstreamBase.StartsWith("http://", StringComparison.Ordinal)))
            throw new ValidationError("upstream base must be an http(s) URL");
        if (request.Body is null)
            throw new ValidationError("body must be a JSON object");
        var root = SubprocessEngineClient.ValidatedRoot(projectRoot);
        // Model requests carry fractional numbers (temperature, top_p): plain JSON.
        byte[] payload;
        try
        {
            payload = JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["schema_version"] = EgressSchemaVersion,
                ["provider"] = request.Provider,
                ["upstream_base"] = request.UpstreamBase,
                ["body"] = request.Body,
            });
        }
        catch (Exception error) when (error is NotSupportedException or JsonException or ArgumentException)
        {
            throw new ValidationError("body is not JSON data", error);
        }
        if (payload.Length > MaxEgressRequestBytes)
            throw new ValidationError("egress request exceeds its bound");
        var raw = await engine.RunPayloadAsync("egress-admit", root, payload, cancellationToken).ConfigureAwait(false);
        return ParseEgressAdmission(raw);
    }
}
