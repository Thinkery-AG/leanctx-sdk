// Until 2.0 the SDK throws the deprecated Engine error subtypes so that both the
// Engine and the Instance names catch them (see Errors.cs).
#pragma warning disable CS0618
// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
using System.Collections.ObjectModel;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Security.Cryptography;

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
/// <summary>How a schema_version 2 receipt digests its decision objects: keyed under the installation key <c>KeyId</c>.</summary>
public sealed record ReceiptObjectDigest(string Algorithm, string KeyId);

/// <summary>
/// In schema_version 2 every decision object is HMAC-SHA256 under the installation's receipt key
/// (<c>ObjectDigest</c>); in schema_version 1 the plain SHA-256 of the object. <c>FinalContext</c> is plain SHA-256 in both.
/// </summary>
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
    JsonElement? Quality,
    long SchemaVersion = 1,
    ReceiptObjectDigest? ObjectDigest = null)
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

/// <summary>V2 decision plus exact JSON text to transmit unchanged.</summary>
public sealed record EgressPreparation(
    EgressAdmission Decision,
    string? PreparedBody,
    string? AdmissionReceiptDigest)
{
    public bool MaySend => Decision.MaySend && PreparedBody is not null;
}

/// <summary>One bounded host-reported transport attempt.</summary>
public sealed record EgressHostReportRequest(
    string AdmissionReceiptDigest,
    int Attempt,
    string Transport,
    string Result,
    int? HttpStatus = null,
    string? Failure = null);

/// <summary>LeanCTX instance report response with its HostReported observation intact.</summary>
public sealed record EgressHostReportResponse(string ObservationDigest, JsonElement Observation);

/// <summary>One model request about to leave for a provider.</summary>
public sealed record EgressRequest(string Provider, string UpstreamBase, IReadOnlyDictionary<string, object?> Body);

/// <summary>
/// Preview: the information gateway's egress admission and decision receipts.
/// AdmitEgressAsync runs one model request through the local instance's egress
/// admission (lean-ctx engine egress-admit) before the caller sends it. Parsing
/// mirrors the instance's validation; unknown fields and values are rejected.
/// Contract leanctx-gateway-preview 0.1 — may change in minor releases.
/// </summary>
public static class GatewayPreview
{
    public const string Contract = "leanctx-gateway-preview";
    public const string Version = "0.1.0";
    public const int EgressSchemaVersion = 1;
    public const int EgressPrepareSchemaVersion = 2;
    public const int MaxEgressRequestBytes = 8 * 1024 * 1024;
    public const int MaxEgressAttempts = 32;
    public const int MaxEgressReportBytes = 4096;

    private const int MaxRefBytes = 512;
    private const int MaxDecisions = 4096;
    private const int MaxReasonCodes = 32;
    private const int MaxSignals = 32;
    private const long U32 = uint.MaxValue;
    private const long MaxSafe = (1L << 53) - 1;
    private static readonly Regex DigestPattern = new("^sha256:[0-9a-f]{64}$", RegexOptions.CultureInvariant);
    private static readonly Regex KeyIdPattern = new("^rk-[0-9a-f]{32}$", RegexOptions.CultureInvariant);
    private static readonly byte[] ObjectDomain = Encoding.UTF8.GetBytes("leanctx.receipt.object.v2\0");
    private static readonly byte[] KeyIdDomain = Encoding.UTF8.GetBytes("leanctx.receipt.key-id.v2");
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
    private static byte[] ReceiptMac(byte[] key, params byte[][] parts)
    {
        if (key is null || key.Length != 32) throw new ValidationError("a receipt key is exactly 32 bytes");
        using var mac = new HMACSHA256(key);
        foreach (var part in parts) mac.TransformBlock(part, 0, part.Length, null, 0);
        mac.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
        return mac.Hash!;
    }

    /// <summary>The public id a schema_version 2 receipt names for the 32-byte <paramref name="key"/>.</summary>
    public static string ReceiptKeyId(byte[] key) =>
        "rk-" + Convert.ToHexString(ReceiptMac(key, KeyIdDomain)).ToLowerInvariant()[..32];

    /// <summary>The schema_version 2 object for the plain <c>sha256:&lt;hex&gt;</c> a schema_version 1 receipt would name.</summary>
    public static string KeyedObjectDigest(byte[] key, string plainObject)
    {
        if (plainObject is null || !DigestPattern.IsMatch(plainObject))
            throw new ValidationError("plainObject must be a sha256 digest");
        return "sha256:" + Convert.ToHexString(
            ReceiptMac(key, ObjectDomain, Encoding.ASCII.GetBytes(plainObject))).ToLowerInvariant();
    }

