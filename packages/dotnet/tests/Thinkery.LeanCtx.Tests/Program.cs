using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using Thinkery.LeanCtx;

internal static class Program
{
    private static int failures;

    private static void Main()
    {
        Run("constants", ConstantsTest);
        Run("product-golden-hashes", ProductGoldenHashes);
        Run("validation-and-path-jail", ValidationAndPathJail);
        Run("engine-parser-negatives", EngineParserNegatives);
        Run("agent-permission-negative", AgentPermissionNegative);
        Run("agent-protocol-negative", AgentProtocolNegative);
        Run("agent-timeout-negative", AgentTimeoutNegative);
        Run("engine-v1-optional", EngineV1Optional);
        if (failures != 0)
            throw new Exception($"{failures} test group(s) failed");
        Console.WriteLine("all .NET SDK tests passed");
    }

    private static void Run(string name, Action action)
    {
        try
        {
            action();
            Console.WriteLine($"PASS {name}");
        }
        catch (Exception error)
        {
            failures++;
            Console.Error.WriteLine($"FAIL {name}: {error.GetType().Name}: {error.Message}");
        }
    }

    private static void ConstantsTest()
    {
        Equal("1.1.0", Constants.__version__);
        Equal("1.0.0", Constants.ENGINE_INTERFACE_VERSION);
        Equal("1.0.0", Constants.AGENT_TOOLS_INTERFACE_VERSION);
        Equal("3.10.1", Constants.SUPPORTED_AGENT_TOOLS_ENGINE_VERSION);
        Equal(1, Constants.SCHEMA_VERSION);
        Equal(1, Constants.TRANSPORT_VERSION);
    }

    private static void ProductGoldenHashes()
    {
        var source = new ContextSource("fixture/source.txt", "/PROJECT");
        var plan = new ContextPlan("session-fixed", "task-fixed", "inspect", source);
        var text = "fresh synthetic view\n";
        var sourceText = "fresh synthetic source\n";
        var outputDigest = Digest(text);
        var sourceDigest = Digest(sourceText);
        var sourceRef = "source:synthetic-path-sha256:" + new string('a', 64);
        var inputRef = "input:synthetic-request-sha256:" + new string('b', 64);
        var invocationId = "engine-invocation-synthetic";
        var measurements = new List<ContextMeasurement>
        {
            new("input_tokens", "token", "measured", 1),
            new("output_tokens", "token", "measured", 2),
        };
        var receiptLink = new ContextReceiptLink(1, "engine-receipt-synthetic",
            "receipt:sha256:" + new string('d', 64), "sha256:" + new string('d', 64),
            invocationId);
        var invocation = Dict(
            ("schema_version", 1L),
            ("invocation_id", invocationId),
            ("engine", Dict(("engine_id", "lean-ctx-local"), ("engine_version", "3.9.20"))),
            ("operation", Dict(("capability_id", "capability://leanctx/context-optimization"),
                ("capability_version", "1.0.0"))),
            ("input_ref", inputRef),
            ("input_digest", "sha256:" + new string('c', 64)),
            ("source_refs", new List<object?> { inputRef, sourceRef }),
            ("policy_admission", Dict(("policy_ref", "policy:synthetic"), ("decision", "admitted"))));
        var observation = Dict(
            ("schema_version", 1L),
            ("invocation_id", invocationId),
            ("status", "succeeded"),
            ("output_ref", "output:" + outputDigest[7..]),
            ("output_digest", outputDigest),
            ("source_lineage", new List<object?> { inputRef, sourceRef }),
            ("measurements", measurements),
            ("failure", null),
            ("receipt_link", receiptLink));
        var view = new ContextView(source, text, "output:" + outputDigest[7..], outputDigest,
            sourceRef, sourceDigest, inputRef, EngineStatus.Succeeded, measurements, null,
            receiptLink, invocation, observation);
        var receipt = new ContextReceipt("session-fixed", "task-fixed", plan.PlanId, view,
            HostOutcome.Completed, Integrity.Sealed, usage: Dict(("requests", 1L)));
        var session = new ContextSession("inspect", "/PROJECT", "session-fixed", "task-fixed");
        var actual = new Dictionary<string, string>
        {
            ["ContextSource"] = Fingerprint(source.ToDictionary()),
            ["ContextPlan"] = Fingerprint(plan.ToDictionary()),
            ["ContextView"] = Fingerprint(view.ToDictionary()),
            ["ContextReceipt"] = Fingerprint(receipt.ToDictionary()),
            ["ContextSession"] = Fingerprint(Dict(
                ("session_id", session.SessionId), ("task_id", session.TaskId),
                ("task", session.Task), ("state", "created"))),
        };
        Equal("814ab90ae5f1ab6e93d1f447c703572c04174f7c0dccdd8939daeb304828ee9f", actual["ContextSource"]);
        Equal("a948177b44cfd1fd22b5aa59bd4d0210510675eb0742d219ac2ac36ed09a6d75", actual["ContextPlan"]);
        Equal("b80a6a0055e6ff06724f99990d59f03bbd4cf407d0143085a858cd1949b18918", actual["ContextView"]);
        Equal("0edf6bdc1afd5eb605a01900a99ff1d18579d98ba09719c4397d7366bfeca963", actual["ContextReceipt"]);
        Equal("219d600e70f8421386b034395f7db4e8d6494cb14d57cd34e63058e51834735c", actual["ContextSession"]);
        Equal("plan:sha256:25f29db61cbb19986896152ecf2c8b1b60a1187c83a8e4ceefb0b7203542296e",
            new ContextPlan("fixture-session-r1", "fixture-task-r1", "inspect the synthetic fixture", source).PlanId);
    }

