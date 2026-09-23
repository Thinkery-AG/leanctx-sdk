using System.Net;
using System.Net.Http.Headers;

namespace Thinkery.LeanCtx;

/// <summary>
/// Bounded authenticated client for tenant-bound source planning and context materialization.
/// These operations prepare context and do not establish model transmission or execution proof.
/// </summary>
public sealed class EnterpriseEngineClient : IDisposable
{
    private const string ContextPlanPath = "/v1/engine/context-plan";
    private const string ContextMaterializePath = "/v1/engine/context-materialize";
    private const int MaxCredentialBytes = 4096;
    private const int MaxUrlBytes = 4096;

    private readonly Uri _baseUri;
    private readonly string _credential;
    private readonly HttpClient _httpClient;
    private bool _disposed;

    public EnterpriseEngineClient(
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
        TenantId = EnginePlanningWire.ConfiguredTenant(tenantId);

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
        EnginePlanningRequest request,
        IReadOnlyList<string> sourceIds) =>
        ContextPlanAsync(request, sourceIds).GetAwaiter().GetResult();

    /// <summary>Request a source plan asynchronously for authorized source identifiers.</summary>
    public async Task<IReadOnlyDictionary<string, object?>> ContextPlanAsync(
        EnginePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        CancellationToken cancellationToken = default)
    {
        EnsureNotDisposed();
        var checkedRequest = RequireRequest(request, "context_plan");
        var checkedSourceIds = EnginePlanningWire.SourceIds(sourceIds);
        var payload = WireJson.CanonicalBytes(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["planning"] = checkedRequest.ToDictionary(),
            ["source_ids"] = checkedSourceIds,
        });
        if (payload.Length > EnginePlanningWire.MaxRequestBytes)
            throw new ValidationError("Enterprise Engine request exceeds its byte bound");

        var raw = await PostJsonAsync(
            ContextPlanPath, payload, EnginePlanningWire.MaxPlanResponseBytes,
            "Enterprise Engine", "planning request", cancellationToken).ConfigureAwait(false);
        var normalized = EnginePlanningWire.ParsePlanResponse(raw, checkedRequest, checkedSourceIds, TenantId);
        return (IReadOnlyDictionary<string, object?>)WireJson.DeepFreeze(normalized)!;
    }

    /// <summary>Materialize a plan bound to the observed governance revision and digest.</summary>
    public IReadOnlyDictionary<string, object?> ContextMaterialize(
        EnginePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest,
        string? planningEvaluationTime = null) =>
        ContextMaterializeAsync(request, sourceIds, expectedGovernanceRevision,
            expectedBindingDigest, planningEvaluationTime).GetAwaiter().GetResult();

    /// <summary>Materialize a plan asynchronously under the supplied governance binding.</summary>
    public async Task<IReadOnlyDictionary<string, object?>> ContextMaterializeAsync(
        EnginePlanningRequest request,
        IReadOnlyList<string> sourceIds,
        ulong expectedGovernanceRevision,
        string expectedBindingDigest,
        string? planningEvaluationTime = null,
        CancellationToken cancellationToken = default)
    {
        EnsureNotDisposed();
        var checkedRequest = RequireRequest(request, "context_materialize");
        var checkedSourceIds = EnginePlanningWire.SourceIds(sourceIds);
        var bindingDigest = EnginePlanningWire.ValidateBindingDigest(expectedBindingDigest);
        var payloadValue = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["planning"] = checkedRequest.ToDictionary(),
            ["source_ids"] = checkedSourceIds,
            ["expected_governance_revision"] = expectedGovernanceRevision,
            ["expected_binding_digest"] = bindingDigest,
        };
        if (planningEvaluationTime is not null)
            payloadValue["planning_evaluation_time"] =
                EnginePlanningWire.ValidateTimestamp(planningEvaluationTime, "planning_evaluation_time");
        var payload = WireJson.CanonicalBytes(payloadValue);
        if (payload.Length > EnginePlanningWire.MaxRequestBytes)
            throw new ValidationError("Enterprise Engine materialization request exceeds its byte bound");

        var raw = await PostJsonAsync(
            ContextMaterializePath, payload, EnginePlanningWire.MaxMaterializationResponseBytes,
            "Enterprise Engine materialization", "materialization request", cancellationToken)
            .ConfigureAwait(false);
        var normalized = EnginePlanningWire.ParseMaterializationResponse(
            raw, checkedRequest, checkedSourceIds, TenantId, expectedGovernanceRevision, bindingDigest);
        return (IReadOnlyDictionary<string, object?>)WireJson.DeepFreeze(normalized)!;
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
        CancellationToken cancellationToken)
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

        try
        {
            using var response = await _httpClient.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, linked.Token).ConfigureAwait(false);
            linked.Token.ThrowIfCancellationRequested();
            var status = (int)response.StatusCode;
            if (status is >= 300 and < 400)
                throw new EngineProtocolError($"{operation} redirects are not followed");
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
        catch (EngineRejected)
        {
            throw;
        }
        catch (EngineProtocolError)
        {
            throw;
        }
        catch (EngineUnavailable)
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
                throw new EngineProtocolError("Enterprise Engine response exceeds its byte bound");
            output.Write(buffer, 0, count);
        }
    }

    private static EnginePlanningRequest RequireRequest(EnginePlanningRequest? request, string operation) =>
        request ?? throw new ValidationError($"{operation} requires EnginePlanningRequest");

    private void EnsureNotDisposed()
    {
        if (_disposed)
            throw new ConfigurationError("EnterpriseEngineClient is disposed");
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
