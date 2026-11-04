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
@SuppressWarnings("removal")
public class EnterpriseInstanceClient implements AutoCloseable {
    private static final String CONTEXT_PLAN_PATH = "/v1/engine/context-plan";
    private static final String CONTEXT_MATERIALIZE_PATH = "/v1/engine/context-materialize";
    private static final String CONTEXT_MATERIALIZE_V2_PATH = "/v1/engine/context-materialize-v2";
    private static final String PRESENTED_USE_PATH = "/v1/engine/presented-use";
    private static final String PRESENTER_REVOKE_PATH = "/v1/engine/presenter-revoke";
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
    public EnterpriseInstanceClient(String baseUrl, String credential, String tenantId) {
        this(baseUrl, credential, tenantId, DEFAULT_TIMEOUT, false);
    }

    /** Construct a production HTTPS client with the supplied request deadline. */
    public EnterpriseInstanceClient(String baseUrl, String credential, String tenantId,
                                  Duration timeout) {
        this(baseUrl, credential, tenantId, timeout, false);
    }

    /**
     * Construct a client; HTTP is accepted only when explicitly enabled for a
     * literal loopback IP used by deterministic local transport tests.
     */
    public EnterpriseInstanceClient(String baseUrl, String credential, String tenantId,
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
    public Map<String, Object> contextPlan(InstancePlanningRequest request, List<String> sourceIds) {
        if (request == null) {
            throw new ValidationError("context_plan requires InstancePlanningRequest");
        }
        List<String> normalizedIds = EnterprisePlanningProtocol.sourceIds(sourceIds);
        Map<String, Object> body = Map.of(
                "planning", request.toDict(),
                "source_ids", normalizedIds);
        byte[] payload = requestBytes(body, "Enterprise instance planning request");
        byte[] raw = postJson(CONTEXT_PLAN_PATH, payload,
                EnterprisePlanningProtocol.MAX_PLAN_RESPONSE_BYTES,
                "Enterprise instance context-plan", "planning request");
        try {
            return EnterprisePlanningProtocol.parsePlanResponse(raw, request, normalizedIds, tenantId);
        } catch (InstanceProtocolError error) {
            throw withoutCredential(error, "Enterprise instance context-plan response is invalid");
        }
    }

    /** Materialize a plan bound to the expected governance revision and binding digest. */
    public Map<String, Object> contextMaterialize(InstancePlanningRequest request,
                                                  List<String> sourceIds,
                                                  long expectedGovernanceRevision,
                                                  String expectedBindingDigest) {
        return contextMaterialize(request, sourceIds, BigInteger.valueOf(expectedGovernanceRevision),
                expectedBindingDigest, null);
    }

    /** Materialize a plan with an optional canonical evaluation-time identity echo. */
    public Map<String, Object> contextMaterialize(InstancePlanningRequest request,
                                                  List<String> sourceIds,
                                                  long expectedGovernanceRevision,
                                                  String expectedBindingDigest,
                                                  String planningEvaluationTime) {
        return contextMaterialize(request, sourceIds, BigInteger.valueOf(expectedGovernanceRevision),
                expectedBindingDigest, planningEvaluationTime);
    }

    /** Materialize using the complete unsigned governance-revision domain. */
    public Map<String, Object> contextMaterialize(InstancePlanningRequest request,
                                                  List<String> sourceIds,
                                                  BigInteger expectedGovernanceRevision,
                                                  String expectedBindingDigest) {
        return contextMaterialize(request, sourceIds, expectedGovernanceRevision,
                expectedBindingDigest, null);
    }

    /** Materialize using the complete unsigned governance-revision domain. */
    public Map<String, Object> contextMaterialize(InstancePlanningRequest request,
                                                  List<String> sourceIds,
                                                  BigInteger expectedGovernanceRevision,
                                                  String expectedBindingDigest,
                                                  String planningEvaluationTime) {
        if (request == null) {
            throw new ValidationError("context_materialize requires InstancePlanningRequest");
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
        byte[] payload = requestBytes(body, "Enterprise instance materialization request");
        byte[] raw = postJson(CONTEXT_MATERIALIZE_PATH, payload,
                EnterprisePlanningProtocol.MAX_MATERIALIZATION_RESPONSE_BYTES,
                "Enterprise instance materialization", "materialization request");
        try {
            return EnterprisePlanningProtocol.parseMaterializationResponse(
                    raw, request, normalizedIds, governanceRevision, bindingDigest, tenantId);
        } catch (InstanceProtocolError error) {
            throw withoutCredential(error, "Enterprise instance materialization response is invalid");
        }
    }

    /**
     * Materialize under a new presenter capability with the given lifetime
     * (1 to 86400 seconds). The capability is returned once, as
     * {@code result.materialization.presenter.capability}.
     *
     * <p>A capability proves the holder of the capability and nothing else: the
     * suite checks the API key, its tenant and the current source rights first
     * and at every use, and the capability is usable only with the API key it
     * was issued to. An {@link UnsupportedInstanceError} means the suite or its
     * LeanCTX instance does not serve the v2 contract.
     */
    public Map<String, Object> contextMaterializeV2(InstancePlanningRequest request,
                                                    List<String> sourceIds,
                                                    BigInteger expectedGovernanceRevision,
                                                    String expectedBindingDigest,
                                                    String planningEvaluationTime,
                                                    int presenterTtlSeconds) {
        if (presenterTtlSeconds < 1 || presenterTtlSeconds > PresenterCapability.MAX_TTL_SECONDS) {
            throw new ValidationError("presenter lifetime must be 1 to 86400 seconds");
        }
        return contextMaterializeV2(request, sourceIds, expectedGovernanceRevision,
                expectedBindingDigest, planningEvaluationTime,
                Map.of("mode", "issue", "ttl_seconds", presenterTtlSeconds), null);
    }

    /**
     * Materialize under a capability this host already holds. It travels in the
     * {@code x-leanctx-presenter} request header and is never part of the body;
     * the result carries no capability.
     */
    public Map<String, Object> contextMaterializeV2(InstancePlanningRequest request,
                                                    List<String> sourceIds,
                                                    BigInteger expectedGovernanceRevision,
                                                    String expectedBindingDigest,
                                                    String planningEvaluationTime,
                                                    PresenterCapability presenter) {
        if (presenter == null) {
            throw new ValidationError("context_materialize_v2 requires a PresenterCapability");
        }
        return contextMaterializeV2(request, sourceIds, expectedGovernanceRevision,
                expectedBindingDigest, planningEvaluationTime, Map.of("mode", "renew"), presenter);
    }

    private Map<String, Object> contextMaterializeV2(InstancePlanningRequest request,
                                                     List<String> sourceIds,
                                                     BigInteger expectedGovernanceRevision,
                                                     String expectedBindingDigest,
                                                     String planningEvaluationTime,
                                                     Map<String, Object> mode,
                                                     PresenterCapability held) {
        if (request == null) {
            throw new ValidationError("context_materialize_v2 requires InstancePlanningRequest");
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
        body.put("presenter", mode);
        if (planningEvaluationTime != null) {
            body.put("planning_evaluation_time", EnterprisePlanningProtocol.inputTimestamp(
                    planningEvaluationTime, "planning_evaluation_time"));
        }
        byte[] payload = requestBytes(body, "Enterprise instance materialization request");
        byte[] raw = postJson(CONTEXT_MATERIALIZE_V2_PATH, payload,
                EnterprisePlanningProtocol.MAX_MATERIALIZATION_RESPONSE_BYTES
                        + EnterprisePlanningProtocol.PRESENTER_ENVELOPE_BYTES,
                "Enterprise instance materialization", "materialization request", held,
                "Enterprise suite does not serve context-materialize-v2: LeanCTX instance "
                        + PresenterCapability.MIN_ENGINE_VERSION + " or later is required");
        try {
            return EnterprisePlanningProtocol.parseMaterializationV2Response(
                    raw, request, normalizedIds, governanceRevision, bindingDigest, tenantId,
                    held == null);
        } catch (InstanceProtocolError error) {
            throw withoutCredential(error, "Enterprise instance materialization response is invalid");
        }
    }

    private static final String PRESENTER_UNSUPPORTED =
            "Enterprise suite does not serve presenter capabilities: LeanCTX instance "
                    + PresenterCapability.MIN_ENGINE_VERSION + " or later is required";

    /**
     * Use text that was materialized under {@code presenter}. {@code mode} is
     * {@code "check"} (may the exact delivered text {@code value} still be used
     * by this holder now), {@code "retain"} (also keep a recovery copy; the
     * answer carries its {@code recovery_id}) or {@code "expand"} ({@code value}
     * is a recovery id; the answer carries the kept text).
     *
     * <p>{@code sourceIds} are the governed sources the text was materialized
     * from: exactly those, or the use is refused. The capability travels in the
     * {@code x-leanctx-presenter} request header, never in the body. The suite
     * checks the API key, its tenant and its current rights to every source
     * first and again before it answers. A refused capability is a
     * {@link PolicyAdmissionError}; an {@link UnsupportedInstanceError} means the
     * suite or its LeanCTX instance does not serve the operation.
     */
    public Map<String, Object> presentedUse(PresenterCapability presenter, List<String> sourceIds,
                                            BigInteger expectedGovernanceRevision,
                                            String mode, String value) {
        if (presenter == null) {
            throw new ValidationError("presented_use requires a PresenterCapability");
        }
        List<String> normalizedIds = EnterprisePlanningProtocol.sourceIds(sourceIds);
        BigInteger governanceRevision = EnterprisePlanningProtocol.inputU64(
                expectedGovernanceRevision, "expected_governance_revision");
        boolean expanding = "expand".equals(mode);
        boolean wellFormed = value != null && (expanding
                ? value.matches("[0-9a-f]{64}")
                : ("check".equals(mode) || "retain".equals(mode)) && !value.isEmpty()
                        && value.getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                                <= EnterprisePlanningProtocol.MAX_PRESENTED_CONTENT_BYTES);
        if (normalizedIds.isEmpty() || !wellFormed) {
            throw new ValidationError(
                    "presented_use requires source ids, a mode and a bounded text or a recovery id");
        }
        Map<String, Object> body = new java.util.LinkedHashMap<>();
        body.put("schema_version", BigInteger.ONE);
        body.put("source_ids", normalizedIds);
        body.put("expected_governance_revision", governanceRevision);
        body.put("use", Map.of("mode", mode, expanding ? "recovery_id" : "content", value));
        byte[] raw = postJson(PRESENTED_USE_PATH,
                EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(body),
                EnterprisePlanningProtocol.MAX_MATERIALIZATION_RESPONSE_BYTES
                        + EnterprisePlanningProtocol.PRESENTER_ENVELOPE_BYTES,
                "Enterprise presented use", "presented use request", presenter,
                PRESENTER_UNSUPPORTED);
        try {
            return EnterprisePlanningProtocol.parsePresentedUseResponse(
                    raw, mode, value, normalizedIds, governanceRevision, tenantId, presenter);
        } catch (InstanceProtocolError error) {
            throw withoutCredential(error, "Enterprise presented use response is invalid");
        }
    }

    /**
     * End {@code presenter}: from now on it presents and reuses nothing. Only
     * the API key it was issued to can revoke it.
     */
    public Map<String, Object> revokePresenter(PresenterCapability presenter) {
        if (presenter == null) {
            throw new ValidationError("presenter_revoke requires a PresenterCapability");
        }
        byte[] raw = postJson(PRESENTER_REVOKE_PATH,
                EnterprisePlanningProtocol.canonicalBytesWithUnsignedU64(
                        Map.of("schema_version", BigInteger.ONE)),
                EnterprisePlanningProtocol.PRESENTER_ENVELOPE_BYTES,
                "Enterprise presenter revocation", "presenter revocation request", presenter,
                PRESENTER_UNSUPPORTED);
        try {
            return EnterprisePlanningProtocol.parsePresenterRevocationResponse(raw, tenantId);
        } catch (InstanceProtocolError error) {
            throw withoutCredential(error, "Enterprise presenter revocation response is invalid");
        }
    }

    private InstanceProtocolError withoutCredential(InstanceProtocolError error, String fallback) {
        String message = error.getMessage();
        return message != null && message.contains(credential) ? new EngineProtocolError(fallback) : error;
    }

    private byte[] postJson(String path, byte[] payload, int maximumResponseBytes,
                            String operation, String rejection) {
        return postJson(path, payload, maximumResponseBytes, operation, rejection, null, null);
    }

    /**
     * {@code postJson} for an operation that may show a presenter capability (a
     * request header, never a body member) and that an older suite or LeanCTX instance may
     * not serve: with {@code unsupported}, 404, 405 and 501 are an
     * {@link UnsupportedInstanceError}.
     */
    private byte[] postJson(String path, byte[] payload, int maximumResponseBytes,
                            String operation, String rejection,
                            PresenterCapability presenter, String unsupported) {
        URI endpoint = URI.create(baseUrl + path);
        HttpRequest.Builder builder = HttpRequest.newBuilder(endpoint)
                .timeout(timeout)
                .header("Accept", "application/json")
                .header("Accept-Encoding", "identity")
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + credential);
        if (presenter != null) {
            builder.header(PresenterCapability.HEADER, presenter.reveal());
        }
        HttpRequest request = builder
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
            if (cause instanceof InstanceProtocolError protocolError) {
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
        if (unsupported != null && (status == 404 || status == 405 || status == 501)) {
            throw new UnsupportedEngineError(unsupported);
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
