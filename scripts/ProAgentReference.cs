// Compile in a fresh consumer using PackageReference to the candidate nupkg.
using System.Text.Json;
using Thinkery.LeanCtx;

if (args.Length != 4) throw new ArgumentException("engine, previous engine, fixture root, output required");
var root = args[2];
var policy = Path.Combine(root, ".lean-ctx", "policy.toml");
var rules = File.ReadAllText(policy);
var checks = new SortedDictionary<string, bool>();
var responses = new SortedDictionary<string, string>();
bool oldRejected = false;
try { using var old = AgentContext.Open(root, engineBinary: args[1]); }
catch (EngineProtocolError error) { oldRejected = error.Message.Contains("hello is incompatible", StringComparison.Ordinal); }
checks["old_engine_rejected"] = oldRejected;
using (var context = AgentContext.Open(root, engineBinary: args[0]))
{
    var read = context.Read("login.py", ReadMode.Full).Text;
    var composed = context.Compose("investigate authentication retry").Text;
    if (Environment.GetEnvironmentVariable("LEANCTX_REFERENCE_PRO") == "1")
        checks["pro_context_selection"] = composed.Contains("Pro context selection:")
            && !composed.Contains("Pro context selection unavailable");
    checks["useful_masked_read"] = read.Contains("REFRESH_SESSION_FIRST") && read.Contains("REDACTED") && !read.Contains("CUS-1234");
    checks["useful_protected_compose"] = composed.Contains("REFRESH_SESSION_FIRST") && composed.Contains("login.py") && new[] { "CUS-1234", "PRIVATE_CANARY", "private.py" }.All(value => !composed.Contains(value));
    responses["read"] = read; responses["compose"] = composed;
    var separator = rules.EndsWith("\n", StringComparison.Ordinal) ? "" : "\n";
    var deniedContext = "[context]\ndeny_tools=[\"ctx_read\"]\n";
    File.WriteAllText(policy, rules.Contains("[context]", StringComparison.Ordinal) ? rules.Replace("[context]", deniedContext, StringComparison.Ordinal) : rules + separator + deniedContext);
    string denied;
    bool blocked;
    try { denied = context.Read("login.py", ReadMode.Full).Text; blocked = denied.Contains("POLICY BLOCKED"); }
    catch (Exception error) when (error is AgentPermissionError or EngineExecutionError)
    { denied = error.Message; blocked = denied.Contains("policy", StringComparison.OrdinalIgnoreCase); }
    checks["changed_rule_blocks_read"] = blocked && !denied.Contains("REFRESH_SESSION_FIRST") && !denied.Contains("CUS-1234");
    responses["denied"] = denied;
    File.WriteAllText(policy, rules);
    var restored = context.Read("login.py", ReadMode.Full).Text;
    checks["same_session_rule_repair"] = restored.Contains("REFRESH_SESSION_FIRST") && !restored.Contains("CUS-1234");
    responses["restored"] = restored;
}
var passed = checks.Count >= 5 && checks.Values.All(value => value);
File.WriteAllText(args[3], JsonSerializer.Serialize(new { passed, checks, responses, sdk_location = typeof(AgentContext).Assembly.Location }));
if (!passed) throw new InvalidOperationException("installed reference failed");
