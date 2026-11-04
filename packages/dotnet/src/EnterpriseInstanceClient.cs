// Until 2.0 the SDK throws the deprecated Engine error subtypes so that both the
// Engine and the Instance names catch them (see Errors.cs).
#pragma warning disable CS0618
using System.Net;
using System.Net.Http.Headers;

namespace Thinkery.LeanCtx;

/// <summary>
/// Bounded authenticated client for tenant-bound source planning and context materialization.
/// These operations prepare context and do not establish model transmission or execution proof.
/// </summary>
public class EnterpriseInstanceClient : IDisposable
{
    private const string ContextPlanPath = "/v1/engine/context-plan";
    private const string ContextMaterializePath = "/v1/engine/context-materialize";
    private const string ContextMaterializeV2Path = "/v1/engine/context-materialize-v2";
    private const int MaxCredentialBytes = 4096;
    private const int MaxUrlBytes = 4096;

    private readonly Uri _baseUri;
    private readonly string _credential;
    private readonly HttpClient _httpClient;
    private bool _disposed;

    public EnterpriseInstanceClient(
        string baseUrl,
        string credential,
        string tenantId,
        double timeout = 30,
        bool allowLoopbackHttp = false)
    {
        _baseUri = ValidateBaseUrl(baseUrl, allowLoopbackHttp);
        _credential = ValidateCredential(credential);
        if (!double.IsFinite(timeout) || timeout < 0.1 || timeout > 120)
            throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
        Timeout = timeout;
        TenantId = InstancePlanningWire.ConfiguredTenant(tenantId);

        var handler = new HttpClientHandler
        {
            AllowAutoRedirect = false,
            AutomaticDecompression = DecompressionMethods.None,
            UseCookies = false,
            UseProxy = false,
            UseDefaultCredentials = false,
        };
        // The platform handler retains its normal certificate-chain and hostname checks.
        _httpClient = new HttpClient(handler, disposeHandler: true)
        {
            Timeout = System.Threading.Timeout.InfiniteTimeSpan,
        };
    }

    /// <summary>The normalized tenant bound to every response.</summary>
    public string TenantId { get; }

    /// <summary>The full request deadline in seconds.</summary>
    public double Timeout { get; }

    /// <summary>Request a source plan for authorized source identifiers.</summary>
    public IReadOnlyDictionary<string, object?> ContextPlan(
        InstancePlanningRequest request,
        IReadOnlyList<string> sourceIds) =>
        ContextPlanAsync(request, sourceIds).GetAwaiter().GetResult();

    /// <summary>Request a source plan asynchronously for authorized source identifiers.</summary>
    public async Task<IReadOnlyDictionary<string, object?>> ContextPlanAsync(
        InstancePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        CancellationToken cancellationToken = default)
    {
        EnsureNotDisposed();
        var checkedRequest = RequireRequest(request, "context_plan");
        var checkedSourceIds = InstancePlanningWire.SourceIds(sourceIds);
        var payload = WireJson.CanonicalBytes(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["planning"] = checkedRequest.ToDictionary(),
            ["source_ids"] = checkedSourceIds,
        });
        if (payload.Length > InstancePlanningWire.MaxRequestBytes)
            throw new ValidationError("Enterprise instance request exceeds its byte bound");

