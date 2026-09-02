namespace Thinkery.LeanCtx;

/// <summary>Host lifecycle coordinator for the five Product primitives.</summary>
public sealed class ContextSession : IDisposable
{
    private readonly List<string> degradations = new();
    private SessionState state = SessionState.Created;
    private ContextPlan? plan;
    private ContextView? view;
    private ContextReceipt? receipt;
    private bool prepared;

    public ContextSession(
        string task,
        string? projectRoot = null,
        string? sessionId = null,
        string? taskId = null,
        bool failOpen = false,
        EngineClient? engine = null)
    {
        Task = WireJson.Text(task, "task", WireJson.MaxTaskBytes, controls: false);
        ProjectRoot = projectRoot;
        SessionId = sessionId ?? RuntimeId("session");
        TaskId = taskId ?? RuntimeId("task");
        FailOpen = failOpen;
        Engine = engine ?? new SubprocessEngineClient();
    }

    public string Task { get; }
    public string? ProjectRoot { get; }
    public string SessionId { get; }
    public string TaskId { get; }
    public bool FailOpen { get; }
    public EngineClient Engine { get; }
    public SessionState State => state;
    public ContextPlan? CurrentPlan => plan;
    public ContextPlan? current_plan => CurrentPlan;
    public ContextView? View => view;
    public ContextReceipt? Receipt => receipt;
    public IReadOnlyList<string> Degradations => degradations.AsReadOnly();
    public string project_root => ProjectRoot ?? string.Empty;
    public string session_id => SessionId;
    public string task_id => TaskId;
    public string task => Task;
    public string state_text => ProtocolText.SessionStateText(State);

    public ContextPlan Plan(
        ContextSource source,
        string mode = "aggressive",
        string freshness = "reuse")
    {
        EnsureNotTerminal();
        var candidate = new ContextPlan(SessionId, TaskId, Task, source, mode, freshness);
        if (plan is not null)
        {
            if (plan.PlanId != candidate.PlanId)
                throw new SessionStateError("a session cannot replace its Product intent");
            return plan;
        }
        plan = candidate;
        state = SessionState.Planned;
        return candidate;
    }

    public ContextPlan PlanFor(
        ContextSource source,
        string mode = "aggressive",
        string freshness = "reuse") => Plan(source, mode, freshness);

    public ContextPlan Plan(
        ContextSource source,
        string mode,
        Freshness freshness) => Plan(source, mode,
            freshness == Thinkery.LeanCtx.Freshness.Reuse ? "reuse" : "refresh");

    public ContextPlan PlanIntent(
        ContextSource source,
        string mode = "aggressive",
        string freshness = "reuse") => Plan(source, mode, freshness);

    public ContextView? Prepare(
        ContextSource? source = null,
        string mode = "aggressive",
        string freshness = "reuse",
        CancellationToken cancellationToken = default) =>
        PrepareAsync(source, mode, freshness, cancellationToken).GetAwaiter().GetResult();

    public async Task<ContextView?> PrepareAsync(
        ContextSource? source = null,
        string mode = "aggressive",
        string freshness = "reuse",
        CancellationToken cancellationToken = default)
    {
        if (state is SessionState.Completed or SessionState.Aborted or SessionState.Closed)
            throw new SessionStateError("prepare is not legal after terminal completion");
        if (prepared)
            return view;
        if (plan is null)
        {
            if (source is null)
                throw new SessionStateError("prepare requires a source before planning");
            Plan(source, mode, freshness);
        }
        else if (source is not null)
        {
            Plan(source, mode, freshness);
        }
        if (plan is null)
            throw new SessionStateError("prepare could not establish a plan");
        state = SessionState.Executing;
        try
        {
            view = await Engine.ContextViewAsync(plan, cancellationToken).ConfigureAwait(false);
            prepared = true;
            if (view.Status == EngineStatus.Degraded)
                AddDegradation("engine:degraded");
            return view;
        }
        catch (EngineUnavailable error) when (FailOpen)
        {
            AddDegradation($"engine:{error.Code}");
            prepared = true;
            view = null;
            return null;
        }
        catch (EngineTimeout error) when (FailOpen)
        {
            AddDegradation($"engine:{error.Code}");
            prepared = true;
            view = null;
            return null;
        }
        catch (Exception error)
        {
            AbortEngineFailure(error);
            throw;
        }
    }

    public ContextReceipt Complete(
        object? hostResult = null,
        HostOutcome outcome = HostOutcome.Unknown,
        IReadOnlyDictionary<string, object?>? usage = null)
    {
        if (state == SessionState.Completed)
        {
            if (receipt is null || receipt.Outcome != outcome ||
                !SameUsage(receipt.Usage, usage))
                throw new SessionStateError("conflicting repeated complete");
            return receipt;
        }
        if (state is SessionState.Aborted or SessionState.Closed)
            throw new SessionStateError("complete is not legal after abort/close");
        if (state != SessionState.Executing)
            throw new SessionStateError("complete requires an executing session");
        if (outcome == HostOutcome.Aborted)
            throw new ValidationError("complete outcome must be an explicit non-aborted host outcome");
        receipt = MakeReceipt(outcome, hostResult, usage, null);
        state = SessionState.Completed;
        return receipt;
    }

