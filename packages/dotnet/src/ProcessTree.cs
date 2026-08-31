using System.Diagnostics;

namespace Thinkery.LeanCtx;

internal static class ProcessTree
{
    internal static void KillAndReap(Process process, string label)
    {
        var descendants = CaptureLinuxDescendants(process.Id, label);
        try
        {
            if (!process.HasExited)
                process.Kill(entireProcessTree: true);
            if (!process.WaitForExit(2000))
                throw new EngineExecutionError($"{label} process could not be reaped");
            foreach (var descendant in descendants)
            {
                try
                {
                    if (!descendant.HasExited && !descendant.WaitForExit(2000))
                        throw new EngineExecutionError($"{label} descendant could not be reaped");
                }
                catch (InvalidOperationException)
                {
                    // The captured descendant already exited and its handle is no longer valid.
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
    }

    private static List<Process> CaptureLinuxDescendants(int rootPid, string label)
    {
        var result = new List<Process>();
        if (!OperatingSystem.IsLinux())
            return result;
        var seen = new HashSet<int> { rootPid };
        var pending = new Queue<int>();
        pending.Enqueue(rootPid);
        while (pending.TryDequeue(out var parent))
        {
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
}