    private static void ValidationAndPathJail()
    {
        Throws<ValidationError>(() => new ContextSource("../escape.txt", "/PROJECT"));
        Throws<ValidationError>(() => new ContextSource("fixture\0.txt", "/PROJECT"));
        Throws<ConfigurationError>(() => new SubprocessEngineClient(timeout: 0.09));
        Throws<ValidationError>(() => new ContextMeasurement("Input", "token", "measured", 1));
        Throws<ValidationError>(() => new ContextReceiptLink(1, "receipt", "bad", "sha256:" + new string('a', 64), "i"));
        Throws<ValidationError>(() => new ExecutionPolicy(allowedEnv: new[] { "PATH" }));
        Throws<ConfigurationError>(() => new AgentContext("/missing-leanctx-root"));
        using var root = new TemporaryDirectory();
        using var outside = new TemporaryDirectory();
        var escape = Path.Combine(root.Path, "escape-link");
        Directory.CreateSymbolicLink(escape, outside.Path);
        True(!AgentContext.IsSafeDirectory(root.Path, escape));
        Equal("{\"\":2,\"𐀀\":1}", WireJson.CanonicalJson(Dict(("𐀀", 1L), ("", 2L))));
        Throws<ValidationError>(() => WireJson.CanonicalBytes(Dict(("value", 1.5))));
    }

    private static void EngineParserNegatives()
    {
        var malformed = Encoding.UTF8.GetBytes("{\"schema_version\":1,\"schema_version\":1}");
        Throws<EngineProtocolError>(() => SubprocessEngineClient.ParseForTest(malformed));
        var unknown = "{\"engine_interface_version\":\"1.0.0\",\"invocation\":null,\"observation\":null,\"recovery\":{},\"schema_version\":1,\"transport_version\":1,\"view\":{},\"extra\":1}";
        Throws<EngineProtocolError>(() => SubprocessEngineClient.ParseForTest(Encoding.UTF8.GetBytes(unknown)));
    }

    private static void AgentPermissionNegative()
    {
        using var root = new TemporaryDirectory();
        var fake = FakeAgent(root.Path, "good");
        using var context = AgentContext.Open(root.Path, engineBinary: fake);
        var result = context.Read("README.md");
        Equal("ctx_read:ok", result.Text);
        Equal(1L, context.Metrics.ToolCalls);
        Throws<AgentPermissionError>(() => context.Run(new[] { "echo" }));
        Throws<AgentPermissionError>(() => context.Patch(Dict(("path", "x"), ("op", "create"))));
        context.Close();
        if (Directory.EnumerateDirectories(root.Path, ".leanctx-agent-*", SearchOption.TopDirectoryOnly).Any())
            throw new Exception("Agent policy directory was not cleaned");
    }

