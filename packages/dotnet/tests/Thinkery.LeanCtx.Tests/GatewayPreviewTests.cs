// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
using System.Text.Json;
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
            catch (EngineProtocolError)
            {
                continue;
            }
            throw new Exception(fixture + ": accepted an invalid admission");
        }
        LiveEngine();
    }

    private static void LiveEngine()
    {
        var binary = Environment.GetEnvironmentVariable("LEANCTX_ENGINE_BINARY");
        if (string.IsNullOrEmpty(binary))
        {
            Console.WriteLine("SKIP gateway-preview live Engine (LEANCTX_ENGINE_BINARY)");
            return;
        }
        var engine = new SubprocessEngineClient(binary);
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
        Check(!restricted.Body!.Value.GetRawText().Contains("customer list", StringComparison.Ordinal), "restricted content left");
        Check(restricted.Receipt.Decisions[0].ReasonCodes.Contains("destination.remote_restricted"), "restricted: reason");

        var marked = Admit("Classification: Confidential\nboard minutes", "openai", "https://api.openai.com");
        Check(marked.Disposition == "forward" && marked.Classification == "confidential", "marking: classified");
        Check(marked.Receipt!.Destination.Locality == "remote", "marking: destination");
    }
}
