using System.Text;
using Thinkery.LeanCtx;

/// <summary>
/// Session containment (Q13): what the SDK asks of the Engine binary and what
/// it reports. Convention of <c>AgentTerminalConditionsTests</c>: a static
/// class with <c>Run()</c>, registered in Program.cs; the Engine is a POSIX
/// shell stand-in. Nothing here starts a LeanCTX instance except the last case,
/// which needs LEANCTX_INSTANCE_BIN.
/// </summary>
internal static class SessionContainmentTests
{
    private const string Ack = "{\"leanctx_session_containment\":1}";
    private const string Hello =
        "{\"agent_tools_interface_version\":\"1.0.0\",\"allow_exec\":false,\"allow_write\":false," +
        "\"capabilities\":[\"ctx_compose\",\"ctx_glob\",\"ctx_read\",\"ctx_search\",\"ctx_symbol\",\"ctx_tree\"]," +
        "\"engine_version\":\"3.11.0\",\"schema_version\":1,\"transport_version\":1}";

    public static void Run()
    {
        OutputWithoutAcknowledgementIsLeftAlone();
        AcknowledgementIsStrippedAndThePayloadIsIdentical();
        if (OperatingSystem.IsWindows())
        {
            Console.WriteLine("SKIP session-containment process cases (POSIX shell stand-in; requested on POSIX only)");
            return;
        }
        AgentReportsWhatTheFirstLineSays(acknowledge: false);
        AgentReportsWhatTheFirstLineSays(acknowledge: true);
        OneShotSuccessfulRunStripsAndRecordsTheAcknowledgement();
        OneShotFailedRunRecordsWhatTheFirstLineSaid(acknowledge: true);
        OneShotFailedRunRecordsWhatTheFirstLineSaid(acknowledge: false);
        RealEngineSessionStarterAcknowledges();
    }

    private const string ViewText = "fresh synthetic view\n";

