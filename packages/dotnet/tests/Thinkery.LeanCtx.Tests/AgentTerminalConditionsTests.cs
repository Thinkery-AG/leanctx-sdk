using System.Diagnostics;
using System.Reflection;
using System.Text;
using Thinkery.LeanCtx;

/// <summary>
/// Deadlines, cancellation and terminal conditions against a real child process
/// tree. The Engine is a shell stand-in; nothing here starts a LeanCTX instance.
/// </summary>
internal static class AgentTerminalConditionsTests
{
    private const string Hello =
        "{\"agent_tools_interface_version\":\"1.0.0\",\"allow_exec\":false,\"allow_write\":false," +
        "\"capabilities\":[\"ctx_compose\",\"ctx_glob\",\"ctx_read\",\"ctx_search\",\"ctx_symbol\",\"ctx_tree\"]," +
        "\"engine_version\":\"3.11.0\",\"schema_version\":1,\"transport_version\":1}";

    public static void Run()
    {
        if (OperatingSystem.IsWindows())
        {
            Console.WriteLine("SKIP agent-terminal-conditions (POSIX shell stand-in; Windows runs in the CI matrix)");
            return;
        }
        ClosedStdoutWithLiveEngineIsTerminalAtOnce();
        InvalidToolResultInValidEnvelopeTerminatesTheTree();
        RequestTheEngineNeverReadsIsBoundedByTheDeadline();
        CancelInterruptsStalledWriteAndPendingResponse();
        PolicyDirectoryThatCannotBeRemovedIsReported();
        UnreapableEngineIsABoundedStoredCleanupFailure();
        ManyDescendantsAndSlowEnumerationShareOneBudget();
        RepeatedAndParallelShutdownSharesOneSuccess();
    }

