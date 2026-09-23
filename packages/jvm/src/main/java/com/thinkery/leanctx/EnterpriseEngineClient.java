package com.thinkery.leanctx;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.Proxy;
import java.net.ProxySelector;
import java.net.SocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.math.BigInteger;
import java.time.Duration;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import javax.net.ssl.SSLParameters;

/**
 * Authenticated, bounded Enterprise source planning and materialization client.
 * Planning and materialization prepare context; they do not prove model transmission.
 */
public final class EnterpriseEngineClient implements AutoCloseable {
    private static final String CONTEXT_PLAN_PATH = "/v1/engine/context-plan";
    private static final String CONTEXT_MATERIALIZE_PATH = "/v1/engine/context-materialize";
    private static final Duration DEFAULT_TIMEOUT = Duration.ofSeconds(30);
    private static final Duration MIN_TIMEOUT = Duration.ofMillis(100);
    private static final Duration MAX_TIMEOUT = Duration.ofSeconds(120);
    private static final int MAX_CREDENTIAL_BYTES = 4096;
    private static final int MAX_URL_BYTES = 4096;
    private static final ProxySelector DIRECT_PROXY_SELECTOR = new ProxySelector() {
        @Override
        public List<Proxy> select(URI uri) {
            return List.of(Proxy.NO_PROXY);
        }

        @Override
        public void connectFailed(URI uri, SocketAddress address, IOException error) {
            // Direct connections have no proxy endpoint to report.
        }
    };

    private final String baseUrl;
    private final String tenantId;
    private final String credential;
    private final Duration timeout;
    private final HttpClient httpClient;

    /** Construct a production HTTPS client with a 30 second deadline. */
    public EnterpriseEngineClient(String baseUrl, String credential, String tenantId) {
        this(baseUrl, credential, tenantId, DEFAULT_TIMEOUT, false);
    }

    /** Construct a production HTTPS client with the supplied request deadline. */
    public EnterpriseEngineClient(String baseUrl, String credential, String tenantId,
                                  Duration timeout) {
        this(baseUrl, credential, tenantId, timeout, false);
    }

    /**
     * Construct a client; HTTP is accepted only when explicitly enabled for a
     * literal loopback IP used by deterministic local transport tests.
     */
    public EnterpriseEngineClient(String baseUrl, String credential, String tenantId,
                                  Duration timeout, boolean allowLoopbackHttp) {
        this.baseUrl = checkedBaseUrl(baseUrl, allowLoopbackHttp);
        this.credential = checkedCredential(credential);
        this.tenantId = EnterprisePlanningProtocol.inputUuid(tenantId, "tenant_id", true);
        this.timeout = checkedTimeout(timeout);
        this.httpClient = newHttpClient(this.timeout);
    }

    public String tenantId() {
        return tenantId;
    }

    public String getTenantId() {
        return tenantId;
    }

    /** Release the JDK HTTP client's internal resources. */
    @Override
    public void close() {
        httpClient.close();
    }

    /** Plan authenticated Enterprise source identifiers without execution evidence. */
    public Map<String, Object> contextPlan(EnginePlanningRequest request, List<String> sourceIds) {
        if (request == null) {
            throw new ValidationError("context_plan requires EnginePlanningRequest");
        }
        List<String> normalizedIds = EnterprisePlanningProtocol.sourceIds(sourceIds);
        Map<String, Object> body = Map.of(
                "planning", request.toDict(),
                "source_ids", normalizedIds);
        byte[] payload = requestBytes(body, "Enterprise Engine planning request");
        byte[] raw = postJson(CONTEXT_PLAN_PATH, payload,
                EnterprisePlanningProtocol.MAX_PLAN_RESPONSE_BYTES,
                "Enterprise Engine context-plan", "planning request");
        try {
            return EnterprisePlanningProtocol.parsePlanResponse(raw, request, normalizedIds, tenantId);
        } catch (EngineProtocolError error) {
            throw withoutCredential(error, "Enterprise Engine context-plan response is invalid");
        }
    }