    private static string Digest(string value) =>
        "sha256:" + Convert.ToHexString(
            System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    /// <summary>
    /// A complete, valid context-view response with the synthetic values the
    /// other bindings use for the same case (Rust <c>engine_response</c>, JVM
    /// <c>serializationFixtureView</c>).
    /// </summary>
    private static string SucceedingResponse()
    {
        var outputDigest = Digest(ViewText);
        var outputRef = "output:" + outputDigest["sha256:".Length..];
        var sourceDigest = Digest("fresh synthetic source\n");
        var sourceRef = "source:synthetic-path-sha256:" + new string('a', 64);
        var inputRef = "input:synthetic-request-sha256:" + new string('b', 64);
        var refs = "[\"" + inputRef + "\",\"" + sourceRef + "\"]";
        return "{\"schema_version\":1,\"transport_version\":1,\"engine_interface_version\":\"1.0.0\""
            + ",\"view\":{\"text\":\"fresh synthetic view\\n\",\"output_ref\":\"" + outputRef
            + "\",\"output_digest\":\"" + outputDigest + "\"}"
            + ",\"invocation\":{\"schema_version\":1,\"invocation_id\":\"engine-invocation-synthetic\""
            + ",\"engine\":{\"engine_id\":\"lean-ctx-local\",\"engine_version\":\"3.9.20\"}"
            + ",\"operation\":{\"capability_id\":\"capability://leanctx/context-optimization\""
            + ",\"capability_version\":\"1.0.0\"}"
            + ",\"input_ref\":\"" + inputRef + "\",\"input_digest\":\"sha256:" + new string('c', 64) + "\""
            + ",\"source_refs\":" + refs
            + ",\"policy_admission\":{\"policy_ref\":\"policy:synthetic\",\"decision\":\"admitted\"}}"
            + ",\"observation\":{\"schema_version\":1,\"invocation_id\":\"engine-invocation-synthetic\""
            + ",\"status\":\"succeeded\",\"output_ref\":\"" + outputRef
            + "\",\"output_digest\":\"" + outputDigest + "\""
            + ",\"source_lineage\":" + refs
            + ",\"measurements\":[{\"name\":\"input_tokens\",\"unit\":\"token\",\"classification\":\"measured\",\"value\":1}"
            + ",{\"name\":\"output_tokens\",\"unit\":\"token\",\"classification\":\"measured\",\"value\":2}]"
            + ",\"failure\":null"
            + ",\"receipt_link\":{\"schema_version\":1,\"receipt_id\":\"engine-receipt-synthetic\""
            + ",\"receipt_ref\":\"receipt:sha256:" + new string('d', 64) + "\""
            + ",\"receipt_digest\":\"sha256:" + new string('d', 64) + "\""
            + ",\"invocation_id\":\"engine-invocation-synthetic\"}}"
            + ",\"recovery\":{\"recovery_ref\":\"" + inputRef + "\",\"source_ref\":\"" + sourceRef
            + "\",\"source_digest\":\"" + sourceDigest + "\"}}";
    }

    private static ContextPlan Plan(string root)
    {
        File.WriteAllText(Path.Combine(root, "README.md"), "fixture\n", new UTF8Encoding(false));
        return new ContextPlan("fixture-session", "fixture-task", "inspect", new ContextSource("README.md", root));
    }

    /// <summary>Writes the response from a file, so no JSON passes through shell quoting.</summary>
    private static string SucceedingStandIn(string directory, bool acknowledge)
    {
        var response = Path.Combine(directory, "response.json");
        File.WriteAllText(response, SucceedingResponse(), new UTF8Encoding(false));
        return Script(directory, acknowledge ? "one-shot-ack.sh" : "one-shot-plain.sh", "#!/bin/sh\n"
            + (acknowledge ? "printf '%s\\n' '" + Ack + "'\n" : "")
            + "cat '" + response + "'\n");
    }

    /// <summary>
    /// B16: a successful one-shot run end to end. With and without the
    /// acknowledgement the client hands the same payload to the parser — the
    /// view verifies either way — and reports what the first line said.
    /// </summary>
    private static void OneShotSuccessfulRunStripsAndRecordsTheAcknowledgement()
    {
        var root = Scratch();
        try
        {
            var plan = Plan(root);

            var plain = new SubprocessInstanceClient(SucceedingStandIn(root, acknowledge: false), timeout: 10);
            var unacknowledged = plain.ContextView(plan);
            Require(unacknowledged.Text == ViewText, "the view text without an acknowledgement");
            Require(unacknowledged.Verify(), "the view verifies without an acknowledgement");
            Require(plain.LastSessionContainment == SessionContainment.Unavailable,
                "no acknowledgement: unavailable");

            var contained = new SubprocessInstanceClient(SucceedingStandIn(root, acknowledge: true), timeout: 10);
            Require(contained.LastSessionContainment == SessionContainment.Unavailable,
                "before any run: unavailable");
            var acknowledged = contained.ContextView(plan);
            Require(contained.LastSessionContainment == SessionContainment.InstanceStarter,
                "the acknowledgement is reported");
            Require(acknowledged.Text == ViewText, "the acknowledgement is not part of the payload");
            Require(acknowledged.Verify(), "the view verifies behind an acknowledgement");
            Require(acknowledged.OutputDigest == unacknowledged.OutputDigest, "the payload is identical");
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    /// <summary>B16: a failed one-shot run still records what the first line said.</summary>
    private static void OneShotFailedRunRecordsWhatTheFirstLineSaid(bool acknowledge)
    {
        var root = Scratch();
        try
        {
            var failing = Script(root, "one-shot-failing.sh", "#!/bin/sh\n"
                + (acknowledge ? "printf '%s\\n' '" + Ack + "'\n" : "")
                + "echo 'engine: nonzero_exit' >&2\n"
                + "exit 3\n");
            var client = new SubprocessInstanceClient(failing, timeout: 10);
            var plan = Plan(root);
            var failed = false;
            try { client.ContextView(plan); }
            catch (InstanceError) { failed = true; }
            Require(failed, "a non-zero exit is an Engine error");
            var expected = acknowledge ? SessionContainment.InstanceStarter : SessionContainment.Unavailable;
            Require(client.LastSessionContainment == expected,
                $"session containment is {client.LastSessionContainment}, expected {expected}");
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static void OutputWithoutAcknowledgementIsLeftAlone()
    {
        var payload = Encoding.UTF8.GetBytes("{\"payload\":\"é\",\"n\":1}\n");
        Require(SessionContainmentProtocol.AckPrefixLength(payload) == 0, "a payload is not an acknowledgement");
        Require(SessionContainmentProtocol.AckPrefixLength(Array.Empty<byte>()) == 0, "empty output");
        // The line alone, without its newline, is not a complete acknowledgement.
        Require(SessionContainmentProtocol.AckPrefixLength(Encoding.UTF8.GetBytes(Ack)) == 0, "line without newline");
        Require(!SessionContainmentProtocol.IsAck(payload), "a payload line is not the acknowledgement");
    }

    private static void AcknowledgementIsStrippedAndThePayloadIsIdentical()
    {
        var payload = Encoding.UTF8.GetBytes("{\"payload\":\"é\",\"n\":1}\n");
        var output = Encoding.UTF8.GetBytes(Ack + "\n").Concat(payload).ToArray();
        var prefix = SessionContainmentProtocol.AckPrefixLength(output);
        Require(prefix == Ack.Length + 1, "the acknowledgement and its newline are the prefix");
        Require(output[prefix..].SequenceEqual(payload), "the payload must be byte-identical");
        Require(SessionContainmentProtocol.IsAck(Encoding.UTF8.GetBytes(Ack)), "the line is the acknowledgement");
    }

    private static string Script(string directory, string name, string source)
    {
        var path = Path.Combine(directory, name);
        File.WriteAllText(path, source, new UTF8Encoding(false));
        // The stand-ins are shell scripts; `Run` returns before them on Windows.
        if (!OperatingSystem.IsWindows())
        {
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        }
        return path;
    }

    /// <summary>Answers the hello and one further request; optionally acknowledges first.</summary>
    private static string AgentStandIn(string directory, bool acknowledge) =>
        Script(directory, "stand-in-engine.sh", "#!/bin/sh\n"
            + (acknowledge ? "printf '%s\\n' '" + Ack + "'\n" : "")
            + "IFS= read -r line\n"
            + "printf '%s\\n' '{\"id\":\"1\",\"ok\":true,\"result\":" + Hello + "}'\n"
            + "IFS= read -r line\n"
            + "printf '%s\\n' '{\"id\":\"2\",\"ok\":true,\"result\":{}}'\n");

    private static string Scratch()
    {
        var directory = Path.Combine(Path.GetTempPath(), "leanctx-containment-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        // The SDK canonicalizes the project root; hand it the real path.
        return new DirectoryInfo(directory).ResolveLinkTarget(true)?.FullName ?? directory;
    }

    private static void Check(string binary, string root, SessionContainment expected)
    {
        var context = AgentContext.Open(root, engineBinary: binary, timeout: 10);
        try
        {
            Require(context.SessionContainment == expected,
                $"session containment is {context.SessionContainment}, expected {expected}");
            // The hello was accepted unchanged, with or without an acknowledgement before it.
            Require(context.Capabilities.Count == 6, "the hello must be accepted");
        }
        finally
        {
            // The subject is the reported state, not the shutdown.
            try { context.Cancel(); } catch (InstanceError) { }
        }
    }

    private static void AgentReportsWhatTheFirstLineSays(bool acknowledge)
    {
        var root = Scratch();
        try
        {
            Check(AgentStandIn(root, acknowledge), root,
                acknowledge ? SessionContainment.InstanceStarter : SessionContainment.Unavailable);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    /// <summary>Real Engine; skipped without it, as engine-v1-optional in Program.cs.</summary>
    private static void RealEngineSessionStarterAcknowledges()
    {
        var engine = Program.TestEnvironment("LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN");
        if (string.IsNullOrEmpty(engine))
        {
            Console.WriteLine("SKIP session-containment real Engine (LEANCTX_INSTANCE_BIN unavailable)");
            return;
        }
        var root = Scratch();
        try
        {
            var standIn = AgentStandIn(root, acknowledge: false);
            // The stand-in is started through the real binary's session starter.
            var wrapper = Script(root, "contained-engine.sh", "#!/bin/sh\n"
                + "exec '" + Path.GetFullPath(engine) + "' __session-starter --ack --watch-parent -- '"
                + standIn + "' \"$@\"\n");
            Check(wrapper, root, SessionContainment.InstanceStarter);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }
}
