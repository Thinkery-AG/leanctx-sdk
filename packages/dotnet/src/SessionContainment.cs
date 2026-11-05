using System.Diagnostics;
using System.Text;

namespace Thinkery.LeanCtx;

/// <summary>
/// How the instance's own process tree is contained.
/// <para>
/// .NET cannot put a child into a process group of its own. With
/// <see cref="InstanceStarter"/> the instance's session starter is what provides
/// that group: the process this SDK started is the starter; it runs the LeanCTX instance
/// in a new process group, ends that group when the LeanCTX instance exits, when this
/// SDK ends the starter or when this process is gone, and exits only
/// afterwards.
/// </para>
/// <para>
/// <see cref="Unavailable"/> is reduced containment: the LeanCTX instance binary has no
/// such mode, or the platform is Windows. Then only what the runtime can still
/// reach as the tree of a live LeanCTX instance is ended; what a LeanCTX instance that exited
/// first left behind is not reachable.
/// </para>
/// </summary>
public enum SessionContainment
{
    InstanceStarter,
    Unavailable,

    /// <summary>Deprecated name of <see cref="InstanceStarter"/> (same value); removed in 2.0.</summary>
    [Obsolete("Use InstanceStarter; removed in 2.0")]
    EngineStarter = InstanceStarter,
}

internal static class SessionContainmentProtocol
{
    private const string RequestVariable = "LEAN_CTX_SESSION_CONTAINMENT";

    /// <summary>The starter's first output line, written before the LeanCTX instance exists.</summary>
    private static readonly byte[] Ack =
        Encoding.UTF8.GetBytes("{\"leanctx_session_containment\":1}");

    /// <summary>
    /// Asks the LeanCTX instance binary to contain its session: on POSIX it runs as its
    /// own session starter, on Windows it joins a kill-on-close job. A LeanCTX instance
    /// without the mode ignores the variable and acknowledges nothing.
    /// </summary>
    internal static void Request(ProcessStartInfo startInfo)
    {
        startInfo.Environment[RequestVariable] = "1";
    }

    /// <summary>Whether <paramref name="line"/> (without its newline) is the acknowledgement.</summary>
    internal static bool IsAck(ReadOnlySpan<byte> line) => line.SequenceEqual(Ack);

    /// <summary>Length of the acknowledgement at the start of the output, or 0.</summary>
    internal static int AckPrefixLength(ReadOnlySpan<byte> output) =>
        output.Length > Ack.Length
        && output[Ack.Length] == (byte)'\n'
        && output[..Ack.Length].SequenceEqual(Ack)
            ? Ack.Length + 1
            : 0;
}
