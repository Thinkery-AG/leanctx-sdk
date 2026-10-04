using System.Collections.ObjectModel;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Thinkery.LeanCtx;

/// <summary>A bounded Engine context-planning request with no execution evidence.</summary>
public sealed class EnginePlanningRequest
{
    internal const int MaxQueryBytes = 16 * 1024;
    internal const long MaxBudgetTokens = 1_048_576;
    internal const int MaxCandidateBound = 256;
    internal const int MaxRequestBytes = 64 * 1024;

    public EnginePlanningRequest(
        string taskId,
        string query,
        long budgetTokens,
        int maxCandidates = 64)
    {
        TaskId = EnginePlanningWire.Text(taskId, "task_id", 256);
        Query = EnginePlanningWire.Text(query, "query", MaxQueryBytes, controls: false);
        if (budgetTokens < 1 || budgetTokens > MaxBudgetTokens)
            throw new ValidationError("budget_tokens is outside its protocol bounds");
        if (maxCandidates < 1 || maxCandidates > MaxCandidateBound)
            throw new ValidationError("max_candidates is outside its protocol bounds");
        BudgetTokens = budgetTokens;
        MaxCandidates = maxCandidates;
    }

    public string TaskId { get; }
    public string Query { get; }
    public long BudgetTokens { get; }
    public int MaxCandidates { get; }

    /// <summary>Return a detached canonical wire projection.</summary>
    public IReadOnlyDictionary<string, object?> ToDictionary()
    {
        var value = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["transport_version"] = 1L,
            ["engine_interface_version"] = Constants.ENGINE_INTERFACE_VERSION,
            ["task_id"] = TaskId,
            ["query"] = Query,
            ["budget_tokens"] = BudgetTokens,
            ["max_candidates"] = (long)MaxCandidates,
        };
        if (WireJson.CanonicalBytes(value).Length > MaxRequestBytes)
            throw new ValidationError("Engine context-plan request exceeds its byte bound");
        return new ReadOnlyDictionary<string, object?>(value);
    }

    public IReadOnlyDictionary<string, object?> ToDict() => ToDictionary();
}

internal static class EnginePlanningWire
{
    internal const int MaxRequestBytes = 64 * 1024;
    internal const int MaxPlanResponseBytes = 1024 * 1024;
    internal const int MaxMaterializedContentBytes = 1024 * 1024;
    internal const int MaxMaterializationResponseBytes = MaxPlanResponseBytes + MaxMaterializedContentBytes;
    internal const int MaxSources = 64;
    private const int MaxItems = 256;
    private const int MaxIdentifierBytes = 256;
    private const int MaxReferenceBytes = 1024;
    private const int MaxExtensionBytes = 64 * 1024;
    private const int MaxExtensionDepth = 8;

    private static readonly Regex TimestampPattern = new(
        "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly HashSet<string> ResponseKeys = SetOf(
        "schema_version", "tenant_id", "governance_revision", "plan");
    private static readonly HashSet<string> MaterializationResponseKeys = SetOf(
        "schema_version", "tenant_id", "governance_revision", "materialization");
    private static readonly HashSet<string> ResultKeys = SetOf(
        "schema_version", "transport_version", "engine_interface_version", "plan");
    private static readonly HashSet<string> MaterializationKeys = SetOf(
        "schema_version", "transport_version", "engine_interface_version", "plan",
        "materialized_digest", "materialized_token_count", "content");
    private static readonly HashSet<string> PlanKeys = SetOf(
        "schema_version", "context_plan_id", "task_id", "projection_digest", "budget_tokens",
        "selections", "provider_stats", "policy_decision_refs", "evidence");
    private static readonly HashSet<string> SelectionKeys = SetOf(
        "source_ref", "provider", "disposition", "token_count", "sha256_digest",
        "reason_codes", "reason_detail");
    private static readonly HashSet<string> ProviderStatKeys = SetOf(
        "candidates_offered", "candidates_selected", "tokens_used");
    private static readonly HashSet<string> EvidenceKeys = SetOf(
        "schema_version", "kind", "uri", "digest", "signature_status", "media_type");
    private static readonly HashSet<string> DescriptorKeys = SetOf(
        "object_ref", "source_id", "source_type", "content_digest", "revision", "owner",
        "observed_at", "valid_until", "classification", "permission");
    private static readonly HashSet<string> SourceTypes = SetOf(
        "filesystem", "issue_tracker", "relational_database", "other");
    private static readonly HashSet<string> Permissions = SetOf("permitted", "denied", "unknown");
    private static readonly HashSet<string> Classifications = SetOf(
        "Public", "Internal", "Confidential", "Restricted");
    private static readonly HashSet<string> Dispositions = SetOf("selected", "excluded", "deferred");
    private static readonly HashSet<string> ReasonCodes = SetOf(
        "relevant", "required", "cache_hit", "budget_exceeded", "lower_utility",
        "policy_excluded", "deferred_for_later", "other");
    private static readonly HashSet<string> EvidenceKinds = SetOf(
        "ProviderReceipt", "RuntimeLog", "SignedBatch", "QualityMeasurement", "ExperimentOutcome");
    private static readonly HashSet<string> SignatureStatuses = SetOf("Verified", "Unverified", "NotSigned");

