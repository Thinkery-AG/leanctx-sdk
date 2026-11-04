using Thinkery.LeanCtx;

#pragma warning disable CS0618 // These tests prove that the deprecated Engine names keep working until 2.0.

/// <summary>SDK 1.3: every deprecated Engine name still compiles, catches and configures.</summary>
internal static class DeprecatedNamesTests
{
    public static void Run()
    {
        ErrorsAreCatchableByBothNames();
        OldClientsAndOptions();
        OldEnvironmentVariableIsAFallback();
        OldEnumNames();
    }

    private static void ErrorsAreCatchableByBothNames()
    {
        var root = Directory.CreateTempSubdirectory("leanctx-deprecated-").FullName;
        File.WriteAllText(Path.Combine(root, "README.md"), "test\n");
        var missing = Path.Combine(root, "missing-lean-ctx");
        var client = new SubprocessInstanceClient(missing, timeout: 5);
        var plan = new ContextPlan("s", "t", "inspect", new ContextSource("README.md", root));
        Exception? caught = null;
        try { client.ContextView(plan); }
        catch (EngineUnavailable error) { caught = error; }
        Require(caught is InstanceUnavailable and EngineError and InstanceError, "old catch type");
        caught = null;
        try { client.ContextView(plan); }
        catch (InstanceUnavailable error) { caught = error; }
        Require(caught is not null, "new catch type");
        Require(((SDKError)caught!).Code == "engine_unavailable", "error code is a contract value");

        Require(new EngineProtocolError("x") is InstanceProtocolError, "protocol");
        Require(new CompatibilityError("x") is InstanceProtocolError and EngineProtocolError, "compatibility");
        Require(new UnsupportedEngineError("x") is UnsupportedInstanceError, "unsupported");
        Require(new EngineTimeout("x") is InstanceTimeout, "timeout");
        Require(new EngineCrashed("x") is InstanceCrashed, "crashed");
        Require(new EngineRejected("x") is InstanceRejected, "rejected");
        Require(new PolicyAdmissionError("x") is InstanceRejected and EngineRejected, "policy");
        Require(new SourceUnavailableError("x") is InstanceExecutionError and EngineExecutionError, "source");
        Require(new AgentPermissionError("x") is InstanceError and EngineError, "permission");
    }

    private static void OldClientsAndOptions()
    {
        EngineClient legacy = new LegacyClient();
        InstanceClient asInstance = legacy;
        Require(asInstance is EngineClient, "old interface");

        var old = new SubprocessEngineClient(engineBinary: "/opt/leanctx/bin/lean-ctx");
        SubprocessInstanceClient asNew = old;
        Require(old.EngineBinary == "/opt/leanctx/bin/lean-ctx", "old option property");
        Require(asNew.InstanceBinary == old.EngineBinary, "option forwards");
        Require(new SubprocessInstanceClient(engineBinary: "/old-arg").InstanceBinary == "/old-arg", "old named argument");

        var session = new ContextSession("task", engine: old);
        Require(ReferenceEquals(session.Engine, session.Instance), "session alias");

        InstancePlanningRequest request = new EnginePlanningRequest("task-id", "query", 16);
        Require(request.BudgetTokens == 16, "old planning request");
        using EnterpriseInstanceClient enterprise = new EnterpriseEngineClient(
            "https://leanctx.example.com", "token", "11111111-1111-4111-8111-111111111111");
        Require(enterprise.Timeout == 30, "old enterprise client");
        Require(PresenterCapability.MinInstanceVersion == "3.11.0", "min version");
    }

    private static void OldEnvironmentVariableIsAFallback()
    {
        var savedNew = Environment.GetEnvironmentVariable("LEANCTX_INSTANCE_BIN");
        var savedOld = Environment.GetEnvironmentVariable("LEANCTX_ENGINE_BIN");
        try
        {
            Environment.SetEnvironmentVariable("LEANCTX_INSTANCE_BIN", null);
            Environment.SetEnvironmentVariable("LEANCTX_ENGINE_BIN", "/old/lean-ctx");
            Require(new SubprocessInstanceClient().InstanceBinary == "/old/lean-ctx", "old env fallback");
            Environment.SetEnvironmentVariable("LEANCTX_INSTANCE_BIN", "/new/lean-ctx");
            Require(new SubprocessInstanceClient().InstanceBinary == "/new/lean-ctx", "new env wins");
            Require(new SubprocessInstanceClient("/explicit").InstanceBinary == "/explicit", "explicit wins");
        }
        finally
        {
            Environment.SetEnvironmentVariable("LEANCTX_INSTANCE_BIN", savedNew);
            Environment.SetEnvironmentVariable("LEANCTX_ENGINE_BIN", savedOld);
        }
    }

    private static void OldEnumNames()
    {
        InstanceStatus status = EngineStatus.Degraded;
        Require(status == InstanceStatus.Degraded, "status alias");
        Require(status is EngineStatus.Succeeded or EngineStatus.Degraded, "status pattern");
        Require(SessionContainment.EngineStarter == SessionContainment.InstanceStarter, "containment alias");
    }

    private sealed class LegacyClient : EngineClient
    {
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception("deprecated names: " + message);
    }
}