    /** Materialize a plan bound to the expected governance revision and binding digest. */
    public Map<String, Object> contextMaterialize(EnginePlanningRequest request,
                                                  List<String> sourceIds,
                                                  long expectedGovernanceRevision,
                                                  String expectedBindingDigest) {
        return contextMaterialize(request, sourceIds, BigInteger.valueOf(expectedGovernanceRevision),
                expectedBindingDigest, null);
    }

    /** Materialize a plan with an optional canonical evaluation-time identity echo. */
    public Map<String, Object> contextMaterialize(EnginePlanningRequest request,
                                                  List<String> sourceIds,
                                                  long expectedGovernanceRevision,
                                                  String expectedBindingDigest,
                                                  String planningEvaluationTime) {
        return contextMaterialize(request, sourceIds, BigInteger.valueOf(expectedGovernanceRevision),
                expectedBindingDigest, planningEvaluationTime);
    }

    /** Materialize using the complete unsigned governance-revision domain. */
    public Map<String, Object> contextMaterialize(EnginePlanningRequest request,
                                                  List<String> sourceIds,
                                                  BigInteger expectedGovernanceRevision,
                                                  String expectedBindingDigest) {
        return contextMaterialize(request, sourceIds, expectedGovernanceRevision,
                expectedBindingDigest, null);
    }

    /** Materialize using the complete unsigned governance-revision domain. */
    public Map<String, Object> contextMaterialize(EnginePlanningRequest request,
                                                  List<String> sourceIds,
                                                  BigInteger expectedGovernanceRevision,
                                                  String expectedBindingDigest,
                                                  String planningEvaluationTime) {
        if (request == null) {
            throw new ValidationError("context_materialize requires EnginePlanningRequest");
        }
        List<String> normalizedIds = EnterprisePlanningProtocol.sourceIds(sourceIds);
        BigInteger governanceRevision = EnterprisePlanningProtocol.inputU64(
                expectedGovernanceRevision, "expected_governance_revision");
        String bindingDigest = EnterprisePlanningProtocol.inputDigest(
                expectedBindingDigest, "expected_binding_digest");
        Map<String, Object> body = new java.util.LinkedHashMap<>();
        body.put("planning", request.toDict());
        body.put("source_ids", normalizedIds);
        body.put("expected_governance_revision", governanceRevision);
        body.put("expected_binding_digest", bindingDigest);
        if (planningEvaluationTime != null) {
            body.put("planning_evaluation_time", EnterprisePlanningProtocol.inputTimestamp(
                    planningEvaluationTime, "planning_evaluation_time"));
        }
        byte[] payload = requestBytes(body, "Enterprise Engine materialization request");
        byte[] raw = postJson(CONTEXT_MATERIALIZE_PATH, payload,
                EnterprisePlanningProtocol.MAX_MATERIALIZATION_RESPONSE_BYTES,
                "Enterprise Engine materialization", "materialization request");
        try {
            return EnterprisePlanningProtocol.parseMaterializationResponse(
                    raw, request, normalizedIds, governanceRevision, bindingDigest, tenantId);
        } catch (EngineProtocolError error) {
            throw withoutCredential(error, "Enterprise Engine materialization response is invalid");
        }
    }

    private EngineProtocolError withoutCredential(EngineProtocolError error, String fallback) {
        String message = error.getMessage();
        return message != null && message.contains(credential) ? new EngineProtocolError(fallback) : error;
    }

    private byte[] postJson(String path, byte[] payload, int maximumResponseBytes,
                            String operation, String rejection) {
        URI endpoint = URI.create(baseUrl + path);
        HttpRequest request = HttpRequest.newBuilder(endpoint)
                .timeout(timeout)
                .header("Accept", "application/json")
                .header("Accept-Encoding", "identity")
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + credential)
                .POST(HttpRequest.BodyPublishers.ofByteArray(payload))
                .build();
        CompletableFuture<HttpResponse<BoundedBody>> pending;
        try {
            pending = httpClient.sendAsync(request, info -> new BoundedBodySubscriber(
                    isSuccess(info.statusCode()) ? maximumResponseBytes : 0));
        } catch (RuntimeException error) {
            throw new EngineUnavailable(operation + " request failed");
        }
        HttpResponse<BoundedBody> response;
        try {
            response = pending.get(timeout.toNanos(), TimeUnit.NANOSECONDS);
        } catch (TimeoutException error) {
            pending.cancel(true);
            throw new EngineTimeout(operation + " request exceeded its deadline");
        } catch (InterruptedException error) {
            pending.cancel(true);
            Thread.currentThread().interrupt();
            throw new EngineUnavailable(operation + " request was interrupted");
        } catch (ExecutionException error) {
            Throwable cause = error.getCause();
            if (cause instanceof HttpTimeoutException) {
                throw new EngineTimeout(operation + " request exceeded its deadline");
            }
            if (cause instanceof EngineProtocolError protocolError) {
                throw protocolError;
            }
            throw new EngineUnavailable(operation + " request failed");
        } catch (java.util.concurrent.CancellationException error) {
            throw new EngineUnavailable(operation + " request was cancelled");
        }