    private static ReceiptObjectDigest ObjectDigestOf(JsonElement raw)
    {
        var value = Fields(raw, "receipt.object_digest");
        Keys(value, new[] { "algorithm", "key_id" }, Array.Empty<string>(), "receipt.object_digest");
        var keyId = value["key_id"];
        if (keyId.ValueKind != JsonValueKind.String || !KeyIdPattern.IsMatch(keyId.GetString()!))
            throw Bad("receipt.object_digest.key_id must be rk- and 32 hex digits");
        return new ReceiptObjectDigest(
            OneOf(value["algorithm"], new[] { "hmac-sha256" }, "receipt.object_digest.algorithm"),
            keyId.GetString()!);
    }

    public static ContextDecisionReceipt ParseDecisionReceipt(JsonElement raw)
    {
        var value = Fields(raw, "receipt");
        Keys(value, new[]
            {
                "schema_version", "receipt_id", "mode", "principal", "destination", "sources",
                "security", "tokens", "outcome", "duration_us",
            },
            new[] { "task", "policy", "decisions", "final_context", "quality", "object_digest" }, "receipt");
        var schemaVersion = Integer(value["schema_version"], "receipt.schema_version", MaxSafe);
        if (schemaVersion is not (1 or 2))
            throw Bad("unsupported receipt schema_version");
        if ((schemaVersion == 2) != value.ContainsKey("object_digest"))
            throw Bad("exactly a schema_version 2 receipt names its object digest key");
        ReceiptObjectDigest? objectDigest = value.TryGetValue("object_digest", out var rawKey)
            ? ObjectDigestOf(rawKey)
            : null;
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
            quality,
            schemaVersion,
            objectDigest);
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

