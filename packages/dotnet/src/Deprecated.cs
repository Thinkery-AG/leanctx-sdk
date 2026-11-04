namespace Thinkery.LeanCtx;

// SDK 1.3 renamed the public "Engine" names to "Instance". .NET cannot alias a type, so
// each deprecated name below is a subtype of its Instance type; where the SDK creates the
// object it still creates the deprecated subtype until 2.0, so code that tests for or
// declares the old type keeps working. The error aliases live in Errors.cs.

/// <summary>Deprecated name of <see cref="SubprocessInstanceClient"/>; removed in 2.0.</summary>
[Obsolete("Use SubprocessInstanceClient; removed in 2.0")]
public sealed class SubprocessEngineClient : SubprocessInstanceClient
{
    public SubprocessEngineClient(string? engineBinary = null, double timeout = 30)
        : base(engineBinary, timeout) { }
}

/// <summary>Deprecated name of <see cref="EnterpriseInstanceClient"/>; removed in 2.0.</summary>
[Obsolete("Use EnterpriseInstanceClient; removed in 2.0")]
public sealed class EnterpriseEngineClient : EnterpriseInstanceClient
{
    public EnterpriseEngineClient(
        string baseUrl,
        string credential,
        string tenantId,
        double timeout = 30,
        bool allowLoopbackHttp = false)
        : base(baseUrl, credential, tenantId, timeout, allowLoopbackHttp) { }
}

/// <summary>Deprecated name of <see cref="InstancePlanningRequest"/>; removed in 2.0.</summary>
[Obsolete("Use InstancePlanningRequest; removed in 2.0")]
public sealed class EnginePlanningRequest : InstancePlanningRequest
{
    public EnginePlanningRequest(string taskId, string query, long budgetTokens, int maxCandidates = 64)
        : base(taskId, query, budgetTokens, maxCandidates) { }
}

/// <summary>Deprecated name of <see cref="InstancePresenterGrant"/>; the type the SDK returns until 2.0.</summary>
[Obsolete("Use InstancePresenterGrant; removed in 2.0")]
public sealed class EnginePresenterGrant : InstancePresenterGrant
{
    internal EnginePresenterGrant(string presenterRef, ulong expiresAtUnix, PresenterCapability? capability)
        : base(presenterRef, expiresAtUnix, capability) { }
}

/// <summary>Deprecated name of <see cref="InstanceSourceMaterializationV2"/>; the type the SDK returns until 2.0.</summary>
[Obsolete("Use InstanceSourceMaterializationV2; removed in 2.0")]
public sealed class EngineSourceMaterializationV2 : InstanceSourceMaterializationV2
{
    internal EngineSourceMaterializationV2(
        IReadOnlyDictionary<string, object?> materialization,
        InstancePresenterGrant presenter)
        : base(materialization, presenter) { }
}

/// <summary>
/// Deprecated name of <see cref="InstanceStatus"/>; removed in 2.0. Its members are
/// <see cref="InstanceStatus"/> constants, so <c>view.Status == EngineStatus.Degraded</c>
/// and patterns such as <c>EngineStatus.Succeeded or EngineStatus.Degraded</c> still compile.
/// </summary>
[Obsolete("Use InstanceStatus; removed in 2.0")]
public static class EngineStatus
{
    public const InstanceStatus Succeeded = InstanceStatus.Succeeded;
    public const InstanceStatus Degraded = InstanceStatus.Degraded;
    public const InstanceStatus Rejected = InstanceStatus.Rejected;
    public const InstanceStatus Failed = InstanceStatus.Failed;
}