        int status = response.statusCode();
        if (status >= 300 && status < 400) {
            throw new EngineProtocolError(operation + " redirects are not followed");
        }
        if (status == 401) {
            throw new EngineRejected(operation + " authentication was rejected");
        }
        if (status == 403) {
            throw new PolicyAdmissionError(operation + " policy rejected the request");
        }
        if (status >= 500) {
            throw new EngineUnavailable(operation + " returned a server error");
        }
        if (status < 200 || status >= 300) {
            throw new EngineRejected(operation + " rejected the " + rejection);
        }
        Optional<String> contentLength = response.headers().firstValue("Content-Length");
        if (contentLength.isPresent()) {
            String value = contentLength.get();
            if (!value.matches("0|[1-9][0-9]*")) {
                throw new EngineProtocolError(operation + " response length is invalid");
            }
            try {
                if (new BigInteger(value).compareTo(BigInteger.valueOf(maximumResponseBytes)) > 0) {
                    throw new EngineProtocolError(operation + " response exceeds its byte bound");
                }
            } catch (NumberFormatException error) {
                throw new EngineProtocolError(operation + " response length is invalid");
            }
        }
        if (response.body().oversized()) {
            throw new EngineProtocolError(operation + " response exceeds its byte bound");
        }
        return response.body().bytes();
    }

    private static byte[] requestBytes(Map<String, Object> body, String label) {
        byte[] payload = EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(body);
        if (payload.length > EnterprisePlanningProtocol.MAX_REQUEST_BYTES) {
            throw new ValidationError(label + " exceeds its byte bound");
        }
        return payload;
    }

    private static Duration checkedTimeout(Duration value) {
        if (value == null || value.compareTo(MIN_TIMEOUT) < 0 || value.compareTo(MAX_TIMEOUT) > 0) {
            throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
        }
        return value;
    }

    private static String checkedCredential(String value) {
        if (value == null || value.isEmpty() || value.length() > MAX_CREDENTIAL_BYTES) {
            throw new ConfigurationError("credential must be a non-empty visible ASCII string");
        }
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character < 0x21 || character > 0x7e) {
                throw new ConfigurationError("credential must be a non-empty visible ASCII string");
            }
        }
        return value;
    }

    private static String checkedBaseUrl(String value, boolean allowLoopbackHttp) {
        if (value == null || value.isEmpty()) {
            throw new ConfigurationError("base_url must be a bounded absolute URL");
        }
        try {
            Json.validateUnicode(value, "base_url");
        } catch (ValidationError error) {
            throw new ConfigurationError("base_url must be valid UTF-8");
        }
        if (value.getBytes(StandardCharsets.UTF_8).length > MAX_URL_BYTES
                || value.codePoints().anyMatch(codePoint -> Character.isWhitespace(codePoint)
                        || Character.isSpaceChar(codePoint) || Character.isISOControl(codePoint))) {
            throw new ConfigurationError("base_url must not contain whitespace or controls");
        }
        final URI uri;
        try {
            uri = URI.create(value);
        } catch (IllegalArgumentException error) {
            throw new ConfigurationError("base_url is not a valid URL");
        }
        if (uri.isOpaque() || uri.getRawAuthority() == null || uri.getHost() == null
                || uri.getHost().isEmpty()) {
            throw new ConfigurationError("base_url must contain a host");
        }
        if (uri.getRawUserInfo() != null) {
            throw new ConfigurationError("base_url must not contain userinfo");
        }
        if (value.indexOf('?') >= 0 || value.indexOf('#') >= 0
                || uri.getRawQuery() != null || uri.getRawFragment() != null
                || !(uri.getRawPath() == null || uri.getRawPath().isEmpty() || uri.getRawPath().equals("/"))) {
            throw new ConfigurationError("base_url may contain only an optional root path");
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("https") && !scheme.equals("http")) {
            throw new ConfigurationError("base_url must use HTTPS");
        }
        int port = uri.getPort();
        if (port == 0 || port > 65535) {
            throw new ConfigurationError("base_url port is outside its bounds");
        }
        if (scheme.equals("http") && (!allowLoopbackHttp || !isLiteralLoopback(uri.getHost()))) {
            throw new ConfigurationError("HTTP is only allowed for an explicit literal loopback IP");
        }
        return scheme + "://" + uri.getRawAuthority();
    }

    private static boolean isLiteralLoopback(String uriHost) {
        String host = uriHost;
        if (host.startsWith("[") && host.endsWith("]")) {
            host = host.substring(1, host.length() - 1);
        }
        try {
            if (host.indexOf(':') >= 0) {
                if (!host.matches("[0-9a-fA-F:.]+")) {
                    return false;
                }
                return InetAddress.getByName(host).isLoopbackAddress();
            }
            if (!host.matches("[0-9.]+")) {
                return false;
            }
            String[] parts = host.split("\\.", -1);
            if (parts.length != 4) {
                return false;
            }
            byte[] address = new byte[4];
            for (int index = 0; index < parts.length; index++) {
                if (parts[index].isEmpty() || (parts[index].length() > 1 && parts[index].charAt(0) == '0')) {
                    return false;
                }
                int part = Integer.parseInt(parts[index]);
                if (part > 255) {
                    return false;
                }
                address[index] = (byte) part;
            }
            return InetAddress.getByAddress(address).isLoopbackAddress();
        } catch (IOException | NumberFormatException error) {
            return false;
        }
    }

    private static HttpClient newHttpClient(Duration timeout) {
        SSLParameters sslParameters = new SSLParameters();
        sslParameters.setEndpointIdentificationAlgorithm("HTTPS");
        return HttpClient.newBuilder()
                .connectTimeout(timeout)
                .followRedirects(HttpClient.Redirect.NEVER)
                .version(HttpClient.Version.HTTP_1_1)
                .proxy(DIRECT_PROXY_SELECTOR)
                .sslParameters(sslParameters)
                .build();
    }

    private static boolean isSuccess(int status) {
        return status >= 200 && status < 300;
    }

    private record BoundedBody(byte[] bytes, boolean oversized) {
    }

    private static final class BoundedBodySubscriber implements HttpResponse.BodySubscriber<BoundedBody> {
        private final int maximumBytes;
        private final ByteArrayOutputStream buffer;
        private final java.util.concurrent.CompletableFuture<BoundedBody> body =
                new java.util.concurrent.CompletableFuture<>();
        private Flow.Subscription subscription;

        private BoundedBodySubscriber(int maximumBytes) {
            this.maximumBytes = maximumBytes;
            this.buffer = new ByteArrayOutputStream(Math.min(maximumBytes, 8192));
        }

        @Override
        public CompletionStage<BoundedBody> getBody() {
            return body;
        }

        @Override
        public void onSubscribe(Flow.Subscription incoming) {
            if (subscription != null) {
                incoming.cancel();
                return;
            }
            subscription = incoming;
            incoming.request(1);
        }

        @Override
        public void onNext(List<ByteBuffer> items) {
            if (body.isDone()) {
                return;
            }
            for (ByteBuffer item : items) {
                ByteBuffer chunk = item.duplicate();
                if (chunk.remaining() > maximumBytes - buffer.size()) {
                    subscription.cancel();
                    body.complete(new BoundedBody(new byte[0], true));
                    return;
                }
                byte[] bytes = new byte[chunk.remaining()];
                chunk.get(bytes);
                buffer.writeBytes(bytes);
            }
            subscription.request(1);
        }

        @Override
        public void onError(Throwable error) {
            body.completeExceptionally(error);
        }

        @Override
        public void onComplete() {
            body.complete(new BoundedBody(buffer.toByteArray(), false));
        }
    }

}
