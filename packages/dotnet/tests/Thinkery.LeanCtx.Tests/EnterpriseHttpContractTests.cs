using System.Collections.Concurrent;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using Thinkery.LeanCtx;

// These fixtures exercise the HTTP transport contract on loopback only; they are not
// installed-user proof and do not call an Engine, GitLab, or another remote service.
internal static class EnterpriseHttpContractTests
{
    private const string TenantId = "11111111-1111-4111-8111-111111111111";
    private const string SourceId = "22222222-2222-4222-8222-222222222222";
    private const string OtherSourceId = "33333333-3333-4333-8333-333333333333";
    private const string Token = "local-contract-token";

    internal static void Run()
    {
        Unsigned64WireContract();
        PlanAndMaterializationContract();
        ResponseBindingsFailClosed();
        TransportBoundsAndDeadlines();
        UrlAndTlsValidation();
    }

    private static void Unsigned64WireContract()
    {
        var bytes = WireJson.CanonicalBytes(new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["value"] = ulong.MaxValue,
        });
        Equal("{\"value\":18446744073709551615}", Encoding.UTF8.GetString(bytes));
        var parsed = WireJson.ParseObject(bytes, "unsigned-64 fixture", 128);
        Equal(ulong.MaxValue, parsed["value"]);
        Throws<ValidationError>(() => new EnginePlanningRequest("bad-budget", "query", 0));
    }

    private static void PlanAndMaterializationContract()
    {
        var request = new EnginePlanningRequest("task-enterprise-contract", "summarize the issue", 128);
        var sourcePlan = SourcePlan(request, SourceId);
        var bindingDigest = (string)sourcePlan["binding_digest"]!;
        const string content = "prepared issue context\n";
        using var server = new LocalHttpServer((captured, _) =>
        {
            if (captured.Path == "/v1/engine/context-plan")
                return Json(200, PlanEnvelope(request, SourceId));
            if (captured.Path == "/v1/engine/context-materialize")
                return Json(200, MaterializationEnvelope(request, SourceId, content));
            return Json(404, new byte[0]);
        });
        using var client = new EnterpriseEngineClient(
            server.Url, Token, TenantId, timeout: 5, allowLoopbackHttp: true);

        Throws<ValidationError>(() => client.ContextPlan(
            request, new[] { SourceId, SourceId.ToUpperInvariant() }));
        Equal(0, server.Requests.Count);
        var planned = client.ContextPlan(request, new[] { SourceId.ToUpperInvariant() });
        Equal(TenantId, planned["tenant_id"]);
        Equal(7UL, planned["governance_revision"]);
        var materialized = client.ContextMaterialize(
            request, new[] { SourceId }, 7, bindingDigest, "2025-06-01T12:30:00Z");
        var value = (IReadOnlyDictionary<string, object?>)materialized["materialization"]!;
        Equal(content, value["content"]);
        Equal(WireJson.Sha256Digest(content), value["materialized_digest"]);
        Equal(4UL, value["materialized_token_count"]);

        var calls = server.Requests.ToArray();
        Equal(2, calls.Length);
        Equal("POST", calls[0].Method);
        Equal("/v1/engine/context-plan", calls[0].Path);
        Equal("Bearer " + Token, calls[0].Headers["Authorization"]);
        var planning = (IReadOnlyDictionary<string, object?>)calls[0].Body["planning"]!;
        Equal("1.0.0", planning["engine_interface_version"]);
        Equal("task-enterprise-contract", planning["task_id"]);
        Equal(SourceId, ((IReadOnlyList<object?>)calls[0].Body["source_ids"]!)[0]);

        Equal("/v1/engine/context-materialize", calls[1].Path);
        Equal("Bearer " + Token, calls[1].Headers["Authorization"]);
        Equal(7L, calls[1].Body["expected_governance_revision"]);
        Equal(bindingDigest, calls[1].Body["expected_binding_digest"]);
        Equal("2025-06-01T12:30:00Z", calls[1].Body["planning_evaluation_time"]);
    }

    private static void ResponseBindingsFailClosed()
    {
        var request = new EnginePlanningRequest("task-binding-contract", "check bindings", 64);
        using (var wrongTenant = new LocalHttpServer((_, _) =>
                   Json(200, PlanEnvelope(request, SourceId, tenantId: OtherTenantId))))
        using (var client = NewClient(wrongTenant.Url))
            Throws<EngineProtocolError>(() => client.ContextPlan(request, new[] { SourceId }));

        using (var unrequested = new LocalHttpServer((_, _) =>
                   Json(200, PlanEnvelope(request, OtherSourceId))))
        using (var client = NewClient(unrequested.Url))
            Throws<EngineProtocolError>(() => client.ContextPlan(request, new[] { SourceId }));

        using (var denied = new LocalHttpServer((_, _) =>
                   Json(200, PlanEnvelope(request, SourceId, permission: "denied"))))
        using (var client = NewClient(denied.Url))
            Throws<EngineProtocolError>(() => client.ContextPlan(request, new[] { SourceId }));

        var wrongVersionResponse = PlanEnvelope(request, SourceId);
        var wrongVersionPlan = (Dictionary<string, object?>)wrongVersionResponse["plan"]!;
        var wrongVersionResult = (Dictionary<string, object?>)wrongVersionPlan["result"]!;
        wrongVersionResult["engine_interface_version"] = "9.9.9";
        using (var wrongVersion = new LocalHttpServer((_, _) => Json(200, wrongVersionResponse)))
        using (var client = NewClient(wrongVersion.Url))
            Throws<EngineProtocolError>(() => client.ContextPlan(request, new[] { SourceId }));

        var validPlan = SourcePlan(request, SourceId);
        var validBindingDigest = (string)validPlan["binding_digest"]!;
        using (var wrongGovernance = new LocalHttpServer((_, _) =>
                   Json(200, MaterializationEnvelope(request, SourceId, "prepared", governanceRevision: 8))))
        using (var client = NewClient(wrongGovernance.Url))
            Throws<EngineProtocolError>(() => client.ContextMaterialize(
                request, new[] { SourceId }, 7, validBindingDigest));

        using (var wrongDigest = new LocalHttpServer((_, _) =>
                   Json(200, MaterializationEnvelope(request, SourceId, "prepared"))))
        using (var client = NewClient(wrongDigest.Url))
            Throws<EngineProtocolError>(() => client.ContextMaterialize(
                request, new[] { SourceId }, 7, "sha256:" + new string('f', 64)));

        using (var badContentDigest = new LocalHttpServer((_, _) =>
                   Json(200, MaterializationEnvelope(request, SourceId, "prepared", corruptContentDigest: true))))
        using (var client = NewClient(badContentDigest.Url))
            Throws<EngineProtocolError>(() => client.ContextMaterialize(
                request, new[] { SourceId }, 7, validBindingDigest));
    }

    private static void TransportBoundsAndDeadlines()
    {
        var request = new EnginePlanningRequest("task-transport-contract", "check transport", 64);
        using (var target = new LocalHttpServer((_, _) => Json(200, PlanEnvelope(request, SourceId))))
        using (var redirect = new LocalHttpServer((_, _) => new Reply(
                   302, Array.Empty<byte>(), new Dictionary<string, string>
                   {
                        ["Location"] = target.Url + "/v1/engine/context-plan",
                   })))
        using (var client = NewClient(redirect.Url))
        {
            Throws<EngineProtocolError>(() => client.ContextPlan(request, new[] { SourceId }));
            Equal(1, redirect.Requests.Count);
            Equal(0, target.Requests.Count);
        }

        using (var oversized = new LocalHttpServer((_, _) => new Reply(
                   200, Array.Empty<byte>(), DeclaredLength: 1024 * 1024 + 1)))
        using (var client = NewClient(oversized.Url))
            Throws<EngineProtocolError>(() => client.ContextPlan(request, new[] { SourceId }));

        using (var slow = new LocalHttpServer((_, _) =>
                   Json(200, PlanEnvelope(request, SourceId)) with { DelayMilliseconds = 500 }))
        using (var client = new EnterpriseEngineClient(
                   slow.Url, Token, TenantId, timeout: 0.2, allowLoopbackHttp: true))
            Throws<EngineTimeout>(() => client.ContextPlan(request, new[] { SourceId }));

        using (var slowBody = new LocalHttpServer((_, _) =>
                   Json(200, PlanEnvelope(request, SourceId)) with { BodyDelayMilliseconds = 500 }))
        using (var client = new EnterpriseEngineClient(
                   slowBody.Url, Token, TenantId, timeout: 0.2, allowLoopbackHttp: true))
            Throws<EngineTimeout>(() => client.ContextPlan(request, new[] { SourceId }));
    }

    private static void UrlAndTlsValidation()
    {
        Throws<ConfigurationError>(() => new EnterpriseEngineClient("http://127.0.0.1:1", Token, TenantId));
        Throws<ConfigurationError>(() => new EnterpriseEngineClient(
            "http://localhost:1", Token, TenantId, allowLoopbackHttp: true));
        Throws<ConfigurationError>(() => new EnterpriseEngineClient(
            "https://user:secret@example.invalid", Token, TenantId));
        Throws<ConfigurationError>(() => new EnterpriseEngineClient(
            "https://example.invalid", "token with spaces", TenantId));
        Throws<ConfigurationError>(() => new EnterpriseEngineClient(
            "https://example.invalid", Token, "00000000-0000-0000-0000-000000000000"));
        Throws<ConfigurationError>(() => new EnterpriseEngineClient(
            "https://example.invalid", Token, "invalid-tenant"));

        using var tls = new LocalInvalidTlsServer();
        using var client = new EnterpriseEngineClient(tls.Url, Token, TenantId, timeout: 3);
        Throws<EngineUnavailable>(() => client.ContextPlan(
            new EnginePlanningRequest("tls-contract", "verify certificate", 8), new[] { SourceId }));
        Equal(0, tls.HttpRequestCount);
    }

    private static string OtherTenantId => "44444444-4444-4444-8444-444444444444";

    private static EnterpriseEngineClient NewClient(string url) =>
        new(url, Token, TenantId, timeout: 5, allowLoopbackHttp: true);

    private static Reply Json(int status, object value) =>
        new(status, WireJson.CanonicalBytes(value));

    private static Dictionary<string, object?> PlanEnvelope(
        EnginePlanningRequest request,
        string sourceId,
        string tenantId = TenantId,
        ulong governanceRevision = 7,
        string permission = "permitted") => new(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["tenant_id"] = tenantId,
            ["governance_revision"] = governanceRevision,
            ["plan"] = SourcePlan(request, sourceId, permission),
        };

    private static Dictionary<string, object?> MaterializationEnvelope(
        EnginePlanningRequest request,
        string sourceId,
        string content,
        ulong governanceRevision = 7,
        bool corruptContentDigest = false)
    {
        var sourcePlan = SourcePlan(request, sourceId);
        var contentDigest = WireJson.Sha256Digest(content);
        var materialization = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["transport_version"] = 1L,
            ["engine_interface_version"] = Constants.ENGINE_INTERFACE_VERSION,
            ["plan"] = sourcePlan,
            ["materialized_digest"] = corruptContentDigest
                ? "sha256:" + new string('a', 64)
                : contentDigest,
            ["materialized_token_count"] = 4UL,
            ["content"] = content,
        };
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["tenant_id"] = TenantId,
            ["governance_revision"] = governanceRevision,
            ["materialization"] = materialization,
        };
    }

    private static Dictionary<string, object?> SourcePlan(
        EnginePlanningRequest request,
        string sourceId,
        string permission = "permitted")
    {
        const string sourceContent = "authorized fixture source";
        var contentDigest = WireJson.Sha256Digest(sourceContent);
        var descriptor = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["object_ref"] = sourceId,
            ["source_id"] = sourceId,
            ["source_type"] = "issue_tracker",
            ["content_digest"] = contentDigest,
            ["revision"] = "rev-17",
            ["owner"] = "team-leanctx",
            ["observed_at"] = "2025-05-01T00:00:00Z",
            ["valid_until"] = "2026-05-01T00:00:00Z",
            ["classification"] = "Internal",
            ["permission"] = permission,
        };
        var selection = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["source_ref"] = sourceId,
            ["provider"] = sourceId,
            ["disposition"] = "selected",
            ["token_count"] = 3UL,
            ["sha256_digest"] = contentDigest,
            ["reason_codes"] = new object?[] { "relevant" },
        };
        var unsignedPlan = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["context_plan_id"] = "context-plan-contract",
            ["task_id"] = request.TaskId,
            ["budget_tokens"] = (ulong)request.BudgetTokens,
            ["selections"] = new object?[] { selection },
        };
        var plan = new Dictionary<string, object?>(unsignedPlan, StringComparer.Ordinal)
        {
            ["projection_digest"] = WireJson.Sha256Digest(WireJson.CanonicalBytes(unsignedPlan)),
        };
        var result = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["schema_version"] = 1L,
            ["transport_version"] = 1L,
            ["engine_interface_version"] = Constants.ENGINE_INTERFACE_VERSION,
            ["plan"] = plan,
        };
        var bindings = new object?[] { descriptor };
        return new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["result"] = result,
            ["source_bindings"] = bindings,
            ["binding_digest"] = WireJson.Sha256Digest(
                WireJson.CanonicalBytes(new object?[] { result, bindings })),
        };
    }

    private static void Equal<T>(T expected, object? actual)
    {
        if (!Equals(expected, actual))
            throw new Exception($"expected {expected}, got {actual}");
    }

    private static void Equal<T>(T expected, int actual)
    {
        if (!Equals(expected, actual))
            throw new Exception($"expected {expected}, got {actual}");
    }

    private static void Throws<T>(Action action) where T : Exception
    {
        try
        {
            action();
        }
        catch (T)
        {
            return;
        }
        throw new Exception($"expected {typeof(T).Name}");
    }

    private sealed record Reply(
        int Status,
        byte[] Body,
        IReadOnlyDictionary<string, string>? Headers = null,
        long? DeclaredLength = null,
        int DelayMilliseconds = 0,
        int BodyDelayMilliseconds = 0);

    private sealed record CapturedRequest(
        string Method,
        string Path,
        IReadOnlyDictionary<string, string> Headers,
        IReadOnlyDictionary<string, object?> Body);

    private sealed class LocalHttpServer : IDisposable
    {
        private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
        private readonly Func<CapturedRequest, LocalHttpServer, Reply> _handler;
        private readonly ConcurrentQueue<CapturedRequest> _requests = new();
        private readonly Thread _worker;
        private volatile bool _stopping;
        private Exception? _failure;

        internal LocalHttpServer(Func<CapturedRequest, LocalHttpServer, Reply> handler)
        {
            _handler = handler;
            _listener.Start();
            var endpoint = (IPEndPoint)_listener.LocalEndpoint;
            Url = $"http://127.0.0.1:{endpoint.Port}";
            _worker = new Thread(Serve) { IsBackground = true, Name = "LeanCTX HTTP contract fixture" };
            _worker.Start();
        }

        internal string Url { get; }
        internal IReadOnlyCollection<CapturedRequest> Requests => _requests.ToArray();

        private void Serve()
        {
            while (!_stopping)
            {
                TcpClient client;
                try
                {
                    client = _listener.AcceptTcpClient();
                }
                catch (SocketException) when (_stopping)
                {
                    return;
                }
                catch (ObjectDisposedException) when (_stopping)
                {
                    return;
                }
                using (client)
                {
                    try
                    {
                        client.ReceiveTimeout = 5000;
                        var request = ReadRequest(client.GetStream());
                        _requests.Enqueue(request);
                        var reply = _handler(request, this);
                        if (reply.DelayMilliseconds > 0)
                            Thread.Sleep(reply.DelayMilliseconds);
                        WriteResponse(client.GetStream(), reply);
                    }
                    catch (IOException)
                    {
                        // A timed-out client closes the local fixture connection by design.
                    }
                    catch (Exception error)
                    {
                        Interlocked.CompareExchange(ref _failure, error, null);
                    }
                }
            }
        }

        public void Dispose()
        {
            _stopping = true;
            _listener.Stop();
            _worker.Join(TimeSpan.FromSeconds(2));
            if (_failure is not null)
                throw new Exception("loopback HTTP fixture failed", _failure);
        }

        private static CapturedRequest ReadRequest(NetworkStream stream)
        {
            var requestLine = ReadAsciiLine(stream);
            var parts = requestLine.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length != 3)
                throw new InvalidDataException("invalid HTTP request line");
            var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            while (true)
            {
                var line = ReadAsciiLine(stream);
                if (line.Length == 0)
                    break;
                var colon = line.IndexOf(':');
                if (colon <= 0)
                    throw new InvalidDataException("invalid HTTP header");
                headers[line[..colon].Trim()] = line[(colon + 1)..].Trim();
            }
            if (!headers.TryGetValue("Content-Length", out var rawLength) ||
                !int.TryParse(rawLength, out var length) || length < 0 || length > 64 * 1024)
                throw new InvalidDataException("invalid HTTP request length");
            var bodyBytes = ReadExact(stream, length);
            var body = WireJson.ParseObject(bodyBytes, "loopback request", 64 * 1024);
            return new CapturedRequest(parts[0], parts[1], headers, body);
        }

        private static string ReadAsciiLine(NetworkStream stream)
        {
            var bytes = new List<byte>();
            while (true)
            {
                var next = stream.ReadByte();
                if (next < 0)
                    throw new EndOfStreamException("HTTP request ended mid-header");
                if (next == '\n')
                    break;
                if (next != '\r')
                    bytes.Add((byte)next);
                if (bytes.Count > 8192)
                    throw new InvalidDataException("HTTP header line exceeded its bound");
            }
            return Encoding.ASCII.GetString(bytes.ToArray());
        }

        private static byte[] ReadExact(NetworkStream stream, int length)
        {
            var result = new byte[length];
            var offset = 0;
            while (offset < length)
            {
                var count = stream.Read(result, offset, length - offset);
                if (count == 0)
                    throw new EndOfStreamException("HTTP request ended mid-body");
                offset += count;
            }
            return result;
        }

        private static void WriteResponse(NetworkStream stream, Reply reply)
        {
            var reason = reply.Status switch
            {
                200 => "OK",
                302 => "Found",
                401 => "Unauthorized",
                403 => "Forbidden",
                404 => "Not Found",
                500 => "Internal Server Error",
                _ => "Contract Fixture",
            };
            var builder = new StringBuilder()
                .Append("HTTP/1.1 ").Append(reply.Status).Append(' ').Append(reason).Append("\r\n")
                .Append("Content-Type: application/json\r\n")
                .Append("Content-Length: ").Append(reply.DeclaredLength ?? reply.Body.Length).Append("\r\n")
                .Append("Connection: close\r\n");
            if (reply.Headers is not null)
                foreach (var (name, value) in reply.Headers)
                    builder.Append(name).Append(": ").Append(value).Append("\r\n");
            builder.Append("\r\n");
            var head = Encoding.ASCII.GetBytes(builder.ToString());
            stream.Write(head);
            stream.Flush();
            if (reply.BodyDelayMilliseconds > 0)
                Thread.Sleep(reply.BodyDelayMilliseconds);
            if (reply.Body.Length > 0)
                stream.Write(reply.Body);
            stream.Flush();
        }
    }

    private sealed class LocalInvalidTlsServer : IDisposable
    {
        private readonly RSA _rsa = RSA.Create(2048);
        private readonly X509Certificate2 _certificate;
        private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
        private readonly Thread _worker;
        private int _httpRequestCount;

        internal LocalInvalidTlsServer()
        {
            var request = new CertificateRequest(
                "CN=localhost", _rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
            request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, false));
            _certificate = request.CreateSelfSigned(
                DateTimeOffset.UtcNow.AddMinutes(-1), DateTimeOffset.UtcNow.AddHours(1));
            _listener.Start();
            var endpoint = (IPEndPoint)_listener.LocalEndpoint;
            Url = $"https://127.0.0.1:{endpoint.Port}";
            _worker = new Thread(Serve) { IsBackground = true, Name = "LeanCTX TLS contract fixture" };
            _worker.Start();
        }

        internal string Url { get; }
        internal int HttpRequestCount => Volatile.Read(ref _httpRequestCount);

        private void Serve()
        {
            try
            {
                using var client = _listener.AcceptTcpClient();
                using var ssl = new SslStream(client.GetStream(), leaveInnerStreamOpen: false);
                ssl.AuthenticateAsServer(
                    _certificate, clientCertificateRequired: false, SslProtocols.Tls12,
                    checkCertificateRevocation: false);
                // The server can complete its handshake before the client rejects
                // the certificate. Count application bytes, not TLS handshakes.
                ssl.ReadTimeout = 2000;
                if (ssl.ReadByte() >= 0)
                    Interlocked.Increment(ref _httpRequestCount);
            }
            catch (Exception error) when (error is AuthenticationException or IOException or SocketException or ObjectDisposedException)
            {
                // The expected result is rejection during certificate verification, before HTTP.
            }
        }

        public void Dispose()
        {
            _listener.Stop();
            _worker.Join(TimeSpan.FromSeconds(2));
            _certificate.Dispose();
            _rsa.Dispose();
        }
    }
}