        var raw = await PostJsonAsync(
            ContextPlanPath, payload, InstancePlanningWire.MaxPlanResponseBytes,
            "Enterprise instance", "planning request", cancellationToken).ConfigureAwait(false);
        var normalized = InstancePlanningWire.ParsePlanResponse(raw, checkedRequest, checkedSourceIds, TenantId);
        return (IReadOnlyDictionary<string, object?>)WireJson.DeepFreeze(normalized)!;
    }

    /// <summary>Materialize a plan bound to the observed governance revision and digest.</summary>
    public IReadOnlyDictionary<string, object?> ContextMaterialize(
        InstancePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest,
        string? planningEvaluationTime = null) =>
        ContextMaterializeAsync(request, sourceIds, expectedGovernanceRevision,
            expectedBindingDigest, planningEvaluationTime).GetAwaiter().GetResult();

    /// <summary>Materialize a plan asynchronously under the supplied governance binding.</summary>
    public async Task<IReadOnlyDictionary<string, object?>> ContextMaterializeAsync(
        InstancePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest,
        string? planningEvaluationTime = null,
        CancellationToken cancellationToken = default)
    {
        EnsureNotDisposed();
        var checkedRequest = RequireRequest(request, "context_materialize");
        var checkedSourceIds = InstancePlanningWire.SourceIds(sourceIds);
        var bindingDigest = InstancePlanningWire.ValidateBindingDigest(expectedBindingDigest);
        var payloadValue = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["planning"] = checkedRequest.ToDictionary(),
            ["source_ids"] = checkedSourceIds,
            ["expected_governance_revision"] = expectedGovernanceRevision,
            ["expected_binding_digest"] = bindingDigest,
        };
        if (planningEvaluationTime is not null)
            payloadValue["planning_evaluation_time"] =
                InstancePlanningWire.ValidateTimestamp(planningEvaluationTime, "planning_evaluation_time");
        var payload = WireJson.CanonicalBytes(payloadValue);
        if (payload.Length > InstancePlanningWire.MaxRequestBytes)
            throw new ValidationError("Enterprise instance materialization request exceeds its byte bound");

        var raw = await PostJsonAsync(
            ContextMaterializePath, payload, InstancePlanningWire.MaxMaterializationResponseBytes,
            "Enterprise instance materialization", "materialization request", cancellationToken)
            .ConfigureAwait(false);
        var normalized = InstancePlanningWire.ParseMaterializationResponse(
            raw, checkedRequest, checkedSourceIds, TenantId, expectedGovernanceRevision, bindingDigest);
        return (IReadOnlyDictionary<string, object?>)WireJson.DeepFreeze(normalized)!;
    }

    /// <summary>
    /// Materialize under a new presenter capability with the given lifetime (1 to 86400
    /// seconds). The capability is returned once, in <c>Presenter.Capability</c>.
    /// </summary>
    /// <remarks>
    /// A capability proves the holder of the capability and nothing else: the suite checks
    /// the API key, its tenant and the current source rights first and at every use, and the
    /// capability is usable only with the API key it was issued to. An
    /// <see cref="UnsupportedInstanceError"/> means the suite or its LeanCTX instance does not serve the
    /// v2 contract.
    /// </remarks>
    public Task<InstanceSourceMaterializationV2> ContextMaterializeV2Async(
        InstancePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest,
        int presenterTtlSeconds,
        string? planningEvaluationTime = null,
        CancellationToken cancellationToken = default)
    {
        if (presenterTtlSeconds < 1 || presenterTtlSeconds > PresenterCapability.MaxTtlSeconds)
            throw new ValidationError("presenter lifetime must be 1 to 86400 seconds");
        return MaterializeV2Async(
            request, sourceIds, expectedGovernanceRevision, expectedBindingDigest,
            new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["mode"] = "issue",
                ["ttl_seconds"] = (long)presenterTtlSeconds,
            },
            null, planningEvaluationTime, cancellationToken);
    }

    /// <summary>
    /// Materialize under a capability this host already holds. It travels in the
    /// <c>x-leanctx-presenter</c> request header and is never part of the body; the result
    /// carries no capability.
    /// </summary>
    public Task<InstanceSourceMaterializationV2> ContextMaterializeV2Async(
        InstancePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest,
        PresenterCapability presenter,
        string? planningEvaluationTime = null,
        CancellationToken cancellationToken = default)
    {
        if (presenter is null)
            throw new ValidationError("context_materialize_v2 requires a PresenterCapability");
        return MaterializeV2Async(
            request, sourceIds, expectedGovernanceRevision, expectedBindingDigest,
            new Dictionary<string, object?>(StringComparer.Ordinal) { ["mode"] = "renew" },
            presenter, planningEvaluationTime, cancellationToken);
    }

    private async Task<InstanceSourceMaterializationV2> MaterializeV2Async(
        InstancePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest,
        Dictionary<string, object?> mode,
        PresenterCapability? held,
        string? planningEvaluationTime,
        CancellationToken cancellationToken)
    {
        EnsureNotDisposed();
        var checkedRequest = RequireRequest(request, "context_materialize_v2");
        var checkedSourceIds = InstancePlanningWire.SourceIds(sourceIds);
        var bindingDigest = InstancePlanningWire.ValidateBindingDigest(expectedBindingDigest);
        var payloadValue = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["planning"] = checkedRequest.ToDictionary(),
            ["source_ids"] = checkedSourceIds,
            ["expected_governance_revision"] = expectedGovernanceRevision,
            ["expected_binding_digest"] = bindingDigest,
            ["presenter"] = mode,
        };
        if (planningEvaluationTime is not null)
            payloadValue["planning_evaluation_time"] =
                InstancePlanningWire.ValidateTimestamp(planningEvaluationTime, "planning_evaluation_time");
        var payload = WireJson.CanonicalBytes(payloadValue);
        if (payload.Length > InstancePlanningWire.MaxRequestBytes)
            throw new ValidationError("Enterprise instance materialization request exceeds its byte bound");

        var raw = await PostJsonAsync(
            ContextMaterializeV2Path, payload,
            InstancePlanningWire.MaxMaterializationResponseBytes + InstancePlanningWire.PresenterEnvelopeBytes,
            "Enterprise instance materialization", "materialization request", cancellationToken,
            held,
            "Enterprise suite does not serve context-materialize-v2: LeanCTX instance " +
            PresenterCapability.MinInstanceVersion + " or later is required")
            .ConfigureAwait(false);
        return InstancePlanningWire.ParseMaterializationV2Response(
            raw, checkedRequest, checkedSourceIds, TenantId, expectedGovernanceRevision, bindingDigest,
            issued: held is null);
    }

    private const string PresentedUsePath = "/v1/engine/presented-use";
    private const string PresenterRevokePath = "/v1/engine/presenter-revoke";
    private const string PresenterUnsupported =
        "Enterprise suite does not serve presenter capabilities: LeanCTX instance " +
        PresenterCapability.MinInstanceVersion + " or later is required";

    /// <summary>
    /// Use text that was materialized under <paramref name="presenter"/>.
    /// <paramref name="mode"/> is <c>check</c> (may the exact delivered text
    /// <paramref name="value"/> still be used by this holder now), <c>retain</c> (also keep
    /// a recovery copy; the answer carries its id) or <c>expand</c> (<paramref name="value"/>
    /// is a recovery id; the answer carries the kept text).
    /// </summary>
    /// <remarks>
    /// <paramref name="sourceIds"/> are the governed sources the text was materialized from:
    /// exactly those, or the use is refused. The capability travels in the
    /// <c>x-leanctx-presenter</c> request header, never in the body. The suite checks the API
    /// key, its tenant and its current rights to every source first and again before it
    /// answers. A refused capability is a <see cref="PolicyAdmissionError"/>; an
    /// <see cref="UnsupportedInstanceError"/> means the suite or its LeanCTX instance does not serve it.
    /// </remarks>
    public async Task<PresentedUseResult> PresentedUseAsync(
        PresenterCapability presenter,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string mode,
        string value,
        CancellationToken cancellationToken = default)
    {
        EnsureNotDisposed();
        if (presenter is null)
            throw new ValidationError("presented_use requires a PresenterCapability");
        var checkedSourceIds = InstancePlanningWire.SourceIds(sourceIds);
        var expanding = mode == "expand";
        var wellFormed = value is not null && (expanding
            ? PresenterCapability.IsLowerHex(value, "", 64)
            : mode is "check" or "retain" && value.Length > 0 &&
              System.Text.Encoding.UTF8.GetByteCount(value) <= InstancePlanningWire.MaxMaterializedContentBytes);
        if (checkedSourceIds.Count == 0 || !wellFormed)
            throw new ValidationError(
                "presented_use requires source ids, a mode and a bounded text or a recovery id");
        var payload = WireJson.CanonicalBytes(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1UL,
            ["source_ids"] = checkedSourceIds,
            ["expected_governance_revision"] = expectedGovernanceRevision,
            ["use"] = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["mode"] = mode,
                [expanding ? "recovery_id" : "content"] = value,
            },
        });
        var raw = await PostJsonAsync(
            PresentedUsePath, payload,
            InstancePlanningWire.MaxMaterializationResponseBytes + InstancePlanningWire.PresenterEnvelopeBytes,
            "Enterprise presented use", "presented use request", cancellationToken,
            presenter, PresenterUnsupported)
            .ConfigureAwait(false);
        return InstancePlanningWire.ParsePresentedUseResponse(
            raw, mode, value!, checkedSourceIds, TenantId, expectedGovernanceRevision, presenter);
    }

    /// <summary>
    /// End <paramref name="presenter"/>: from now on it presents and reuses nothing. Only
    /// the API key it was issued to can revoke it.
    /// </summary>
    public async Task<PresenterRevocation> RevokePresenterAsync(
        PresenterCapability presenter,
        CancellationToken cancellationToken = default)
    {
        EnsureNotDisposed();
        if (presenter is null)
            throw new ValidationError("presenter_revoke requires a PresenterCapability");
        var payload = WireJson.CanonicalBytes(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1UL,
        });
        var raw = await PostJsonAsync(
            PresenterRevokePath, payload, InstancePlanningWire.PresenterEnvelopeBytes,
            "Enterprise presenter revocation", "presenter revocation request", cancellationToken,
            presenter, PresenterUnsupported)
            .ConfigureAwait(false);
        return InstancePlanningWire.ParsePresenterRevocationResponse(raw, TenantId);
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        _httpClient.Dispose();
    }

    private async Task<byte[]> PostJsonAsync(
        string path,
        byte[] payload,
        int maximumResponseBytes,
        string operation,
        string rejection,
        CancellationToken cancellationToken,
        PresenterCapability? presenter = null,
        string? unsupported = null)
    {
        using var timeoutSource = new CancellationTokenSource(TimeSpan.FromSeconds(Timeout));
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken, timeoutSource.Token);
        var content = new ByteArrayContent(payload);
        content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
        using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(_baseUri, path))
        {
            Version = HttpVersion.Version11,
            VersionPolicy = HttpVersionPolicy.RequestVersionOrLower,
            Content = content,
        };
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.ConnectionClose = true;
        request.Headers.TryAddWithoutValidation("Authorization", "Bearer " + _credential);
        // A request header, like the credential: never a body member.
        if (presenter is not null)
            request.Headers.TryAddWithoutValidation(PresenterCapability.Header, presenter.Reveal());

        try
        {
            using var response = await _httpClient.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, linked.Token).ConfigureAwait(false);
            linked.Token.ThrowIfCancellationRequested();
            var status = (int)response.StatusCode;
            if (status is >= 300 and < 400)
                throw new EngineProtocolError($"{operation} redirects are not followed");
            // The route is absent (older suite) or its LeanCTX instance predates the operation.
            if (unsupported is not null && status is 404 or 405 or 501)
                throw new UnsupportedEngineError(unsupported);
            if (status == (int)HttpStatusCode.Unauthorized)
                throw new EngineRejected($"{operation} authentication was rejected");
            if (status == (int)HttpStatusCode.Forbidden)
                throw new PolicyAdmissionError($"{operation} policy rejected the request");
            if (status >= 500)
                throw new EngineUnavailable($"{operation} returned a server error");
            if (status < 200 || status >= 300)
                throw new EngineRejected($"{operation} rejected the {rejection}");

            var declaredLength = response.Content.Headers.ContentLength;
            if (declaredLength is < 0 || declaredLength > maximumResponseBytes)
                throw new EngineProtocolError($"{operation} response exceeds its byte bound");
            await using var stream = await response.Content.ReadAsStreamAsync(linked.Token).ConfigureAwait(false);
            var body = await ReadBoundedAsync(stream, maximumResponseBytes, linked.Token).ConfigureAwait(false);
            linked.Token.ThrowIfCancellationRequested();
            return body;
        }
        catch (InstanceRejected)
        {
            throw;
        }
        catch (InstanceProtocolError)
        {
            throw;
        }
        catch (InstanceUnavailable)
        {
            throw;
        }
        catch (OperationCanceledException error) when (
            timeoutSource.IsCancellationRequested && !cancellationToken.IsCancellationRequested)
        {
            throw new EngineTimeout($"{operation} request exceeded its deadline", error);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (HttpRequestException error) when (
            timeoutSource.IsCancellationRequested && !cancellationToken.IsCancellationRequested)
        {
            throw new EngineTimeout($"{operation} request exceeded its deadline", error);
        }
        catch (HttpRequestException error)
        {
            throw new EngineUnavailable($"{operation} connection failed", error);
        }
        catch (IOException error) when (
            timeoutSource.IsCancellationRequested && !cancellationToken.IsCancellationRequested)
        {
            throw new EngineTimeout($"{operation} request exceeded its deadline", error);
        }
        catch (IOException error)
        {
            throw new EngineUnavailable($"{operation} response could not be read", error);
        }
    }

    private static async Task<byte[]> ReadBoundedAsync(
        Stream stream,
        int maximumBytes,
        CancellationToken cancellationToken)
    {
        using var output = new MemoryStream();
        var buffer = new byte[8192];
        while (true)
        {
            var remaining = maximumBytes + 1 - (int)output.Length;
            var count = await stream.ReadAsync(
                buffer.AsMemory(0, Math.Min(buffer.Length, remaining)), cancellationToken)
                .ConfigureAwait(false);
            if (count == 0)
                return output.ToArray();
            if (output.Length + count > maximumBytes)
                throw new EngineProtocolError("Enterprise instance response exceeds its byte bound");
            output.Write(buffer, 0, count);
        }
    }

    private static InstancePlanningRequest RequireRequest(InstancePlanningRequest? request, string operation) =>
        request ?? throw new ValidationError($"{operation} requires InstancePlanningRequest");

    private void EnsureNotDisposed()
    {
        if (_disposed)
            throw new ConfigurationError("EnterpriseInstanceClient is disposed");
    }

    private static string ValidateCredential(string? value)
    {
        if (string.IsNullOrEmpty(value) || value.Length > MaxCredentialBytes ||
            value.Any(character => character < '!' || character > '~'))
            throw new ConfigurationError("credential must be a non-empty visible ASCII string");
        return value;
    }

    private static Uri ValidateBaseUrl(string? value, bool allowLoopbackHttp)
    {
        if (string.IsNullOrEmpty(value))
            throw new ConfigurationError("base_url must be a bounded absolute URL");
        byte[] encoded;
        try
        {
            encoded = WireJson.Utf8(value, "base_url");
        }
        catch (ValidationError error)
        {
            throw new ConfigurationError("base_url must be valid UTF-8", error);
        }
        if (encoded.Length > MaxUrlBytes || value.Any(character => character < 0x21))
            throw new ConfigurationError("base_url must be a bounded URL without whitespace or controls");
        if (value.Contains('?') || value.Contains('#') ||
            !Uri.TryCreate(value, UriKind.Absolute, out var parsed))
            throw new ConfigurationError("base_url must be an absolute URL with only an optional root path");
        if (!string.IsNullOrEmpty(parsed.UserInfo))
            throw new ConfigurationError("base_url must not contain userinfo");
        if (parsed.Scheme != Uri.UriSchemeHttps && parsed.Scheme != Uri.UriSchemeHttp)
            throw new ConfigurationError("base_url must use HTTPS");
        if (!string.IsNullOrEmpty(parsed.Query) || !string.IsNullOrEmpty(parsed.Fragment) ||
            parsed.AbsolutePath is not ("/" or ""))
            throw new ConfigurationError("base_url may contain only an optional root path");
        if (string.IsNullOrEmpty(parsed.Host) || parsed.Port is < 1 or > 65535)
            throw new ConfigurationError("base_url must contain a host and valid port");
        if (parsed.Scheme == Uri.UriSchemeHttp)
        {
            if (!allowLoopbackHttp)
                throw new ConfigurationError("HTTP is only allowed for explicit loopback testing");
            var literalHost = parsed.DnsSafeHost;
            if (!IPAddress.TryParse(literalHost, out var address) || !IPAddress.IsLoopback(address) ||
                !string.Equals(address.ToString(), literalHost, StringComparison.OrdinalIgnoreCase))
                throw new ConfigurationError("loopback HTTP requires a literal loopback IP");
        }
        var authority = parsed.GetLeftPart(UriPartial.Authority);
        return new Uri(authority + "/", UriKind.Absolute);
    }
}