    /// <summary>Parse EngineEgressPrepareResponseV2 and retain prepared_body verbatim.</summary>
    public static EgressPreparation ParseEgressPreparation(JsonElement raw)
    {
        var value = Fields(raw, "preparation");
        Keys(value, new[] { "schema_version", "decision" }, new[] { "prepared_body", "admission_receipt_digest" }, "preparation");
        if (Integer(value["schema_version"], "schema_version", MaxSafe) != EgressPrepareSchemaVersion)
            throw Bad("unsupported egress preparation schema_version");
        var decision = ParseEgressAdmission(value["decision"]);
        if (decision.Receipt is { } receipt)
        {
            var mismatch = decision.Disposition == "refused"
                ? receipt.Outcome == "delivered" || receipt.FinalContext is not null
                : receipt.Outcome != "delivered" || receipt.FinalContext is null;
            if (mismatch) throw Bad("decision receipt outcome/final_context do not match disposition");
        }
        var hasPrepared = value.TryGetValue("prepared_body", out var rawPrepared);
        var prepared = hasPrepared && rawPrepared.ValueKind == JsonValueKind.String ? rawPrepared.GetString() : null;
        if (decision.Disposition == "refused")
        {
            if (hasPrepared && rawPrepared.ValueKind != JsonValueKind.Null)
                throw Bad("a refused preparation cannot carry prepared bytes");
        }
        else
        {
            if (prepared is null)
                throw Bad("a dispatchable preparation must carry exact bytes");
            JsonDocument bodyDocument;
            try { bodyDocument = JsonDocument.Parse(Encoding.UTF8.GetBytes(prepared), new JsonDocumentOptions { AllowTrailingCommas = false, MaxDepth = 64 }); }
            catch (JsonException error) { throw new EngineProtocolError("egress admission: prepared bytes are not strict JSON", error); }
            using (bodyDocument)
            {
                if (bodyDocument.RootElement.ValueKind != JsonValueKind.Object || decision.Body is null || !JsonEquivalent(bodyDocument.RootElement, decision.Body.Value))
                    throw Bad("prepared bytes do not match the admitted decision body");
            }
            var finalContext = decision.Receipt?.FinalContext;
            if (finalContext is not null)
            {
                var digest = "sha256:" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(prepared))).ToLowerInvariant();
                if (!string.Equals(finalContext, digest, StringComparison.Ordinal))
                    throw Bad("prepared bytes do not match the decision receipt digest");
            }
        }
        string? admissionDigest = null;
        if (value.TryGetValue("admission_receipt_digest", out var rawDigest) && rawDigest.ValueKind != JsonValueKind.Null)
        {
            admissionDigest = Digest(rawDigest, "admission_receipt_digest");
            if (decision.Receipt is null) throw Bad("egress admission digest requires its decision receipt");
        }
        return new EgressPreparation(decision, prepared, admissionDigest);
    }

    public static EgressPreparation ParseEgressPreparation(byte[] utf8)
    {
        try
        {
            using var document = JsonDocument.Parse(utf8, new JsonDocumentOptions { AllowTrailingCommas = false, MaxDepth = 64 });
            return ParseEgressPreparation(document.RootElement);
        }
        catch (JsonException error) { throw new EngineProtocolError("egress admission: response is not strict JSON", error); }
    }

    private static bool JsonEquivalent(JsonElement left, JsonElement right)
    {
        if (left.ValueKind != right.ValueKind)
        {
            if (left.ValueKind == JsonValueKind.Number && right.ValueKind == JsonValueKind.Number)
                return left.TryGetDecimal(out var ld) && right.TryGetDecimal(out var rd) ? ld == rd : left.GetDouble() == right.GetDouble();
            return false;
        }
        return left.ValueKind switch
        {
            JsonValueKind.Object => JsonObjectEquivalent(left, right),
            JsonValueKind.Array => left.GetArrayLength() == right.GetArrayLength() &&
                left.EnumerateArray().Zip(right.EnumerateArray()).All(pair => JsonEquivalent(pair.First, pair.Second)),
            JsonValueKind.String => left.GetString() == right.GetString(),
            JsonValueKind.Number => left.TryGetDecimal(out var ld) && right.TryGetDecimal(out var rd) ? ld == rd : left.GetDouble() == right.GetDouble(),
            JsonValueKind.True or JsonValueKind.False => left.GetBoolean() == right.GetBoolean(),
            JsonValueKind.Null => true,
            _ => false,
        };
    }

    private static bool JsonObjectEquivalent(JsonElement left, JsonElement right)
    {
        var a = Fields(left, "prepared_body");
        var b = Fields(right, "decision.body");
        return a.Count == b.Count && a.All(pair => b.TryGetValue(pair.Key, out var other) && JsonEquivalent(pair.Value, other));
    }

    private static Dictionary<string, object?> BuildHostReportRequest(EgressHostReportRequest request)
    {
        if (!DigestPattern.IsMatch(request.AdmissionReceiptDigest)) throw new ValidationError("admission_receipt_digest must be a sha256 digest");
        if (request.Attempt is < 1 or > MaxEgressAttempts) throw new ValidationError("attempt must be in 1..32");
        if (request.Transport is not ("http" or "websocket")) throw new ValidationError("transport is invalid");
        if (request.Result is not ("response_observed" or "no_response_observed")) throw new ValidationError("result is invalid");
        if (request.Failure is not null && request.Failure is not ("connect" or "timeout" or "cancelled" or "other")) throw new ValidationError("failure is invalid");
        if (request.HttpStatus is < 100 or > 599) throw new ValidationError("http_status must be in 100..599");
        if (request.Result == "response_observed")
        {
            if (request.Failure is not null || (request.Transport == "http" && request.HttpStatus is null))
                throw new ValidationError("response_observed requires no failure and an HTTP status for HTTP");
        }
        else if (request.HttpStatus is not null || request.Failure is null)
            throw new ValidationError("no_response_observed requires a bounded failure and no HTTP status");
        var document = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = EgressSchemaVersion,
            ["admission_receipt_digest"] = request.AdmissionReceiptDigest,
            ["attempt"] = request.Attempt,
            ["transport"] = request.Transport,
            ["result"] = request.Result,
        };
        if (request.HttpStatus is not null) document["http_status"] = request.HttpStatus;
        if (request.Failure is not null) document["failure"] = request.Failure;
        return document;
    }

    public static EgressHostReportResponse ParseEgressHostReport(JsonElement raw, EgressHostReportRequest request)
    {
        var expected = BuildHostReportRequest(request);
        var value = Fields(raw, "report");
        Keys(value, new[] { "schema_version", "observation_digest", "observation" }, Array.Empty<string>(), "report");
        if (Integer(value["schema_version"], "schema_version", MaxSafe) != EgressSchemaVersion)
            throw Bad("unsupported egress report schema_version");
        var observationDigest = Digest(value["observation_digest"], "observation_digest");
        var observation = Fields(value["observation"], "observation");
        Keys(observation, new[] { "schema_version", "admission_receipt_digest", "attempt", "transport", "observer", "result" },
            new[] { "http_status", "failure" }, "observation");
        if (Integer(observation["schema_version"], "observation.schema_version", MaxSafe) != EgressSchemaVersion)
            throw Bad("unsupported observation schema_version");
        if (OneOf(observation["observer"], new[] { "host_reported" }, "observation.observer") != "host_reported")
            throw Bad("host report response must be HostReported");
        foreach (var pair in expected)
        {
            if (!observation.TryGetValue(pair.Key, out var actual)) throw Bad("report observation does not match request field " + pair.Key);
            var expectedJson = JsonSerializer.SerializeToElement(pair.Value);
            if (!JsonEquivalent(actual, expectedJson)) throw Bad("report observation does not match request field " + pair.Key);
        }
        foreach (var key in observation.Keys)
            if (key != "observer" && !expected.ContainsKey(key)) throw Bad("report observation has unexpected field " + key);
        var canonicalObservation = new SortedDictionary<string, object?>(expected, StringComparer.Ordinal)
        {
            ["observer"] = "host_reported",
        };
        var canonicalBytes = JsonSerializer.SerializeToUtf8Bytes(canonicalObservation);
        var canonicalDigest = "sha256:" + Convert.ToHexString(SHA256.HashData(canonicalBytes)).ToLowerInvariant();
        if (!string.Equals(observationDigest, canonicalDigest, StringComparison.Ordinal))
            throw Bad("observation_digest does not match the reported observation");
        return new EgressHostReportResponse(observationDigest, value["observation"].Clone());
    }

    public static EgressHostReportResponse ParseEgressHostReport(byte[] utf8, EgressHostReportRequest request)
    {
        try
        {
            using var document = JsonDocument.Parse(utf8, new JsonDocumentOptions { AllowTrailingCommas = false, MaxDepth = 64 });
            return ParseEgressHostReport(document.RootElement, request);
        }
        catch (JsonException error) { throw new EngineProtocolError("egress admission: response is not strict JSON", error); }
    }

    /// <summary>Run the explicit preparation V2 operation; send PreparedBody verbatim.</summary>
    public static async Task<EgressPreparation> PrepareEgressAsync(
        SubprocessInstanceClient engine,
        string projectRoot,
        EgressRequest request,
        CancellationToken cancellationToken = default)
    {
        if (engine is null) throw new ValidationError("instance is required");
        if (request is null || string.IsNullOrWhiteSpace(request.Provider)) throw new ValidationError("provider must be a non-empty string");
        if (request.UpstreamBase is null || !(request.UpstreamBase.StartsWith("https://", StringComparison.Ordinal) || request.UpstreamBase.StartsWith("http://", StringComparison.Ordinal)))
            throw new ValidationError("upstream base must be an http(s) URL");
        if (request.Body is null) throw new ValidationError("body must be a JSON object");
        var root = SubprocessInstanceClient.ValidatedRoot(projectRoot);
        byte[] payload;
        try
        {
            payload = JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["schema_version"] = EgressSchemaVersion, ["provider"] = request.Provider,
                ["upstream_base"] = request.UpstreamBase, ["body"] = request.Body,
            });
        }
        catch (Exception error) when (error is NotSupportedException or JsonException or ArgumentException)
        { throw new ValidationError("body is not JSON data", error); }
        if (payload.Length > MaxEgressRequestBytes) throw new ValidationError("egress request exceeds its bound");
        var raw = await engine.RunPayloadAsync("egress-prepare", root, payload, cancellationToken).ConfigureAwait(false);
        if (raw.Length > 32 * 1024 * 1024) throw Bad("preparation response exceeds its bound");
        return ParseEgressPreparation(raw);
    }

    /// <summary>Submit bounded transport facts observed by the host.</summary>
    public static async Task<EgressHostReportResponse> ReportEgressAsync(
        SubprocessInstanceClient engine,
        string projectRoot,
        EgressHostReportRequest request,
        CancellationToken cancellationToken = default)
    {
        if (engine is null) throw new ValidationError("instance is required");
        var document = BuildHostReportRequest(request);
        var payload = JsonSerializer.SerializeToUtf8Bytes(document);
        if (payload.Length > MaxEgressReportBytes) throw new ValidationError("egress report request exceeds its bound");
        var root = SubprocessInstanceClient.ValidatedRoot(projectRoot);
        var raw = await engine.RunPayloadAsync("egress-report", root, payload, cancellationToken).ConfigureAwait(false);
        if (raw.Length > 32 * 1024 * 1024) throw Bad("report response exceeds its bound");
        return ParseEgressHostReport(raw, request);
    }

    /// <summary>
    /// Admit one model request through the local LeanCTX instance before it is sent. Send
    /// admission.Body, never the original body, and only when MaySend.
    /// </summary>
    public static async Task<EgressAdmission> AdmitEgressAsync(
        SubprocessInstanceClient engine,
        string projectRoot,
        EgressRequest request,
        CancellationToken cancellationToken = default)
    {
        if (engine is null)
            throw new ValidationError("instance is required");
        if (request is null || string.IsNullOrWhiteSpace(request.Provider))
            throw new ValidationError("provider must be a non-empty string");
        if (request.UpstreamBase is null ||
            !(request.UpstreamBase.StartsWith("https://", StringComparison.Ordinal) ||
              request.UpstreamBase.StartsWith("http://", StringComparison.Ordinal)))
            throw new ValidationError("upstream base must be an http(s) URL");
        if (request.Body is null)
            throw new ValidationError("body must be a JSON object");
        var root = SubprocessInstanceClient.ValidatedRoot(projectRoot);
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
