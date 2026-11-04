// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
using System.Text.Json;
using System.Security.Cryptography;
using System.Text;
using Thinkery.LeanCtx;
using Thinkery.LeanCtx.Preview;

/// <summary>Gateway preview conformance (fixtures shared by all six SDKs) and live Engine.</summary>
internal static class GatewayPreviewTests
{
    private static readonly string Fixtures = Path.Combine(AppContext.BaseDirectory, "fixtures", "gateway-preview-v1");

    private static byte[] Load(string kind, string name) =>
        File.ReadAllBytes(Path.Combine(Fixtures, kind, name + ".json"));

    private static void Check(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }

    public static void Run()
    {
        using var manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(Fixtures, "manifest.json")));
        foreach (var entry in manifest.RootElement.GetProperty("valid").EnumerateObject())
        {
            var expected = entry.Value;
            var admission = GatewayPreview.ParseEgressAdmission(Load("valid", entry.Name));
            var receipt = admission.Receipt ?? throw new Exception(entry.Name + ": receipt missing");
            var classification = expected.GetProperty("classification");
            Check(admission.Disposition == expected.GetProperty("disposition").GetString(), entry.Name + ": disposition");
            Check(admission.Classification == (classification.ValueKind == JsonValueKind.Null ? null : classification.GetString()),
                entry.Name + ": classification");
            Check(receipt.Outcome == expected.GetProperty("outcome").GetString(), entry.Name + ": outcome");
            Check(receipt.Decisions.Count == expected.GetProperty("decisions").GetInt32(), entry.Name + ": decisions");
            Check(receipt.Decisions.Count(d => d.Disposition == "deny") == expected.GetProperty("denied").GetInt32(),
                entry.Name + ": denied");
            Check(receipt.Security["redactions"] == expected.GetProperty("redactions").GetInt64(), entry.Name + ": redactions");
            Check(receipt.Signals.Count == expected.GetProperty("signals").GetInt32(), entry.Name + ": signals");
            Check(admission.MaySend && !receipt.Principal.IsKnown, entry.Name + ": may send, unknown principal");
        }
        foreach (var name in manifest.RootElement.GetProperty("invalid").EnumerateArray())
        {
            var fixture = name.GetString() ?? throw new Exception("invalid fixture name");
            try
            {
                GatewayPreview.ParseEgressAdmission(Load("invalid", fixture));
            }
            catch (InstanceProtocolError)
            {
                continue;
            }
            throw new Exception(fixture + ": accepted an invalid admission");
        }
        KeyedReceiptObjects(manifest.RootElement.GetProperty("keyed_v2"));
        LifecycleContracts();
        LiveEngine();
    }

    private static void KeyedReceiptObjects(JsonElement vector)
    {
        var key = Convert.FromHexString(vector.GetProperty("key_hex").GetString()!);
        var plain = "sha256:" + Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(vector.GetProperty("object_text").GetString()!))).ToLowerInvariant();
        var keyed = vector.GetProperty("keyed_object").GetString()!;
        var keyId = vector.GetProperty("key_id").GetString()!;
        Check(GatewayPreview.ReceiptKeyId(key) == keyId, "receipt key id");
        Check(GatewayPreview.KeyedObjectDigest(key, plain) == keyed, "keyed object digest");
        Check(GatewayPreview.KeyedObjectDigest(new byte[32], plain) != keyed, "another key, another digest");
        var receipt = GatewayPreview.ParseEgressAdmission(Load("valid", vector.GetProperty("fixture").GetString()!)).Receipt
            ?? throw new Exception("v2 receipt missing");
        Check(receipt.SchemaVersion == 2 && receipt.ObjectDigest is { Algorithm: "hmac-sha256" } digest && digest.KeyId == keyId,
            "v2 receipt names its key");
        Check(receipt.Decisions.Select(d => d.Object).SequenceEqual(new[] { keyed }), "v2 receipt names the keyed object");
        Check(receipt.Decisions.All(d => d.Object != plain), "a plain-SHA-256 guess confirms nothing");
        var v1 = GatewayPreview.ParseEgressAdmission(Load("valid", "masked")).Receipt!;
        Check(v1.SchemaVersion == 1 && v1.ObjectDigest is null, "v1 receipt stays v1");
    }

    private static void LifecycleContracts()
    {
        const string prepared = "{ \"model\" : \"m\" }";
        var finalContext = "sha256:" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(prepared))).ToLowerInvariant();
        var decision = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1, ["disposition"] = "forward", ["body"] = new Dictionary<string, object?> { ["model"] = "m" },
            ["classification"] = "internal",
            ["receipt"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["schema_version"] = 1, ["receipt_id"] = "egress-test", ["mode"] = "governed",
                ["principal"] = new Dictionary<string, object?> { ["kind"] = "unknown" },
                ["destination"] = new Dictionary<string, object?> { ["provider"] = "openai", ["locality"] = "remote" },
                ["sources"] = new Dictionary<string, object?> { ["inspected"] = 0, ["permitted"] = 0, ["selected"] = 0, ["blocked"] = 0 },
                ["security"] = new Dictionary<string, object?> { ["redactions"] = 0, ["blocked_objects"] = 0, ["quarantined_objects"] = 0, ["injection_signals"] = 0, ["incomplete_coverage"] = 0 },
                ["tokens"] = new Dictionary<string, object?> { ["original"] = 0, ["delivered"] = 0 },
                ["outcome"] = "delivered", ["duration_us"] = 0, ["final_context"] = finalContext,
            },
        };
        var preparation = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 2, ["decision"] = decision, ["prepared_body"] = prepared,
            ["admission_receipt_digest"] = "sha256:" + new string('b', 64),
        };
        var parsed = GatewayPreview.ParseEgressPreparation(JsonSerializer.SerializeToUtf8Bytes(preparation));
        Check(parsed.PreparedBody == prepared && parsed.MaySend, "prepared bytes were not preserved");
        Check(!GatewayPreview.ParseEgressPreparation(JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, object?>
        {
            ["schema_version"] = 2, ["decision"] = new Dictionary<string, object?> { ["schema_version"] = 1, ["disposition"] = "refused", ["refusal"] = "withheld" },
        })).MaySend, "refusal became dispatchable");
        void AssertRejected(Dictionary<string, object?> candidate, string message)
        {
            try { GatewayPreview.ParseEgressPreparation(JsonSerializer.SerializeToUtf8Bytes(candidate)); }
            catch (InstanceProtocolError) { return; }
            throw new Exception(message);
        }
        Check(GatewayPreview.ParseEgressPreparation(JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, object?>
        {
            ["schema_version"] = 2,
            ["decision"] = new Dictionary<string, object?> { ["schema_version"] = 1, ["disposition"] = "forward", ["body"] = new Dictionary<string, object?> { ["model"] = "m" } },
            ["prepared_body"] = prepared,
        })).MaySend, "receipt-less best-effort forward was rejected");
        var rewrittenDecision = new Dictionary<string, object?>(decision) { ["disposition"] = "rewritten" };
        Check(GatewayPreview.ParseEgressPreparation(JsonSerializer.SerializeToUtf8Bytes(
            new Dictionary<string, object?>(preparation) { ["decision"] = rewrittenDecision })).MaySend,
            "delivered rewritten preparation was rejected");
        var baseReceipt = (Dictionary<string, object?>)decision["receipt"]!;
        foreach (var disposition in new[] { "forward", "rewritten" })
        foreach (var outcome in new[] { "withheld", "failed" })
        {
            var badReceipt = new Dictionary<string, object?>(baseReceipt)
            {
                ["outcome"] = outcome,
            };
            badReceipt.Remove("final_context");
            var badDecision = new Dictionary<string, object?>(decision)
            {
                ["disposition"] = disposition,
                ["receipt"] = badReceipt,
            };
            AssertRejected(new Dictionary<string, object?>(preparation) { ["decision"] = badDecision },
                $"{disposition} with {outcome} receipt was accepted");
        }
        AssertRejected(new Dictionary<string, object?>
        {
            ["schema_version"] = 2,
            ["decision"] = new Dictionary<string, object?>
            {
                ["schema_version"] = 1, ["disposition"] = "refused", ["refusal"] = "withheld",
                ["receipt"] = baseReceipt,
            },
        }, "refused decision with delivered receipt was accepted");

        var request = new EgressHostReportRequest("sha256:" + new string('b', 64), 2, "http", "no_response_observed", null, "timeout");
        var observation = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1, ["admission_receipt_digest"] = request.AdmissionReceiptDigest,
            ["attempt"] = 2, ["transport"] = "http", ["observer"] = "host_reported",
            ["result"] = "no_response_observed", ["failure"] = "timeout",
        };
        var canonicalObservation = new SortedDictionary<string, object?>(observation, StringComparer.Ordinal);
        var observationDigest = "sha256:" + Convert.ToHexString(SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(canonicalObservation))).ToLowerInvariant();
        byte[] ReportBytes() => JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1, ["observation_digest"] = observationDigest, ["observation"] = observation,
        });
        var report = GatewayPreview.ParseEgressHostReport(ReportBytes(), request);
        Check(report.Observation.GetProperty("observer").GetString() == "host_reported" &&
            report.Observation.GetProperty("result").GetString() == "no_response_observed", "host observation changed");
        observation["observer"] = "engine_observed";
        try { GatewayPreview.ParseEgressHostReport(ReportBytes(), request); }
        catch (InstanceProtocolError) { return; }
        throw new Exception("an EngineObserved claim was accepted from a host report");
    }

    private static void LiveEngine()
    {
        var binary = Program.TestEnvironment("LEANCTX_TEST_INSTANCE_BINARY", "LEANCTX_ENGINE_BINARY");
        if (string.IsNullOrEmpty(binary))
        {
            Console.WriteLine("SKIP gateway-preview live Engine (LEANCTX_TEST_INSTANCE_BINARY)");
            return;
        }
        var engine = new SubprocessInstanceClient(binary);
        var root = Directory.CreateTempSubdirectory("leanctx-gateway-dotnet").FullName;
        EgressAdmission Admit(string text, string provider, string upstream) =>
            GatewayPreview.AdmitEgressAsync(engine, root, new EgressRequest(provider, upstream,
                new Dictionary<string, object?>
                {
                    ["model"] = "m",
                    ["temperature"] = 0.2,
                    ["messages"] = new object[] { new Dictionary<string, object?> { ["role"] = "user", ["content"] = text } },
                })).GetAwaiter().GetResult();

        var credential = "AKIA" + "Q3EGRZ7LIVEX4KEY";
        var masked = Admit("deploy fails with " + credential, "openai", "https://api.openai.com");
        Check(masked.Disposition == "rewritten", "credential: disposition");
        Check(!masked.Body!.Value.GetRawText().Contains(credential, StringComparison.Ordinal), "credential left");
        Check(masked.Receipt!.Security["redactions"] == 1, "credential: redactions");

        var restricted = Admit("Classification: Secret\nroot cause and customer list", "anthropic", "https://api.anthropic.com");
        Check(restricted.Classification == "restricted", "restricted: classification");
        Check(restricted.Receipt!.Outcome == "withheld", "restricted: outcome");
        Check(restricted.Disposition == "refused", "restricted: refused");
        Check(!restricted.MaySend && restricted.Body is null, "restricted request must not be sent");
        Check(restricted.Receipt.Decisions[0].ReasonCodes.Contains("destination.remote_restricted"), "restricted: reason");

        var marked = Admit("Classification: Confidential\nboard minutes", "openai", "https://api.openai.com");
        Check(marked.Disposition == "forward" && marked.Classification == "confidential", "marking: classified");
        Check(marked.Receipt!.Destination.Locality == "remote", "marking: destination");

        var model = "host-selected-model-2026";
        var credentialValue = "AKIA" + "Q3EGRZ7LIVEX4KEY";
        var preparation = GatewayPreview.PrepareEgressAsync(engine, root, new EgressRequest("openai", "https://api.openai.com",
            new Dictionary<string, object?>
            {
                ["model"] = model,
                ["messages"] = new object[] { new Dictionary<string, object?>
                    { ["role"] = "user", ["content"] = "deploy fails with " + credentialValue } },
            })).GetAwaiter().GetResult();
        Check(preparation.MaySend && preparation.PreparedBody is not null &&
            preparation.AdmissionReceiptDigest is not null && preparation.Decision.Receipt is not null,
            "preparation: dispatchable body and receipt");
        var preparedBody = preparation.PreparedBody!;
        using var prepared = JsonDocument.Parse(preparedBody);
        Check(prepared.RootElement.GetProperty("model").GetString() == model, "preparation changed the caller model");
        Check(!preparedBody.Contains(credentialValue, StringComparison.Ordinal), "preparation left the credential");
        Check(preparation.Decision.Receipt!.Security["redactions"] == 1, "preparation: redactions");
        Check(preparation.Decision.Receipt.Destination.Provider == "openai", "preparation changed the provider");
        Check(preparation.Decision.Receipt.Destination.Model == model, "receipt does not bind the host-selected model");
        var bodyDigest = "sha256:" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(preparedBody))).ToLowerInvariant();
        Check(preparation.Decision.Receipt.FinalContext == bodyDigest, "receipt does not bind exact prepared bytes");

        var report = new EgressHostReportRequest(preparation.AdmissionReceiptDigest!, 1,
            "http", "no_response_observed", null, "timeout");
        var first = GatewayPreview.ReportEgressAsync(engine, root, report).GetAwaiter().GetResult();
        var replay = GatewayPreview.ReportEgressAsync(engine, root, report).GetAwaiter().GetResult();
        Check(first.Observation.GetProperty("observer").GetString() == "host_reported", "report lost HostReported provenance");
        Check(first.Observation.GetProperty("result").GetString() == "no_response_observed" &&
            first.Observation.GetProperty("failure").GetString() == "timeout", "timeout report changed");
        Check(first.ObservationDigest == replay.ObservationDigest, "identical report replay changed its digest");

        var conflict = new EgressHostReportRequest(preparation.AdmissionReceiptDigest!, 1,
            "http", "response_observed", 503, null);
        var conflictRejected = false;
        try { GatewayPreview.ReportEgressAsync(engine, root, conflict).GetAwaiter().GetResult(); }
        catch (InstanceExecutionError error) when (error.Message.Contains("egress_observation_unavailable", StringComparison.Ordinal))
        { conflictRejected = true; }
        Check(conflictRejected, "a contradictory report for the same attempt was accepted");
    }
}