    private static void AgentProtocolNegative()
    {
        using var root = new TemporaryDirectory();
        var fake = FakeAgent(root.Path, "bad-hello");
        Throws<EngineProtocolError>(() => AgentContext.Open(root.Path, engineBinary: fake));
        if (Directory.EnumerateDirectories(root.Path, ".leanctx-agent-*", SearchOption.TopDirectoryOnly).Any())
            throw new Exception("startup failure left policy state");
    }

    private static void AgentTimeoutNegative()
    {
        using var root = new TemporaryDirectory();
        var fake = FakeAgent(root.Path, "hang");
        using var context = AgentContext.Open(root.Path, engineBinary: fake, timeout: 0.2);
        Throws<EngineTimeout>(() => context.Read("README.md"));
        Throws<EngineCrashed>(() => context.Read("README.md"));
    }

    private static void EngineV1Optional()
    {
        if (!File.Exists("/usr/bin/python3"))
        {
            Console.WriteLine("SKIP engine-v1-optional (python3 unavailable)");
            return;
        }
        using var root = new TemporaryDirectory();
        var sourcePath = Path.Combine(root.Path, "source.txt");
        File.WriteAllText(sourcePath, "engine fixture\n");
        var fake = FakeEngine(root.Path);
        var source = new ContextSource("source.txt", root.Path);
        var client = new SubprocessEngineClient(fake, timeout: 5);
        var view = client.ContextView(new ContextPlan("s", "t", "inspect", source));
        True(view.Verify());
        var recovered = client.Recover(root.Path, "source.txt", view.RecoveryRef!, view.SourceRef, view.SourceDigest);
        Equal("engine fixture\n", recovered.Text);
    }

    private static string FakeAgent(string root, string behavior)
    {
        var path = Path.Combine(root, "fake-agent.sh");
        string script;
        if (behavior == "bad-hello")
        {
            script = """
#!/bin/sh
IFS= read -r line
id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
printf '{"id":"%s","ok":true,"result":{"agent_tools_interface_version":"1.0.0","allow_exec":false,"allow_write":false,"capabilities":[],"engine_version":"3.10.1","schema_version":1,"transport_version":1}}\n' "$id"
while :; do sleep 10; done
""";
        }
        else if (behavior == "hang")
        {
            script = """
#!/bin/sh
IFS= read -r line
printf '{"id":"1","ok":true,"result":{"agent_tools_interface_version":"1.0.0","allow_exec":false,"allow_write":false,"capabilities":["ctx_compose","ctx_glob","ctx_read","ctx_search","ctx_symbol","ctx_tree"],"engine_version":"3.10.1","schema_version":1,"transport_version":1}}\n'
IFS= read -r line
while :; do sleep 10; done
""";
        }
        else
        {
            script = """
#!/bin/sh
while IFS= read -r line; do
  id=$(printf '%s' "$line" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
  case "$line" in
    *'"op":"hello"'*) printf '{"id":"%s","ok":true,"result":{"agent_tools_interface_version":"1.0.0","allow_exec":false,"allow_write":false,"capabilities":["ctx_compose","ctx_glob","ctx_read","ctx_search","ctx_symbol","ctx_tree"],"engine_version":"3.10.1","schema_version":1,"transport_version":1}}\n' "$id" ;;
    *'"op":"close"'*) printf '{"id":"%s","ok":true,"result":{}}\n' "$id"; exit 0 ;;
    *) printf '{"id":"%s","ok":true,"result":{"text":"ctx_read:ok","content_blocks":[],"original_tokens":10,"output_tokens":4,"saved_tokens":6,"mode":null,"changed":false,"shell":null}}\n' "$id" ;;
  esac
done
""";
        }
        File.WriteAllText(path, script, new UTF8Encoding(false));
        SetExecutable(path);
        return path;
    }