    public ContextReceipt Complete(
        object? hostResult,
        string outcome,
        IReadOnlyDictionary<string, object?>? usage = null)
    {
        if (!ProtocolText.TryHostOutcome(outcome, out var parsed))
            throw new ValidationError("invalid host outcome");
        return Complete(hostResult, parsed, usage);
    }

    public ContextReceipt Abort(Exception error)
    {
        if (error is null)
            throw new ValidationError("abort requires an Error");
        if (state == SessionState.Aborted)
        {
            if (receipt is null)
                throw new SessionStateError("aborted session has no receipt");
            return receipt;
        }
        if (state == SessionState.Closed)
        {
            if (receipt is not null && receipt.Outcome == HostOutcome.Aborted)
                return receipt;
            throw new SessionStateError("closed session has no abort receipt");
        }
        if (state == SessionState.Completed)
            throw new SessionStateError("cannot abort a completed session");
        receipt = MakeReceipt(HostOutcome.Aborted, null, null, error);
        state = SessionState.Aborted;
        return receipt;
    }

    public RecoveredSource Recover(
        ContextView? sourceView = null,
        CancellationToken cancellationToken = default) =>
        RecoverAsync(sourceView, cancellationToken).GetAwaiter().GetResult();

    public async Task<RecoveredSource> RecoverAsync(
        ContextView? sourceView = null,
        CancellationToken cancellationToken = default)
    {
        if (state is not (SessionState.Executing or SessionState.Completed or SessionState.Aborted))
            throw new SessionStateError("recover requires an executing or terminal session");
        var selected = sourceView ?? view;
        if (selected is null || selected.RecoveryRef is null || plan is null)
            throw new RecoveryUnavailableError("session has no exact recovery binding", selected);
        if (sourceView is not null && view is not null && sourceView != view &&
            (sourceView.RecoveryRef != view.RecoveryRef || sourceView.SourceRef != view.SourceRef ||
             sourceView.SourceDigest != view.SourceDigest))
            throw new RecoveryUnavailableError("recovery view is not bound to this session", selected);
        return await Engine.RecoverAsync(
            plan.Source.ProjectRoot,
            selected.Source.RelativePath,
            selected.RecoveryRef,
            selected.SourceRef,
            selected.SourceDigest,
            cancellationToken).ConfigureAwait(false);
    }

    public void Close()
    {
        if (state == SessionState.Closed)
            return;
        if (state is not (SessionState.Completed or SessionState.Aborted) || receipt is null)
            throw new SessionStateError("close requires a terminal receipt");
        state = SessionState.Closed;
    }

    public void Dispose() => Close();

    private ContextReceipt MakeReceipt(
        HostOutcome outcome,
        object? hostResult,
        IReadOnlyDictionary<string, object?>? usage,
        Exception? hostException)
    {
        var integrity = view?.IntegrityStatus ?? Integrity.Unsealed;
        return new ContextReceipt(
            SessionId,
            TaskId,
            plan?.PlanId,
            view,
            outcome,
            integrity,
            degradations,
            usage,
            hostException?.GetType().Name,
            hostResult,
            hostException);
    }

    private void AbortEngineFailure(Exception error)
    {
        if (state is SessionState.Completed or SessionState.Aborted or SessionState.Closed)
            return;
        if (error is EngineExecutionError execution && execution.View is not null)
            view = execution.View;
        if (error is SDKError sdkError)
            AddDegradation($"engine:{sdkError.Code}");
        try { receipt = MakeReceipt(HostOutcome.Aborted, null, null, null); }
        catch (ValidationError) { receipt = null; }
        state = SessionState.Aborted;
    }

    private void EnsureNotTerminal()
    {
        if (state is SessionState.Completed or SessionState.Aborted or SessionState.Closed)
            throw new SessionStateError("session is terminal");
    }

    private void AddDegradation(string value)
    {
        if (!degradations.Contains(value, StringComparer.Ordinal))
            degradations.Add(value);
    }

    private static bool SameUsage(
        IReadOnlyDictionary<string, object?>? left,
        IReadOnlyDictionary<string, object?>? right)
    {
        if (left is null || right is null)
            return left is null && right is null;
        return WireJson.CanonicalJson(left) == WireJson.CanonicalJson(right);
    }

    private static string RuntimeId(string prefix) =>
        $"{prefix}-{Guid.NewGuid():N}";
}