    internal static string Text(
        string? value,
        string label,
        int maximumBytes,
        bool controls = true,
        bool rejectNul = true,
        bool nonblank = true,
        bool allowEmpty = false)
    {
        if (value is null)
            throw new ValidationError($"{label} must be a string");
        var encoded = WireJson.Utf8(value, label);
        if ((!allowEmpty && encoded.Length == 0) || encoded.Length > maximumBytes)
            throw new ValidationError($"{label} is outside its UTF-8 byte bound");
        if (rejectNul && value.Contains('\0'))
            throw new ValidationError($"{label} contains NUL");
        if (controls && value.Any(char.IsControl))
            throw new ValidationError($"{label} contains a control character");
        if (nonblank && string.IsNullOrWhiteSpace(value))
            throw new ValidationError($"{label} must not be blank");
        return value;
    }

    internal static IReadOnlyDictionary<string, object?> ParsePlanResponse(
        ReadOnlySpan<byte> raw,
        EnginePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        string configuredTenant)
    {
        var response = ParseObject(raw, "Enterprise Engine response", MaxPlanResponseBytes);
        Exact(response, ResponseKeys, "Enterprise Engine response");
        var schemaVersion = ProtocolU64(response["schema_version"], "schema_version");
        if (schemaVersion != 1)
            throw Protocol("Enterprise Engine response schema_version is unsupported");
        var tenantId = ProtocolUuid(response["tenant_id"], "tenant_id");
        if (tenantId != configuredTenant)
            throw Protocol("Enterprise Engine response tenant binding does not match");
        var governanceRevision = ProtocolU64(response["governance_revision"], "governance_revision");
        var plan = ParseSourcePlan(response["plan"], request, sourceIds);
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["tenant_id"] = tenantId,
            ["governance_revision"] = governanceRevision,
            ["plan"] = plan,
        };
    }

    internal static IReadOnlyDictionary<string, object?> ParseMaterializationResponse(
        ReadOnlySpan<byte> raw,
        EnginePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        string configuredTenant,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest)
    {
        var response = ParseObject(
            raw, "Enterprise Engine materialization response", MaxMaterializationResponseBytes);
        Exact(response, MaterializationResponseKeys, "Enterprise Engine materialization response");
        if (ProtocolU64(response["schema_version"], "schema_version") != 1)
            throw Protocol("Enterprise Engine materialization response schema_version is unsupported");
        var tenantId = ProtocolUuid(response["tenant_id"], "tenant_id");
        if (tenantId != configuredTenant)
            throw Protocol("Enterprise Engine materialization response tenant binding does not match");
        var governanceRevision = ProtocolU64(response["governance_revision"], "governance_revision");
        if (governanceRevision != expectedGovernanceRevision)
            throw Protocol("Enterprise Engine materialization governance revision does not match");

        var materializationRaw = Object(response["materialization"], "materialization");
        Exact(materializationRaw, MaterializationKeys, "Enterprise Engine materialization");
        Header(materializationRaw, "Enterprise Engine materialization");
        var plan = ParseSourcePlan(materializationRaw["plan"], request, sourceIds);
        if (!string.Equals((string)plan["binding_digest"]!, expectedBindingDigest, StringComparison.Ordinal))
            throw Protocol("Enterprise Engine materialization binding digest does not match");
        var materializedDigest = ProtocolDigest(materializationRaw["materialized_digest"], "materialized_digest");
        var tokenCount = ProtocolU64(materializationRaw["materialized_token_count"], "materialized_token_count");
        var result = Object(plan["result"], "materialization plan result");
        var resultPlan = Object(result["plan"], "materialization plan projection");
        if (tokenCount > ProtocolU64(resultPlan["budget_tokens"], "plan.budget_tokens"))
            throw Protocol("Enterprise Engine materialized token metric exceeds the plan budget");
        var content = ProtocolText(
            materializationRaw["content"], "materialized content", MaxMaterializedContentBytes,
            controls: false, rejectNul: false, nonblank: false, allowEmpty: true);
        var contentBytes = Utf8Protocol(content, "materialized content");
        if (WireJson.Sha256Digest(contentBytes) != materializedDigest)
            throw Protocol("Enterprise Engine materialized content digest does not match");

        var normalizedMaterialization = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["transport_version"] = 1L,
            ["engine_interface_version"] = Constants.ENGINE_INTERFACE_VERSION,
            ["plan"] = plan,
            ["materialized_digest"] = materializedDigest,
            ["materialized_token_count"] = tokenCount,
            ["content"] = content,
        };
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["tenant_id"] = tenantId,
            ["governance_revision"] = governanceRevision,
            ["materialization"] = normalizedMaterialization,
        };
    }

    internal static IReadOnlyList<string> SourceIds(IReadOnlyList<string>? values)
    {
        if (values is null || values.Count > MaxSources)
            throw new ValidationError("source_ids exceeds the Engine source bound");
        var result = new List<string>(values.Count);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var value in values)
        {
            var normalized = CanonicalUuid(value, "source_id", protocol: false);
            if (!seen.Add(normalized))
                throw new ValidationError("source_ids must not contain duplicates");
            result.Add(normalized);
        }
        return result.AsReadOnly();
    }

    internal static string ConfiguredTenant(string value)
    {
        try
        {
            return CanonicalUuid(value, "tenant_id", protocol: false);
        }
        catch (ValidationError error)
        {
            throw new ConfigurationError("tenant_id must be a non-nil UUID", error);
        }
    }

    internal static string ValidateBindingDigest(string value)
    {
        try
        {
            return WireJson.ValidateDigest(value, "expected_binding_digest");
        }
        catch (ValidationError error)
        {
            throw new ValidationError("expected_binding_digest is invalid", error);
        }
    }

    internal static string ValidateTimestamp(string value, string label) => Timestamp(value, label, protocol: false);

    private static Dictionary<string, object?> ParseObject(ReadOnlySpan<byte> raw, string label, int maximumBytes)
    {
        var response = WireJson.ParseObject(raw, label, maximumBytes);
        if (response is null)
            throw Protocol($"{label} must be an object");
        return response;
    }

    private static IReadOnlyDictionary<string, object?> ParseSourcePlan(
        object? value,
        EnginePlanningRequest request,
        IReadOnlyList<string> sourceIds)
    {
        var raw = Object(value, "source plan");
        Exact(raw, SetOf("result", "source_bindings", "binding_digest"), "Enterprise Engine source-plan response");
        var result = ParseResult(raw["result"], request);
        var plan = Object(result["plan"], "source plan projection");
        if (plan.GetValueOrDefault("projection_digest") is not string)
            throw Protocol("Enterprise Engine source-plan response requires projection_digest");

        var bindingsRaw = Array(raw["source_bindings"], "source_bindings", MaxItems);
        var bindings = new List<object?>(bindingsRaw.Count);
        string? previousObjectRef = null;
        foreach (var item in bindingsRaw)
        {
            var binding = ParseDescriptor(item, "source binding");
            var objectRef = (string)binding["object_ref"]!;
            if (previousObjectRef is not null &&
                WireJson.UnicodeCodePointComparer.Instance.Compare(previousObjectRef, objectRef) >= 0)
                throw Protocol("source_bindings must be strictly sorted by object_ref");
            previousObjectRef = objectRef;
            bindings.Add(binding);
        }

        var selections = Array(plan["selections"], "plan.selections", MaxItems)
            .Select(item => Object(item, "plan selection")).ToList();
        var selected = selections.Where(item => (string)item["disposition"]! == "selected").ToList();
        if (selected.Count != bindings.Count)
            throw Protocol("source_bindings do not match selected plan entries");
        foreach (var bindingValue in bindings)
        {
            var binding = Object(bindingValue, "source binding");
            var match = selected.Any(selection =>
                string.Equals(selection["source_ref"] as string, binding["object_ref"] as string, StringComparison.Ordinal) &&
                string.Equals(selection["provider"] as string, binding["source_id"] as string, StringComparison.Ordinal) &&
                string.Equals(selection.GetValueOrDefault("sha256_digest") as string,
                    binding["content_digest"] as string, StringComparison.Ordinal));
            if (!match)
                throw Protocol("source binding does not match a selected plan entry");
        }

        var suppliedDigest = ProtocolDigest(raw["binding_digest"], "binding_digest");
        var expectedDigest = WireJson.Sha256Digest(WireJson.CanonicalBytes(new object?[] { result, bindings }));
        if (suppliedDigest != expectedDigest)
            throw Protocol("binding_digest does not match canonical source bindings");
        ValidateSourceScope(plan, bindings, sourceIds);
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["result"] = result,
            ["source_bindings"] = bindings,
            ["binding_digest"] = suppliedDigest,
        };
    }

    private static Dictionary<string, object?> ParseResult(object? value, EnginePlanningRequest request)
    {
        var raw = Object(value, "source-plan result");
        Exact(raw, ResultKeys, "Engine source-plan result");
        Header(raw, "Engine source-plan result");
        var plan = ParsePlan(raw["plan"]);
        if (!string.Equals((string)plan["task_id"]!, request.TaskId, StringComparison.Ordinal))
            throw Protocol("Engine source-plan response task_id does not bind the request");
        if (ProtocolU64(plan["budget_tokens"], "plan.budget_tokens") > (ulong)request.BudgetTokens)
            throw Protocol("Engine source-plan response budget exceeds the request");
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["transport_version"] = 1L,
            ["engine_interface_version"] = Constants.ENGINE_INTERFACE_VERSION,
            ["plan"] = plan,
        };
    }

    private static Dictionary<string, object?> ParsePlan(object? value)
    {
        var raw = Object(value, "plan");
        foreach (var key in new[] { "schema_version", "context_plan_id", "task_id", "budget_tokens", "selections" })
            if (!raw.ContainsKey(key))
                throw Protocol("plan is missing a required field");
        var extensions = Extensions(raw, PlanKeys);
        if (ProtocolU64(raw["schema_version"], "plan.schema_version") != 1)
            throw Protocol("plan.schema_version is unsupported");
        var selectionRaw = Array(raw["selections"], "plan.selections", MaxItems);
        var selections = new List<object?>(selectionRaw.Count);
        var references = new HashSet<string>(StringComparer.Ordinal);
        ulong selectedTokens = 0;
        var budget = ProtocolU64(raw["budget_tokens"], "plan.budget_tokens");
        foreach (var (item, index) in selectionRaw.Select((item, index) => (item, index)))
        {
            var selection = ParseSelection(item, $"plan.selections[{index}]");
            var sourceRef = (string)selection["source_ref"]!;
            if (!references.Add(sourceRef))
                throw Protocol("plan.selections contains duplicate source_ref values");
            if ((string)selection["disposition"]! == "selected")
            {
                var tokens = ProtocolU64(selection["token_count"], "selection.token_count");
                if (tokens > budget - selectedTokens)
                    throw Protocol("plan selected context exceeds budget_tokens");
                selectedTokens += tokens;
            }
            selections.Add(selection);
        }

        var result = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["context_plan_id"] = ProtocolText(raw["context_plan_id"], "plan.context_plan_id", MaxIdentifierBytes),
            ["task_id"] = ProtocolText(raw["task_id"], "plan.task_id", MaxIdentifierBytes),
            ["budget_tokens"] = budget,
            ["selections"] = selections,
        };
        if (raw.TryGetValue("projection_digest", out var projectionValue) && projectionValue is not null)
            result["projection_digest"] = ProtocolDigest(projectionValue, "plan.projection_digest");
        if (raw.ContainsKey("provider_stats"))
        {
            var statsRaw = Object(raw["provider_stats"], "plan.provider_stats");
            if (statsRaw.Count > MaxItems)
                throw Protocol("plan.provider_stats exceeds its item bound");
            var stats = new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (var (provider, valueRaw) in statsRaw)
            {
                var providerName = ProtocolText(provider, "plan.provider_stats key", MaxIdentifierBytes);
                var entry = Object(valueRaw, "plan.provider_stats entry");
                Exact(entry, ProviderStatKeys, "plan.provider_stats entry");
                var offered = ProtocolU64(entry["candidates_offered"], "plan.provider_stats.candidates_offered");
                var chosen = ProtocolU64(entry["candidates_selected"], "plan.provider_stats.candidates_selected");
                if (chosen > offered)
                    throw Protocol("plan.provider_stats selected exceeds offered");
                stats.Add(providerName, new Dictionary<string, object?>(StringComparer.Ordinal)
                {
                    ["candidates_offered"] = offered,
                    ["candidates_selected"] = chosen,
                    ["tokens_used"] = ProtocolU64(entry["tokens_used"], "plan.provider_stats.tokens_used"),
                });
            }
            if (stats.Count > 0)
                result["provider_stats"] = stats;
        }
        if (raw.ContainsKey("policy_decision_refs"))
        {
            var rawRefs = Array(raw["policy_decision_refs"], "plan.policy_decision_refs", MaxItems);
            var normalizedRefs = new List<object?>(rawRefs.Count);
            var seenRefs = new HashSet<string>(StringComparer.Ordinal);
            foreach (var item in rawRefs)
            {
                var reference = ProtocolText(item, "plan.policy_decision_refs", MaxIdentifierBytes);
                if (!seenRefs.Add(reference))
                    throw Protocol("plan.policy_decision_refs contains duplicates");
                normalizedRefs.Add(reference);
            }
            if (normalizedRefs.Count > 0)
                result["policy_decision_refs"] = normalizedRefs;
        }
        if (raw.ContainsKey("evidence"))
        {
            var evidenceRaw = Array(raw["evidence"], "plan.evidence", MaxItems);
            var evidence = evidenceRaw.Select((item, index) => (object?)ParseEvidence(item, $"plan.evidence[{index}]")).ToList();
            if (evidence.Count > 0)
                result["evidence"] = evidence;
        }
        foreach (var (key, extension) in extensions)
            result.Add(key, extension);

        if (result.TryGetValue("projection_digest", out var digestValue))
        {
            var unsigned = new Dictionary<string, object?>(result, StringComparer.Ordinal);
            unsigned.Remove("projection_digest");
            var expected = WireJson.Sha256Digest(WireJson.CanonicalBytes(unsigned));
            if (!string.Equals((string)digestValue!, expected, StringComparison.Ordinal))
                throw Protocol("plan.projection_digest does not match canonical projection content");
        }
        return result;
    }

    private static Dictionary<string, object?> ParseSelection(object? value, string label)
    {
        var raw = Object(value, label);
        var expected = new HashSet<string>(SelectionKeys, StringComparer.Ordinal);
        if (raw.Keys.Any(key => !expected.Contains(key)))
            throw Protocol($"{label} fields do not match the v1 contract");
        foreach (var key in new[] { "source_ref", "provider", "disposition", "token_count", "reason_codes" })
            if (!raw.ContainsKey(key))
                throw Protocol($"{label} is missing a required field");
        var reasonRaw = Array(raw["reason_codes"], $"{label}.reason_codes", MaxItems);
        if (reasonRaw.Count == 0)
            throw Protocol($"{label}.reason_codes has an invalid shape");
        var reasons = new List<object?>(reasonRaw.Count);
        var seenReasons = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in reasonRaw)
        {
            var reason = EnumValue(item, $"{label}.reason_codes", ReasonCodes);
            if (!seenReasons.Add(reason))
                throw Protocol($"{label}.reason_codes contains duplicates");
            reasons.Add(reason);
        }
        var result = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["source_ref"] = ProtocolText(raw["source_ref"], $"{label}.source_ref", MaxIdentifierBytes),
            ["provider"] = ProtocolText(raw["provider"], $"{label}.provider", MaxIdentifierBytes),
            ["disposition"] = EnumValue(raw["disposition"], $"{label}.disposition", Dispositions),
            ["token_count"] = ProtocolU64(raw["token_count"], $"{label}.token_count"),
            ["reason_codes"] = reasons,
        };
        if (raw.TryGetValue("sha256_digest", out var digest) && digest is not null)
            result["sha256_digest"] = ProjectionDigest(digest, $"{label}.sha256_digest");
        if (raw.TryGetValue("reason_detail", out var detail) && detail is not null)
            result["reason_detail"] = ProtocolText(detail, $"{label}.reason_detail", MaxIdentifierBytes);
        return result;
    }

    private static Dictionary<string, object?> ParseEvidence(object? value, string label)
    {
        var raw = Object(value, label);
        var extensions = Extensions(raw, EvidenceKeys);
        foreach (var key in new[] { "kind", "uri", "digest", "signature_status" })
            if (!raw.ContainsKey(key))
                throw Protocol($"{label} is missing a required field");
        var result = new Dictionary<string, object?>(StringComparer.Ordinal);
        if (raw.TryGetValue("schema_version", out var schema) && schema is not null)
        {
            if (ProtocolU64(schema, $"{label}.schema_version") != 1)
                throw Protocol($"{label}.schema_version is unsupported");
            result["schema_version"] = 1L;
        }
        result["kind"] = EnumValue(raw["kind"], $"{label}.kind", EvidenceKinds);
        result["uri"] = ProtocolText(raw["uri"], $"{label}.uri", MaxIdentifierBytes);
        var digest = ProtocolText(raw["digest"], $"{label}.digest", MaxIdentifierBytes);
        if (result.ContainsKey("schema_version"))
        {
            var candidate = digest.StartsWith("sha256:", StringComparison.Ordinal) ? digest[7..] : digest;
            if (candidate.StartsWith("blake3:", StringComparison.Ordinal))
                candidate = candidate[7..];
            if (!IsHexDigest(candidate))
                throw Protocol($"{label}.digest is not a supported versioned digest");
        }
        result["digest"] = digest;
        result["signature_status"] = EnumValue(raw["signature_status"], $"{label}.signature_status", SignatureStatuses);
        if (raw.TryGetValue("media_type", out var mediaType) && mediaType is not null)
            result["media_type"] = ProtocolText(mediaType, $"{label}.media_type", MaxIdentifierBytes);
        foreach (var (key, extension) in extensions)
            result.Add(key, extension);
        return result;
    }

    private static Dictionary<string, object?> ParseDescriptor(object? value, string label)
    {
        var raw = Object(value, label);
        if (raw.Keys.Any(key => !DescriptorKeys.Contains(key)))
            throw Protocol($"{label} fields do not match the v1 contract");
        foreach (var key in new[] { "object_ref", "source_id", "source_type", "content_digest" })
            if (!raw.ContainsKey(key))
                throw Protocol($"{label} is missing a required field");
        var objectRef = ProtocolText(raw["object_ref"], $"{label}.object_ref", MaxReferenceBytes);
        var sourceId = ProtocolText(raw["source_id"], $"{label}.source_id", MaxIdentifierBytes);
        var sourceType = EnumValue(raw["source_type"], $"{label}.source_type", SourceTypes);
        var contentDigest = ProtocolDigest(raw["content_digest"], $"{label}.content_digest");
        var revision = OptionalReference(raw.GetValueOrDefault("revision"), $"{label}.revision");
        var owner = OptionalReference(raw.GetValueOrDefault("owner"), $"{label}.owner");
        var observedAt = OptionalTimestamp(raw.GetValueOrDefault("observed_at"), $"{label}.observed_at");
        var validUntil = OptionalTimestamp(raw.GetValueOrDefault("valid_until"), $"{label}.valid_until");
        if (observedAt is not null && validUntil is not null &&
            string.CompareOrdinal(validUntil, observedAt) <= 0)
            throw Protocol($"{label} validity window is inverted");
        var permissionValue = raw.TryGetValue("permission", out var rawPermission) ? rawPermission : "unknown";
        var permission = EnumValue(permissionValue, $"{label}.permission", Permissions);
        string? classification = null;
        if (raw.TryGetValue("classification", out var rawClassification) && rawClassification is not null)
            classification = EnumValue(rawClassification, $"{label}.classification", Classifications);
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["object_ref"] = objectRef,
            ["source_id"] = sourceId,
            ["source_type"] = sourceType,
            ["content_digest"] = contentDigest,
            ["revision"] = revision,
            ["owner"] = owner,
            ["observed_at"] = observedAt,
            ["valid_until"] = validUntil,
            ["classification"] = classification,
            ["permission"] = permission,
        };
    }

    private static Dictionary<string, object?> Extensions(
        IReadOnlyDictionary<string, object?> value,
        IReadOnlySet<string> reserved)
    {
        var result = new Dictionary<string, object?>(StringComparer.Ordinal);
        foreach (var (key, nested) in value)
        {
            if (reserved.Contains(key))
                continue;
            _ = ProtocolText(key, "extension key", MaxIdentifierBytes);
            ValidateExtension(nested, 0);
            result.Add(key, nested);
        }
        if (result.Count > MaxItems)
            throw Protocol("extensions exceed their field bound");
        return result;
    }

    private static void ValidateExtension(object? value, int depth)
    {
        if (depth > MaxExtensionDepth)
            throw Protocol("extension value exceeds its nesting bound");
        if (value is IReadOnlyDictionary<string, object?> map)
        {
            if (map.Count > MaxItems)
                throw Protocol("extension object exceeds its item bound");
            foreach (var (key, nested) in map)
            {
                _ = ProtocolText(key, "extension object key", MaxIdentifierBytes);
                ValidateExtension(nested, depth + 1);
            }
        }
        else if (value is IEnumerable<object?> items && value is not string)
        {
            var array = items.ToList();
            if (array.Count > MaxItems)
                throw Protocol("extension array exceeds its item bound");
            foreach (var nested in array)
                ValidateExtension(nested, depth + 1);
        }
        else if (value is string text && WireJson.Utf8(text, "extension string").Length > MaxExtensionBytes)
        {
            throw Protocol("extension string exceeds its byte bound");
        }
        try
        {
            if (WireJson.CanonicalBytes(value).Length > MaxExtensionBytes)
                throw Protocol("extension value exceeds its serialized byte bound");
        }
        catch (ValidationError error)
        {
            throw Protocol("extension value is not canonical JSON", error);
        }
    }

    private static void ValidateSourceScope(
        IReadOnlyDictionary<string, object?> plan,
        IReadOnlyList<object?> bindings,
        IReadOnlyList<string> sourceIds)
    {
        var requested = new HashSet<string>(sourceIds, StringComparer.Ordinal);
        var selections = Array(plan["selections"], "plan.selections", MaxItems);
        foreach (var selectionValue in selections)
        {
            var selection = Object(selectionValue, "plan selection");
            if (!requested.Contains((string)selection["source_ref"]!) ||
                !requested.Contains((string)selection["provider"]!))
                throw Protocol("Enterprise Engine selection is outside requested sources");
        }
        foreach (var bindingValue in bindings)
        {
            var binding = Object(bindingValue, "source binding");
            if (!requested.Contains((string)binding["object_ref"]!) ||
                !requested.Contains((string)binding["source_id"]!))
                throw Protocol("Enterprise Engine source binding is outside requested sources");
            if (!string.Equals((string)binding["permission"]!, "permitted", StringComparison.Ordinal))
                throw Protocol("Enterprise Engine selected source is not permitted");
        }
    }

    private static void Header(IReadOnlyDictionary<string, object?> value, string label)
    {
        if (ProtocolU64(value["schema_version"], $"{label}.schema_version") != 1 ||
            ProtocolU64(value["transport_version"], $"{label}.transport_version") != 1)
            throw Protocol($"{label} schema or transport version is unsupported");
        if (!string.Equals(value["engine_interface_version"] as string,
                Constants.ENGINE_INTERFACE_VERSION, StringComparison.Ordinal))
            throw Protocol($"{label} engine_interface_version is unsupported");
    }

    private static IReadOnlyList<object?> Array(object? value, string label, int maximum)
    {
        if (value is not List<object?> items || items.Count > maximum)
            throw Protocol($"{label} has an invalid shape or exceeds its item bound");
        return items;
    }

    private static IReadOnlyDictionary<string, object?> Object(object? value, string label)
    {
        if (value is not IReadOnlyDictionary<string, object?> result)
            throw Protocol($"{label} must be an object");
        return result;
    }

    private static void Exact(
        IReadOnlyDictionary<string, object?> value,
        IReadOnlySet<string> expected,
        string label) => WireJson.RequireExactKeys(value, expected, label);

    private static ulong ProtocolU64(object? value, string label)
    {
        if (value is ulong unsigned)
            return unsigned;
        if (value is long signed && signed >= 0)
            return (ulong)signed;
        throw Protocol($"{label} must be an unsigned 64-bit integer");
    }

    private static string ProtocolUuid(object? value, string label)
    {
        if (value is not string text)
            throw Protocol($"{label} must be a canonical UUID");
        return CanonicalUuid(text, label, protocol: true);
    }

    private static string CanonicalUuid(string value, string label, bool protocol)
    {
        var valid = !string.IsNullOrEmpty(value) && value == value.Trim() &&
            Guid.TryParseExact(value, "D", out var parsed) && parsed != Guid.Empty;
        if (!valid)
        {
            if (protocol)
                throw Protocol($"{label} must be a non-nil canonical UUID");
            throw new ValidationError($"{label} must be a non-nil canonical UUID");
        }
        return Guid.ParseExact(value, "D").ToString("D");
    }

    private static string ProtocolDigest(object? value, string label)
    {
        if (value is not string text)
            throw Protocol($"{label} must be a digest string");
        try
        {
            return WireJson.ValidateDigest(text, label);
        }
        catch (ValidationError error)
        {
            throw Protocol($"{label} is invalid", error);
        }
    }

    private static string ProjectionDigest(object? value, string label)
    {
        if (value is not string digest)
            throw Protocol($"{label} must be a string");
        var candidate = digest.StartsWith("sha256:", StringComparison.Ordinal) ? digest[7..] : digest;
        if (!IsHexDigest(candidate))
            throw Protocol($"{label} must contain a 64-digit hexadecimal digest");
        return digest;
    }

    private static bool IsHexDigest(string value) => value.Length == 64 &&
        value.All(character => character is >= '0' and <= '9' or >= 'a' and <= 'f' or >= 'A' and <= 'F');

    private static string EnumValue(object? value, string label, IReadOnlySet<string> allowed)
    {
        if (value is not string text || !allowed.Contains(text))
            throw Protocol($"{label} has an unsupported value");
        return text;
    }

    private static string? OptionalReference(object? value, string label) =>
        value is null ? null : ProtocolText(value, label, MaxReferenceBytes);

    private static string? OptionalTimestamp(object? value, string label) =>
        value is null ? null : Timestamp(value, label, protocol: true);

    private static string Timestamp(object? value, string label, bool protocol)
    {
        if (value is not string candidate)
        {
            if (protocol)
                throw Protocol($"{label} must be a timestamp string");
            throw new ValidationError($"{label} must be a timestamp string");
        }
        var text = protocol
            ? ProtocolText(candidate, label, MaxIdentifierBytes)
            : Text(candidate, label, MaxIdentifierBytes);
        if (!TimestampPattern.IsMatch(text) ||
            !DateTime.TryParseExact(text, "yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out _))
        {
            if (protocol)
                throw Protocol($"{label} must use canonical UTC timestamp syntax");
            throw new ValidationError($"{label} must use canonical UTC timestamp syntax");
        }
        return text;
    }

    private static string ProtocolText(
        object? value,
        string label,
        int maximumBytes,
        bool controls = true,
        bool rejectNul = true,
        bool nonblank = true,
        bool allowEmpty = false)
    {
        if (value is not string text)
            throw Protocol($"{label} must be a string");
        try
        {
            return Text(text, label, maximumBytes, controls, rejectNul, nonblank, allowEmpty);
        }
        catch (ValidationError error)
        {
            throw Protocol($"{label} violates its protocol bound", error);
        }
    }

    private static byte[] Utf8Protocol(string value, string label)
    {
        try
        {
            return WireJson.Utf8(value, label);
        }
        catch (ValidationError error)
        {
            throw Protocol($"{label} is not valid UTF-8", error);
        }
    }

    private static EngineProtocolError Protocol(string message, Exception? cause = null) =>
        new(message, cause);

    private static HashSet<string> SetOf(params string[] values) => new(values, StringComparer.Ordinal);
}
