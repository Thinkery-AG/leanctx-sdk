namespace Thinkery.LeanCtx;

/// <summary>
/// A bearer secret the LeanCTX instance returns once to the host that presented sources.
/// Whoever later shows it is treated as THE HOLDER OF THAT CAPABILITY for those
/// presentations. It is not an agent name, not an attested identity and not an
/// authorization: current source rights are checked again at every use.
/// </summary>
/// <remarks>
/// Host-internal: never put it in model context, a tool argument, a tool result, a log
/// or a receipt, and never send it to a provider. <see cref="ToString"/> is redacted, the
/// type has no public state for a serializer to read, and equality is identity;
/// <see cref="Reveal"/> is the one deliberate way to the secret.
/// </remarks>
[System.Diagnostics.DebuggerDisplay("PresenterCapability(<redacted>)")]
public sealed class PresenterCapability
{
    /// <summary>First LeanCTX instance release that serves the v2 materialization contract.</summary>
    public const string MinInstanceVersion = "3.11.0";

    /// <summary>Longest presenter lifetime a host may ask for: one day.</summary>
    public const int MaxTtlSeconds = 24 * 60 * 60;

    /// <summary>Request header that carries a capability; never a body member.</summary>
    public const string Header = "x-leanctx-presenter";

    [System.Diagnostics.DebuggerBrowsable(System.Diagnostics.DebuggerBrowsableState.Never)]
    private readonly string _secret;

    /// <summary>Wrap a capability a host kept in its own secret store.</summary>
    public PresenterCapability(string secret)
    {
        if (!IsLowerHex(secret, "lcp1_", 64))
            throw new ValidationError("presenter capability is malformed");
        _secret = secret;
    }

    /// <summary>Return the secret. Never log, print or forward the result.</summary>
    public string Reveal() => _secret;

    /// <inheritdoc />
    public override string ToString() => "PresenterCapability(<redacted>)";

    internal static bool IsLowerHex(string? text, string prefix, int digits)
    {
        if (text is null || text.Length != prefix.Length + digits ||
            !text.StartsWith(prefix, StringComparison.Ordinal))
            return false;
        for (var index = prefix.Length; index < text.Length; index++)
        {
            var character = text[index];
            if (character is not ((>= '0' and <= '9') or (>= 'a' and <= 'f')))
                return false;
        }
        return true;
    }
}

/// <summary>
/// The presenter entry of a v2 materialization. <see cref="PresenterRef"/> is content-free
/// and safe in records; <see cref="Capability"/> is set only when the call issued one.
/// </summary>
public class InstancePresenterGrant
{
    internal InstancePresenterGrant(string presenterRef, ulong expiresAtUnix, PresenterCapability? capability)
    {
        PresenterRef = presenterRef;
        ExpiresAtUnix = expiresAtUnix;
        Capability = capability;
    }

    public string PresenterRef { get; }

    public ulong ExpiresAtUnix { get; }

    /// <summary>
    /// The capability, when this call issued one. Excluded from System.Text.Json output.
    /// </summary>
    [System.Text.Json.Serialization.JsonIgnore]
    public PresenterCapability? Capability { get; }

    /// <inheritdoc />
    public override string ToString() => $"InstancePresenterGrant({PresenterRef})";
}

/// <summary>
/// A validated tenant-bound v2 materialization: the unchanged v1 result and the presenter
/// grant. It does not establish model transmission or execution proof.
/// </summary>
public class InstanceSourceMaterializationV2
{
    internal InstanceSourceMaterializationV2(
        IReadOnlyDictionary<string, object?> materialization,
        InstancePresenterGrant presenter)
    {
        Materialization = materialization;
        Presenter = presenter;
    }

    /// <summary>Always 2.</summary>
    public int SchemaVersion => 2;

    /// <summary>The validated v1 result, exactly as <c>ContextMaterialize</c> returns it.</summary>
    public IReadOnlyDictionary<string, object?> Materialization { get; }

    public InstancePresenterGrant Presenter { get; }
}

/// <summary>
/// A validated, tenant-bound answer to a use of text that was materialized under a
/// presenter capability. When <see cref="Usable"/> is false, <see cref="Reason"/> is a
/// content-free code and every other member is empty.
/// </summary>
public sealed class PresentedUseResult
{
    internal PresentedUseResult(
        string presenterRef,
        bool usable,
        string? reason,
        IReadOnlyList<string> sourceIds,
        string? recoveryId,
        string? content)
    {
        PresenterRef = presenterRef;
        Usable = usable;
        Reason = reason;
        SourceIds = sourceIds;
        RecoveryId = recoveryId;
        Content = content;
    }

    /// <summary>Content-free reference of the presenter entry. Safe in records.</summary>
    public string PresenterRef { get; }

    /// <summary>Whether this holder may use the text now.</summary>
    public bool Usable { get; }

    /// <summary>A content-free refusal code when not usable.</summary>
    public string? Reason { get; }

    /// <summary>The governed sources the text derives from.</summary>
    public IReadOnlyList<string> SourceIds { get; }

    /// <summary>The id of the recovery copy, for retain and expand.</summary>
    public string? RecoveryId { get; }

    /// <summary>The kept text, for expand only.</summary>
    public string? Content { get; }
}

/// <summary>The validated answer to a presenter revocation.</summary>
public sealed class PresenterRevocation
{
    internal PresenterRevocation(string presenterRef, bool revoked)
    {
        PresenterRef = presenterRef;
        Revoked = revoked;
    }

    /// <summary>Content-free reference of the presenter entry. Safe in records.</summary>
    public string PresenterRef { get; }

    /// <summary>False when the LeanCTX instance had no such presenter on record.</summary>
    public bool Revoked { get; }
}
