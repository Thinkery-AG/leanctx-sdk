// Compile in a fresh consumer using PackageReference to the candidate nupkg.
using System.Text.Json;
using System.Diagnostics;
using System.Text;
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
GitLabSource? source = null;
if (Environment.GetEnvironmentVariable("LEANCTX_REFERENCE_GITLAB_SOURCE") is string sourcePath)
{
    using var selected = JsonDocument.Parse(File.ReadAllText(sourcePath));
    var value = selected.RootElement;
    source = new GitLabSource(value.GetProperty("host").GetString()!, value.GetProperty("project").GetInt64(),
        value.GetProperty("namespace").GetString()!, value.GetProperty("glab").GetString()!,
        value.TryGetProperty("config_dir", out var directory) ? directory.GetString() : null);
}
using (var context = source is null ? AgentContext.Open(root, engineBinary: args[0]) : AgentContext.OpenWithGitLabSource(root, source, engineBinary: args[0]))
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
    if (source is not null)
    {
        var query = new Dictionary<string, object?> { ["action"]="query", ["provider"]="gitlab", ["resource"]="merge_requests",
            ["project"]=Environment.GetEnvironmentVariable("LEANCTX_REFERENCE_GITLAB_PROJECT"), ["mode"]="snapshot", ["limit"]=1 };
        var snapshot = context.Call("ctx_provider", query);
        var start = new ProcessStartInfo(Environment.GetEnvironmentVariable("LEANCTX_REFERENCE_PYTHON")!)
        { RedirectStandardInput=true, RedirectStandardOutput=true, RedirectStandardError=true, UseShellExecute=false };
        start.ArgumentList.Add(Environment.GetEnvironmentVariable("LEANCTX_REFERENCE_SNAPSHOT_OBSERVER")!);
        using (var observer = Process.Start(start)!)
        {
            try {
                observer.StandardInput.Write(snapshot.Text); observer.StandardInput.Close();
                checks["live_selected_gitlab"] = observer.WaitForExit(15000) && observer.ExitCode == 0;
            } finally { if (!observer.HasExited) observer.Kill(entireProcessTree:true); }
        }
        foreach (var refusal in new[] { ("foreign_project_refused", "project", "other/project"), ("unsupported_source_action_refused", "action", "refresh") })
        {
            var deniedQuery = new Dictionary<string, object?>(query) { [refusal.Item2]=refusal.Item3 };
            try { context.Call("ctx_provider", deniedQuery); checks[refusal.Item1]=false; }
            catch (AgentPermissionError) { checks[refusal.Item1]=true; }
        }
        try {
            File.Delete(policy);
            try { context.Read("login.py", ReadMode.Full); checks["source_policy_removal_closes_session"]=false; }
            catch (AgentPermissionError) { checks["source_policy_removal_closes_session"]=true; }
        } finally { File.WriteAllText(policy, rules); }
        using (var reconnected = context.Reconnect())
        {
            var freshSnapshot = reconnected.Call("ctx_provider", query);
            using (var observer = Process.Start(start)!)
            {
                try {
                    observer.StandardInput.Write(freshSnapshot.Text); observer.StandardInput.Close();
                    checks["reconnect_selected_gitlab"] = observer.WaitForExit(15000) && observer.ExitCode == 0;
                } finally { if (!observer.HasExited) observer.Kill(entireProcessTree:true); }
            }
            var fresh = reconnected.Read("login.py", ReadMode.Full).Text;
            checks["reconnect_protected_read"] = fresh.Contains("REFRESH_SESSION_FIRST") && fresh.Contains("REDACTED") && !fresh.Contains("CUS-1234");
            var deniedQuery = new Dictionary<string, object?>(query) { ["project"]="other/project" };
            try { reconnected.Call("ctx_provider", deniedQuery); checks["reconnect_foreign_project_refused"]=false; }
            catch (AgentPermissionError) { checks["reconnect_foreign_project_refused"]=true; }
        }
        responses.Clear();
    }
}
var passed = checks.Count >= 5 && checks.Values.All(value => value);
File.WriteAllText(args[3], JsonSerializer.Serialize(new { passed, checks, responses, sdk_location = typeof(AgentContext).Assembly.Location }));
if (!passed) throw new InvalidOperationException("installed reference failed");