    private static string FakeEngine(string root)
    {
        var path = Path.Combine(root, "fake-engine.py");
        var script = """
#!/usr/bin/python3
import hashlib, json, os, sys
args = sys.argv[1:]
op = args[1]
root = args[args.index('--project-root') + 1]
request = json.load(open(args[args.index('--json-file') + 1]))
if op == 'context-view':
    rel = request['path']; text = open(os.path.join(root, rel)).read()
    source_digest = 'sha256:' + hashlib.sha256(text.encode()).hexdigest()
    source_ref = 'source:fixture-' + 'a' * 64
    recovery_ref = 'input:fixture-' + 'b' * 64
    invocation_id = 'engine-invocation-fixture'
    output_digest = 'sha256:' + hashlib.sha256(text.encode()).hexdigest()
    invocation = {'schema_version':1,'invocation_id':invocation_id,'engine':{'engine_id':'lean-ctx-local','engine_version':'3.10.1'},'operation':{'capability_id':'capability://leanctx/context-optimization','capability_version':'1.0.0'},'input_ref':recovery_ref,'input_digest':'sha256:'+'c'*64,'source_refs':[recovery_ref,source_ref],'policy_admission':{'policy_ref':'policy:fixture','decision':'admitted'}}
    receipt = {'schema_version':1,'receipt_id':'engine-receipt-fixture','receipt_ref':'receipt:sha256:'+'d'*64,'receipt_digest':'sha256:'+'d'*64,'invocation_id':invocation_id}
    observation = {'schema_version':1,'invocation_id':invocation_id,'status':'succeeded','output_ref':'output:'+output_digest[7:],'output_digest':output_digest,'source_lineage':[recovery_ref,source_ref],'measurements':[],'failure':None,'receipt_link':receipt}
    response = {'schema_version':1,'transport_version':1,'engine_interface_version':'1.0.0','view':{'text':text,'output_ref':'output:'+output_digest[7:],'output_digest':output_digest},'invocation':invocation,'observation':observation,'recovery':{'recovery_ref':recovery_ref,'source_ref':source_ref,'source_digest':source_digest}}
else:
    text = open(os.path.join(root, request['path'])).read()
    digest = 'sha256:' + hashlib.sha256(text.encode()).hexdigest()
    response = {'schema_version':1,'transport_version':1,'engine_interface_version':'1.0.0','view':{'text':text,'output_ref':'output:'+digest[7:],'output_digest':digest},'invocation':None,'observation':None,'recovery':{'recovery_ref':request['recovery_ref'],'source_ref':request['source_ref'],'source_digest':request['source_digest']}}
print(json.dumps(response, separators=(',',':')))
""";
        File.WriteAllText(path, script, new UTF8Encoding(false));
        SetExecutable(path);
        return path;
    }

    private static Dictionary<string, object?> Dict(params (string Key, object? Value)[] values) =>
        values.ToDictionary(item => item.Key, item => item.Value, StringComparer.Ordinal);

    private static string Digest(string value) =>
        "sha256:" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static string Fingerprint(object value) =>
        WireJson.Sha256Digest(WireJson.CanonicalBytes(value))[7..];

    private static void SetExecutable(string path)
    {
        if (!OperatingSystem.IsWindows())
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
    }

    private static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
            throw new Exception($"expected {expected}, got {actual}");
    }

    private static void True(bool value)
    {
        if (!value) throw new Exception("expected true");
    }

    private static void Throws<T>(Action action) where T : Exception
    {
        try
        {
            action();
        }
        catch (T) { return; }
        catch (Exception error)
        {
            throw new Exception($"expected {typeof(T).Name}, got {error.GetType().Name}: {error}", error);
        }
        throw new Exception($"expected {typeof(T).Name}");
    }

    private sealed class TemporaryDirectory : IDisposable
    {
        public TemporaryDirectory() { Path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "leanctx-dotnet-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(Path); File.WriteAllText(System.IO.Path.Combine(Path, "README.md"), "test\n"); }
        public string Path { get; }
        public void Dispose() { try { Directory.Delete(Path, true); } catch { } }
    }
}
