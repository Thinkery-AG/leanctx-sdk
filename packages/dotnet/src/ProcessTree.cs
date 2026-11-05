// Until 2.0 the SDK throws the deprecated Engine error subtypes so that both the
// Engine and the Instance names catch them (see Errors.cs).
#pragma warning disable CS0618
using System.Diagnostics;

namespace Thinkery.LeanCtx;

internal static class ProcessTree
{
    /// <summary>
    /// Test seam: replaces the kill, and only the kill. The bounded reap that
    /// follows is the real one, so a kill that does nothing exercises the
    /// path of a LeanCTX instance that cannot be terminated. Null in production.
    /// </summary>
    internal static Action<Process>? KillForTest;

    /// <summary>
    /// The one budget for a whole cleanup: enumerating descendants, and every
    /// wait for the leader and for each descendant, share it. Internal and
    /// mutable so that tests can prove the bound without waiting for it.
    /// </summary>
    internal static TimeSpan CleanupBudget = TimeSpan.FromSeconds(5);

    /// <summary>Test seam: replaces the descendant enumeration. Null in production.</summary>
    internal static Func<int, List<Process>>? DescendantsForTest;

    /// <summary>
    /// Ends the process tree and reaps it under one monotonic deadline. The
    /// kill is always issued. Every wait draws on what is left of the budget;
    /// when it is used up the remaining waits are skipped and the cleanup is
    /// reported as failed, never as done. The kill call itself is not
    /// interruptible and is the one step outside the deadline.
    /// </summary>
    internal static void KillAndReap(Process process, string label)
    {
        var clock = Stopwatch.StartNew();
        var budget = CleanupBudget;
        int Remaining() => (int)Math.Max(0, Math.Min(int.MaxValue, (budget - clock.Elapsed).TotalMilliseconds));
        string? failure = null;
        var complete = true;
        var descendants = DescendantsForTest is { } capture
            ? capture(process.Id)
            : CaptureLinuxDescendants(process.Id, label, () => clock.Elapsed >= budget, out complete);
        try
        {
            if (!complete || clock.Elapsed >= budget)
                failure = $"{label} descendants could not be inspected within the cleanup budget";
            if (!process.HasExited)
            {
                if (KillForTest is { } kill) kill(process);
                else process.Kill(entireProcessTree: true);
            }
            if (!process.WaitForExit(Remaining()))
            {
                failure ??= $"{label} process could not be reaped";
            }
            else
            {
                foreach (var descendant in descendants)
                {
                    try
                    {
                        if (descendant.HasExited)
                            continue;
                        if (!descendant.WaitForExit(Remaining()))
                        {
                            // The budget is spent or this descendant survived:
                            // either way nothing further is waited for.
                            failure ??= $"{label} descendants could not be reaped within the cleanup budget";
                            break;
                        }
                    }
                    catch (InvalidOperationException)
                    {
                        // The captured descendant already exited and its handle is no longer valid.
                    }
                }
            }
        }
        catch (InvalidOperationException)
        {
            throw new EngineExecutionError($"{label} process could not be terminated");
        }
        catch (System.ComponentModel.Win32Exception)
        {
            throw new EngineExecutionError($"{label} process tree could not be terminated");
        }
        finally
        {
            foreach (var descendant in descendants)
                descendant.Dispose();
        }
        if (failure is not null)
            throw new EngineExecutionError(failure);
    }

    private static List<Process> CaptureLinuxDescendants(int rootPid, string label,
        Func<bool> expired, out bool complete)
    {
        complete = true;
        var result = new List<Process>();
        if (!OperatingSystem.IsLinux())
            return result;
        var seen = new HashSet<int> { rootPid };
        var pending = new Queue<int>();
        pending.Enqueue(rootPid);
        while (pending.TryDequeue(out var parent))
        {
            if (expired())
            {
                // The enumeration is cut off; what was captured is still reaped.
                complete = false;
                return result;
            }
            var path = $"/proc/{parent}/task/{parent}/children";
            string children;
            try
            {
                children = File.ReadAllText(path);
            }
            catch (FileNotFoundException)
            {
                continue;
            }
            catch (DirectoryNotFoundException)
            {
                continue;
            }
            catch (IOException) when (HasEnded(parent))
            {
                // The process exited while it was being read: procfs answers
                // ESRCH (an IOException, not FileNotFound) for a task that is
                // gone or a zombie. It has no children left to capture.
                continue;
            }
            catch (IOException)
            {
                throw new EngineExecutionError($"{label} descendants could not be inspected");
            }
            foreach (var value in children.Split(' ', StringSplitOptions.RemoveEmptyEntries))
            {
                if (!int.TryParse(value, out var pid) || pid <= 0 || !seen.Add(pid))
                    continue;
                pending.Enqueue(pid);
                try
                {
                    result.Add(Process.GetProcessById(pid));
                }
                catch (ArgumentException)
                {
                    // The descendant exited between the procfs snapshot and handle capture.
                }
            }
        }
        return result;
    }

    /// <summary>
    /// Whether a Linux process has ended: its procfs entry is gone, or it is a
    /// zombie (state Z) or dead (state X) task.
    /// </summary>
    internal static bool HasEnded(int pid)
    {
        string stat;
        try
        {
            stat = File.ReadAllText($"/proc/{pid}/stat");
        }
        catch (IOException)
        {
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
        // The command name in parentheses may contain spaces or ')'; the state
        // is the first field after the last ')'.
        var end = stat.LastIndexOf(')');
        if (end < 0 || end + 2 >= stat.Length)
            return false;
        var state = stat[end + 2];
        return state is 'Z' or 'X';
    }
}