    /// <summary>
    /// Negative control: the kill is replaced by a no-op, so the stand-in really
    /// keeps running and the real bounded reap has to give up. The failure is
    /// stored and every later Cancel and Close reports it. The survivor is this
    /// test's own child and is ended by the test.
    /// </summary>
    private static void UnreapableEngineIsABoundedStoredCleanupFailure()
    {
        using var root = new Scratch();
        var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path, "sleep 30\n"), timeout: 30);
        var leader = Published(root.Path, "leader.pid");
        var descendant = Published(root.Path, "descendant.pid");
        ProcessTree.KillForTest = _ => { };
        try
        {
            var started = Stopwatch.StartNew();
            var first = Failure<InstanceExecutionError>(() => context.Cancel());
            if (started.Elapsed > TimeSpan.FromSeconds(8))
                throw new Exception("Cancel was not bounded for an unreapable Engine");
            if (!first.Message.Contains("could not be reaped", StringComparison.Ordinal))
                throw new Exception($"unexpected cleanup failure: {first.Message}");
            if (!Alive(leader))
                throw new Exception("the stand-in was expected to survive the no-op kill");
            for (var attempt = 0; attempt < 2; attempt++)
            {
                var again = Stopwatch.StartNew();
                if (!ReferenceEquals(Failure<InstanceExecutionError>(() => context.Cancel()), first) ||
                    !ReferenceEquals(Failure<InstanceExecutionError>(() => context.Close()), first))
                    throw new Exception("a later shutdown did not report the stored failure");
                if (again.Elapsed > TimeSpan.FromSeconds(3))
                    throw new Exception("a later shutdown waited again for the failed cleanup");
            }
            Throws<InstanceCrashed>(() => context.Read("README.md"));
        }
        finally
        {
            ProcessTree.KillForTest = null;
            foreach (var pid in new[] { leader, descendant })
            {
                try { using var own = Process.GetProcessById(pid); own.Kill(); }
                catch (Exception) { }
            }
        }
        RequireGone(root.Path, "leader.pid");
        RequireGone(root.Path, "descendant.pid");
    }

    /// <summary>
    /// One budget for the whole cleanup of the owner. The enumeration is replaced
    /// by a slow stand-in that returns six of this test's own long-running
    /// processes, none of which the tree kill reaches. With a wait per
    /// descendant the owner would take six times the per-step wait; with one
    /// deadline it gives up when the budget is spent, and that failure is the
    /// stored outcome for every later Cancel and Close.
    /// </summary>
    private static void ManyDescendantsAndSlowEnumerationShareOneBudget()
    {
        using var root = new Scratch();
        var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path, "sleep 30\n"), timeout: 30);
        Published(root.Path, "leader.pid");
        var sleepers = Enumerable.Range(0, 6).Select(_ => Process.Start(new ProcessStartInfo("/bin/sleep")
        {
            ArgumentList = { "60" }, UseShellExecute = false,
        })!).ToList();
        var budget = ProcessTree.CleanupBudget;
        ProcessTree.CleanupBudget = TimeSpan.FromMilliseconds(1500);
        ProcessTree.DescendantsForTest = _ =>
        {
            Thread.Sleep(400); // slow enumeration draws on the same budget
            return sleepers.Select(sleeper => Process.GetProcessById(sleeper.Id)).ToList();
        };
        try
        {
            var started = Stopwatch.StartNew();
            var first = Failure<InstanceExecutionError>(() => context.Cancel());
            if (started.Elapsed > TimeSpan.FromSeconds(4))
                throw new Exception($"the owner's cleanup took {started.Elapsed}, more than its one budget allows");
            if (!first.Message.Contains("within the cleanup budget", StringComparison.Ordinal))
                throw new Exception($"unexpected cleanup failure: {first.Message}");
            if (!ReferenceEquals(Failure<InstanceExecutionError>(() => context.Close()), first) ||
                !ReferenceEquals(Failure<InstanceExecutionError>(() => context.Cancel()), first))
                throw new Exception("a later shutdown did not report the stored failure");
        }
        finally
        {
            ProcessTree.DescendantsForTest = null;
            ProcessTree.CleanupBudget = budget;
            foreach (var sleeper in sleepers)
            {
                try { sleeper.Kill(); sleeper.WaitForExit(5000); }
                catch (Exception) { }
                sleeper.Dispose();
            }
        }
        // The Engine itself was killed for real: only the waits were cut short.
        RequireGone(root.Path, "leader.pid");
        RequireGone(root.Path, "descendant.pid");
    }

    /// <summary>Positive control: repeated and parallel shutdowns all succeed.</summary>
    private static void RepeatedAndParallelShutdownSharesOneSuccess()
    {
        using var root = new Scratch();
        var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path, "sleep 30\n"), timeout: 30);
        Published(root.Path, "leader.pid");
        var failures = new System.Collections.Concurrent.ConcurrentBag<Exception>();
        var callers = Enumerable.Range(0, 8).Select(index => new Thread(() =>
        {
            try { if (index % 2 == 0) context.Cancel(); else context.Close(); }
            catch (Exception error) { failures.Add(error); }
        })).ToList();
        callers.ForEach(caller => caller.Start());
        foreach (var caller in callers)
        {
            if (!caller.Join(TimeSpan.FromSeconds(20)))
                throw new Exception("a parallel shutdown did not return");
        }
        if (!failures.IsEmpty)
            throw new Exception($"parallel shutdown failed: {failures.First()}");
        context.Cancel();
        RequireTerminal(root.Path, context);
    }

    private static T Failure<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T expected) { return expected; }
        catch (Exception error)
        {
            throw new Exception($"expected {typeof(T).Name}, got {error.GetType().Name}: {error}", error);
        }
        throw new Exception($"expected {typeof(T).Name}");
    }

    /// <summary>Answers the hello, keeps one descendant alive, records both identifiers, then runs afterHello.</summary>
    private static string StandIn(string root, string afterHello)
    {
        var path = Path.Combine(root, "stand-in-engine.sh");
        var script = "#!/bin/sh\n" +
            "IFS= read -r line\n" +
            "printf '%s' \"$$\" > leader.pid.tmp && mv leader.pid.tmp leader.pid\n" +
            "sleep 30 >/dev/null 2>&1 &\n" +
            "printf '%s' \"$!\" > descendant.pid.tmp && mv descendant.pid.tmp descendant.pid\n" +
            "printf '%s\\n' '{\"id\":\"1\",\"ok\":true,\"result\":" + Hello + "}'\n" +
            afterHello;
        File.WriteAllText(path, script, new UTF8Encoding(false));
        // A shell stand-in exists on POSIX only; the mode call has no Windows counterpart.
        if (!OperatingSystem.IsWindows())
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        return path;
    }

    private static int Published(string root, string name)
    {
        var path = Path.Combine(root, name);
        var deadline = Stopwatch.StartNew();
        while (!File.Exists(path) && deadline.Elapsed < TimeSpan.FromSeconds(3))
            Thread.Sleep(10);
        return int.Parse(File.ReadAllText(path).Trim(), System.Globalization.CultureInfo.InvariantCulture);
    }

    private static bool Alive(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return !process.HasExited;
        }
        catch (ArgumentException) { return false; }
        catch (InvalidOperationException) { return false; }
    }

    private static void RequireGone(string root, string name)
    {
        var pid = Published(root, name);
        var deadline = Stopwatch.StartNew();
        while (Alive(pid) && deadline.Elapsed < TimeSpan.FromSeconds(3))
            Thread.Sleep(10);
        if (!Alive(pid))
            return;
        try { using var survivor = Process.GetProcessById(pid); survivor.Kill(); }
        catch (Exception) { }
        throw new Exception($"{name} process {pid} survived");
    }

    private static void RequireTerminal(string root, AgentContext context)
    {
        RequireGone(root, "leader.pid");
        RequireGone(root, "descendant.pid");
        if (Directory.EnumerateDirectories(root, ".leanctx-agent-*", SearchOption.TopDirectoryOnly).Any())
            throw new Exception("temporary policy directory left behind");
        Throws<InstanceCrashed>(() => context.Read("README.md"));
        context.Close();
        context.Close();
    }

    private static Dictionary<string, object?> Oversized() =>
        new(StringComparer.Ordinal) { ["padding"] = new string('x', 600_000) };

    private static void ClosedStdoutWithLiveEngineIsTerminalAtOnce()
    {
        using var root = new Scratch();
        var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path, "IFS= read -r line\nexec 1>&-\nsleep 30\n"), timeout: 20);
        var started = Stopwatch.StartNew();
        Throws<InstanceCrashed>(() => context.Read("README.md"));
        if (started.Elapsed > TimeSpan.FromSeconds(5))
            throw new Exception("closed stdout waited for the deadline");
        RequireTerminal(root.Path, context);
    }

    private static void InvalidToolResultInValidEnvelopeTerminatesTheTree()
    {
        using var root = new Scratch();
        var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path,
            "IFS= read -r line\nprintf '%s\\n' '{\"id\":\"2\",\"ok\":true,\"result\":{\"extra\":true}}'\nsleep 30\n"), timeout: 5);
        Throws<InstanceProtocolError>(() => context.Read("README.md"));
        RequireTerminal(root.Path, context);
    }

    private static void RequestTheEngineNeverReadsIsBoundedByTheDeadline()
    {
        using var root = new Scratch();
        var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path, "sleep 30\n"), timeout: 1);
        var started = Stopwatch.StartNew();
        Throws<InstanceTimeout>(() => context.Call("ctx_read", Oversized()));
        if (started.Elapsed > TimeSpan.FromSeconds(5))
            throw new Exception("stalled write outlived its deadline");
        RequireTerminal(root.Path, context);
    }

    private static void CancelInterruptsStalledWriteAndPendingResponse()
    {
        foreach (var arguments in new[] { Oversized(), new Dictionary<string, object?>(StringComparer.Ordinal) })
        {
            using var root = new Scratch();
            // The deadline is far away: only Cancel may end this call.
            var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path, "sleep 30\n"), timeout: 30);
            Published(root.Path, "leader.pid");
            Exception? failure = null;
            var caller = new Thread(() =>
            {
                try { context.Call("ctx_read", arguments); }
                catch (Exception error) { failure = error; }
            });
            caller.Start();
            Thread.Sleep(300);
            var started = Stopwatch.StartNew();
            context.Cancel();
            if (started.Elapsed > TimeSpan.FromSeconds(4))
                throw new Exception("Cancel was not bounded");
            if (!caller.Join(TimeSpan.FromSeconds(3)))
                throw new Exception("cancelled call is still blocked");
            if (failure is not InstanceCrashed)
                throw new Exception($"expected InstanceCrashed, got {failure?.GetType().Name}: {failure}");
            RequireTerminal(root.Path, context);
        }
    }

    private static void PolicyDirectoryThatCannotBeRemovedIsReported()
    {
        using var root = new Scratch();
        var context = AgentContext.Open(root.Path, engineBinary: StandIn(root.Path, "sleep 30\n"), timeout: 5);
        var owned = Path.Combine(context.ProjectRoot, ".leanctx-agent-undeletable");
        Directory.CreateDirectory(owned);
        var blocker = Path.Combine(owned, "not-created-by-the-sdk");
        File.WriteAllText(blocker, "keep");
        SetField(context, "policyPath", Path.Combine(owned, "policy.json"));
        SetField(context, "policyDirectory", owned);
        Throws<InstanceExecutionError>(() => context.Cancel());
        if (File.ReadAllText(blocker) != "keep")
            throw new Exception("cleanup touched a file it did not create");
        Throws<InstanceExecutionError>(() => context.Close());
        File.Delete(blocker);
        context.Close();
        if (Directory.Exists(owned))
            throw new Exception("policy directory still present");
        context.Close();
        RequireGone(root.Path, "leader.pid");
        RequireGone(root.Path, "descendant.pid");
    }

    private static void SetField(AgentContext context, string name, string value)
    {
        var field = typeof(AgentContext).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)
            ?? throw new Exception($"field {name} not found");
        field.SetValue(context, value);
    }

    private static void Throws<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T) { return; }
        catch (Exception error)
        {
            throw new Exception($"expected {typeof(T).Name}, got {error.GetType().Name}: {error}", error);
        }
        throw new Exception($"expected {typeof(T).Name}");
    }

    private sealed class Scratch : IDisposable
    {
        public Scratch()
        {
            Path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "leanctx-dotnet-tree-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path);
        }

        public string Path { get; }

        public void Dispose()
        {
            try { Directory.Delete(Path, true); }
            catch (Exception) { }
        }
    }
}
