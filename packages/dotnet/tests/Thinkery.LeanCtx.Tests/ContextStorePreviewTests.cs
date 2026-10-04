// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
using System.Text.Json;
using Thinkery.LeanCtx;
using Thinkery.LeanCtx.Preview;

/// <summary>Context Store preview conformance (fixtures shared by all six SDKs) and live Engine.</summary>
internal static class ContextStorePreviewTests
{
    private static readonly string Fixtures = Path.Combine(AppContext.BaseDirectory, "fixtures", "context-store-preview-v1");

    private static byte[] Load(string kind, string validity, string name) =>
        File.ReadAllBytes(Path.Combine(Fixtures, kind, validity, name + ".json"));

    private static void Check(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }

    public static void Run()
    {
        using var manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(Fixtures, "manifest.json")));
        var parsers = new Dictionary<string, Action<byte[]>>
        {
            ["evidence"] = bytes => ContextStorePreview.ParsePolicyEvidence(bytes),
            ["lineage"] = bytes => ContextStorePreview.ParseTaskLineage(bytes),
        };
        foreach (var (kind, parse) in parsers)
        {
            var documents = manifest.RootElement.GetProperty("documents").GetProperty(kind);
            foreach (var name in documents.GetProperty("valid").EnumerateArray())
                parse(Load(kind, "valid", name.GetString()!));
            foreach (var name in documents.GetProperty("invalid").EnumerateArray())
            {
                try
                {
                    parse(Load(kind, "invalid", name.GetString()!));
                }
                catch (EngineProtocolError)
                {
                    continue;
                }
                throw new Exception($"{kind}/{name.GetString()}: accepted an invalid document");
            }
        }

        var evidence = ContextStorePreview.ParsePolicyEvidence(Load("evidence", "valid", "rich"));
        var unmeasured = evidence.Records[1];
        Check(!unmeasured.Quality.Measured && unmeasured.Quality.Retained is null, "unmeasured quality carries values");
        Check(unmeasured.Security.Regressions is null, "unmeasured security carries a count");
        Check(evidence.Records[0].Security.Regressions == 0, "measured security lost");
        var gapped = ContextStorePreview.ParseTaskLineage(Load("lineage", "valid", "gapped"));
        Check(!gapped.IsComplete && gapped.Deliveries[0].Summary is null, "gapped lineage reads complete");
        LiveEngine();
    }

    private static void LiveEngine()
    {
        var binary = Environment.GetEnvironmentVariable("LEANCTX_ENGINE_BINARY");
        if (string.IsNullOrEmpty(binary))
        {
            Console.WriteLine("SKIP context-store-preview live Engine (LEANCTX_ENGINE_BINARY)");
            return;
        }
        var engine = new SubprocessEngineClient(binary);
        // Private Engine storage refuses symlinked ancestors (macOS /var).
        var created = Directory.CreateTempSubdirectory("leanctx-store-dotnet").FullName;
        var root = created.StartsWith("/var/", StringComparison.Ordinal) && Directory.Exists("/private" + created)
            ? "/private" + created
            : created;
        var evidence = ContextStorePreview.ReadPolicyEvidenceAsync(engine, root,
            new ContextStoreScope("sdk-preview-fresh")).GetAwaiter().GetResult();
        Check(evidence.Records.Count == 0 && evidence.Evaluations.Count == 0, "fresh scope has evidence");
        var lineage = ContextStorePreview.ReadTaskLineageAsync(engine, root, "sdk-preview-unknown-task",
            new ContextStoreScope("sdk-preview-fresh", "tenant-a")).GetAwaiter().GetResult();
        Check(lineage.Outcome == "unknown" && lineage.Gaps.Contains("no_plan_recorded"), "unknown task gaps");
        Check(lineage.Scope?.Contains("tenant-a", StringComparison.Ordinal) == true, "lineage scope");
    }
}
