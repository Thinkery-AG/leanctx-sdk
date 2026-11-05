// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.FileAttributeView;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Strict subprocess adapter for Engine Interface v1. */
@SuppressWarnings("removal")
public class SubprocessInstanceClient implements InstanceClient {
    private static final Pattern STDERR_CODE = Pattern.compile("(?:^|\\n)engine:\\s*([a-z0-9_]+)");
    private final String instanceBinary;
    private final double timeout;

    public SubprocessInstanceClient() {
        this("lean-ctx", 30.0);
    }

    public SubprocessInstanceClient(String instanceBinary) {
        this(instanceBinary, 30.0);
    }

    public SubprocessInstanceClient(Path instanceBinary) {
        this(instanceBinary == null ? null : instanceBinary.toString(), 30.0);
    }

    public SubprocessInstanceClient(String instanceBinary, double timeout) {
        if (instanceBinary == null || instanceBinary.isEmpty()) {
            throw new ConfigurationError("instance_binary must not be empty");
        }
        if (!Double.isFinite(timeout) || timeout < 0.1 || timeout > 120.0) {
            throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
        }
        this.instanceBinary = instanceBinary;
        this.timeout = timeout;
    }

    public SubprocessInstanceClient(Path instanceBinary, double timeout) {
        this(instanceBinary == null ? null : instanceBinary.toString(), timeout);
    }

    public String instanceBinary() {
        return instanceBinary;
    }

    public String getInstanceBinary() {
        return instanceBinary;
    }

    /** @deprecated since 1.3.0, use {@link #instanceBinary()}. */
    @Deprecated(since = "1.3.0", forRemoval = true)
    public String engineBinary() {
        return instanceBinary;
    }

    /** @deprecated since 1.3.0, use {@link #getInstanceBinary()}. */
    @Deprecated(since = "1.3.0", forRemoval = true)
    public String getEngineBinary() {
        return instanceBinary;
    }

    public double timeout() {
        return timeout;
    }

    public double getTimeout() {
        return timeout;
    }

    @Override
    public ContextView contextView(ContextPlan plan) {
        if (plan == null) {
            throw new ValidationError("context_view requires ContextPlan");
        }
        ContextSource source = plan.source();
        Map<String, Object> request = Map.of(
                "schema_version", LeanCtx.SCHEMA_VERSION,
                "transport_version", LeanCtx.TRANSPORT_VERSION,
                "engine_interface_version", LeanCtx.ENGINE_INTERFACE_VERSION,
                "path", source.relativePath(),
                "mode", plan.mode());
        EngineProtocol.ParsedResponse response = invoke("context-view", source.projectRoot(), request);
        if (response.records == null) {
            throw new EngineProtocolError("context-view response omitted invocation/observation");
        }
        if (!(response.records.invocation.get("source_refs") instanceof List<?>)) {
            throw new EngineProtocolError("invocation source_refs are malformed");
        }
        List<?> admitted = (List<?>) response.records.invocation.get("source_refs");
        if (!admitted.contains(response.recovery.sourceRef)) {
            throw new EngineProtocolError("recovery source_ref is not admitted by invocation");
        }
        if (source.sourceRef() != null && !source.sourceRef().equals(response.recovery.sourceRef)) {
            throw new EngineProtocolError("LeanCTX instance source_ref differs from requested binding");
        }
        if (source.sourceDigest() != null
                && !source.sourceDigest().equals(response.recovery.sourceDigest)) {
            throw new EngineProtocolError("LeanCTX instance source_digest differs from requested binding");
        }
        ContextView result = buildView(source, response);
        ContextFailure failure = result.failure();
        if (InstanceStatus.REJECTED.value().equals(result.status())) {
            String detail = failure == null ? "rejected" : failure.code().value();
            if (failure != null && failure.code() == FailureCode.POLICY_REJECTED) {
                throw new PolicyAdmissionError("LeanCTX instance rejected request: " + detail, failure, result);
            }
            if (failure != null && failure.code() == FailureCode.SOURCE_UNAVAILABLE) {
                throw new SourceUnavailableError("LeanCTX instance rejected request: " + detail, failure, result);
            }
            throw new EngineRejected("LeanCTX instance rejected request: " + detail, failure, result);
        }
        if (InstanceStatus.FAILED.value().equals(result.status())) {
            String detail = failure == null ? "failed" : failure.code().value();
            if (failure != null && failure.code() == FailureCode.UNSUPPORTED_OPERATION) {
                throw new UnsupportedEngineError("LeanCTX instance execution failed: " + detail);
            }
            if (failure != null && failure.code() == FailureCode.SOURCE_INTEGRITY_MISMATCH) {
                throw new ArtifactIntegrityError("LeanCTX instance execution failed: " + detail, failure, result);
            }
            if (failure != null && failure.code() == FailureCode.SOURCE_UNAVAILABLE) {
                throw new SourceUnavailableError("LeanCTX instance execution failed: " + detail, failure, result);
            }
            throw new EngineExecutionError("LeanCTX instance execution failed: " + detail, failure, result);
        }
        return result;
    }

    @Override
    public RecoveredSource recover(String projectRoot, String path, String recoveryRef,
                                   String sourceRef, String sourceDigest) {
        Path root = validateRoot(projectRoot);
        String relativePath = safeRelativePath(path);
        String checkedRecovery = protocolRef(recoveryRef, "recovery_ref");
        String checkedSource = protocolRef(sourceRef, "source_ref");
        String checkedDigest;
        try {
            checkedDigest = Protocol.digest(sourceDigest, "source_digest");
        } catch (ValidationError exception) {
            throw new EngineProtocolError(exception.getMessage(), exception);
        }
        Map<String, Object> request = Map.of(
                "schema_version", LeanCtx.SCHEMA_VERSION,
                "transport_version", LeanCtx.TRANSPORT_VERSION,
                "engine_interface_version", LeanCtx.ENGINE_INTERFACE_VERSION,
                "path", relativePath,
                "recovery_ref", checkedRecovery,
                "source_ref", checkedSource,
                "source_digest", checkedDigest);
        EngineProtocol.ParsedResponse response = invoke("recover", root.toString(), request);
        if (response.records != null) {
            throw new EngineProtocolError("recover response must have null invocation/observation");
        }
        if (!checkedRecovery.equals(response.recovery.recoveryRef)
                || !checkedSource.equals(response.recovery.sourceRef)
                || !checkedDigest.equals(response.recovery.sourceDigest)) {
            throw new ArtifactIntegrityError("recover response binding mismatch");
        }
        if (!checkedDigest.equals(response.view.outputDigest)) {
            throw new ArtifactIntegrityError("recover output digest does not match source digest");
        }
        String expectedRef = "output:" + checkedDigest.substring("sha256:".length());
        if (response.view.outputRef != null && !expectedRef.equals(response.view.outputRef)) {
            throw new ArtifactIntegrityError("recover output reference does not match source digest");
        }
        try {
            return new RecoveredSource(response.view.text, checkedSource, checkedDigest, checkedRecovery);
        } catch (ValidationError exception) {
            throw new EngineProtocolError(exception.getMessage(), exception);
        }
    }

    private static String protocolRef(String value, String field) {
        try {
            return Protocol.ref(value, field);
        } catch (ValidationError exception) {
            throw new EngineProtocolError(exception.getMessage(), exception);
        }
    }

    private static String safeRelativePath(String value) {
        if (value == null || value.isEmpty()) {
            throw new EngineProtocolError("path must be a rooted relative path");
        }
        Json.validateUnicode(value, "path");
        if (value.getBytes(StandardCharsets.UTF_8).length > Protocol.MAX_PATH_BYTES
                || value.indexOf('\0') >= 0 || Path.of(value).isAbsolute()
                || value.codePoints().anyMatch(codePoint -> codePoint < 0x20)) {
            throw new EngineProtocolError("path must be a rooted relative path");
        }
        String normalized = Path.of(value).normalize().toString().replace('\\', '/');
        if (normalized.isEmpty() || normalized.equals(".") || normalized.equals("..")
                || normalized.startsWith("../")) {
            throw new EngineProtocolError("path escapes project root");
        }
        return normalized;
    }

    private Path validateRoot(String projectRoot) {
        if (projectRoot == null || projectRoot.isEmpty() || projectRoot.indexOf('\0') >= 0) {
            throw new SourceUnavailableError("project_root is unavailable");
        }
        try {
            Path root = Path.of(projectRoot).toAbsolutePath().normalize();
            if (root.toString().getBytes(StandardCharsets.UTF_8).length > Protocol.MAX_PATH_BYTES
                    || !Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS)) {
                throw new SourceUnavailableError("project_root is unavailable");
            }
            return root.toRealPath();
        } catch (SourceUnavailableError exception) {
            throw exception;
        } catch (IOException | RuntimeException exception) {
            throw new SourceUnavailableError("project_root is unavailable", null, null, exception);
        }
    }

    private Path resolveBinary() {
        Path candidate;
        if (!instanceBinary.contains("/") && !instanceBinary.contains("\\")) {
            String path = System.getenv("PATH");
            if (path == null || path.isEmpty()) {
                throw new EngineUnavailable("configured LeanCTX instance binary is unavailable");
            }
            candidate = null;
            for (String entry : path.split(java.util.regex.Pattern.quote(java.io.File.pathSeparator), -1)) {
                if (entry.isEmpty()) {
                    continue;
                }
                Path found = Path.of(entry).resolve(instanceBinary).toAbsolutePath().normalize();
                if (isExecutableFile(found)) {
                    candidate = found;
                    break;
                }
            }
            if (candidate == null) {
                throw new EngineUnavailable("configured LeanCTX instance binary is unavailable");
            }
        } else {
            candidate = Path.of(instanceBinary).toAbsolutePath().normalize();
            if (!isExecutableFile(candidate)) {
                throw new EngineUnavailable("configured LeanCTX instance binary is unavailable");
            }
        }
        try {
            return candidate.toRealPath();
        } catch (IOException exception) {
            throw new EngineUnavailable("configured LeanCTX instance binary is unavailable", exception);
        }
    }

    private static boolean isExecutableFile(Path path) {
        return Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && Files.isExecutable(path);
    }

    private EngineProtocol.ParsedResponse invoke(String operation, String projectRoot,
                                                 Map<String, Object> request) {
        byte[] payload = Json.canonicalBytes(request);
        if (payload.length > Protocol.MAX_REQUEST_BYTES) {
            throw new EngineProtocolError("LeanCTX instance request exceeds the bound");
        }
        return EngineProtocol.parseResponse(runPayload(operation, projectRoot, payload));
    }

    /** Write one request document to a private file under the root and run the operation. */
    byte[] runPayload(String operation, String projectRoot, byte[] payload) {
        Path root = validateRoot(projectRoot);
        Path directory = null;
        Path requestPath = null;
        try {
            directory = createPrivateDirectory(root, ".leanctx-sdk-");
            requestPath = directory.resolve("request.json");
            createPrivateFile(requestPath);
            try (FileChannel channel = FileChannel.open(requestPath,
                    java.nio.file.StandardOpenOption.WRITE)) {
                channel.write(ByteBuffer.wrap(payload));
                channel.force(true);
            }
            return run(operation, root, requestPath);
        } catch (InstanceError exception) {
            throw exception;
        } catch (IOException exception) {
            throw new EngineUnavailable("LeanCTX instance request could not be prepared", exception);
        } finally {
            deleteQuietly(requestPath);
            deleteQuietly(directory);
        }
    }

    private volatile SessionContainment lastSessionContainment = SessionContainment.UNAVAILABLE;

    /**
     * How the process tree of the most recent completed LeanCTX instance run was contained; see
     * {@link SessionContainment}. A run that was ended early by a deadline or an output
     * bound does not change it; with concurrent calls it is the state of whichever
     * finished last.
     */
    public SessionContainment lastSessionContainment() {
        return lastSessionContainment;
    }

    private byte[] run(String operation, Path projectRoot, Path requestPath) {
        Path binary = resolveBinary();
        ProcessBuilder builder = new ProcessBuilder(
                binary.toString(), "engine", operation,
                "--project-root", projectRoot.toString(),
                "--json-file", requestPath.toString());
        builder.directory(projectRoot.toFile());
        builder.redirectErrorStream(false);
        Map<String, String> environment = builder.environment();
        environment.clear();
        environment.put("LC_ALL", "C");
        environment.put("LANG", "C");
        environment.put("TZ", "UTC");
        environment.put("PYTHONHASHSEED", "0");
        SessionContainment.request(environment);
        Process process;
        try {
            process = builder.start();
        } catch (IOException | RuntimeException exception) {
            throw new EngineUnavailable("LeanCTX instance process could not be started", exception);
        }
        // Not try-with-resources: closing the executor waits, without any bound,
        // for the readers, and a reader stays blocked for as long as any process
        // that inherited the LeanCTX instance's pipes keeps them open.
        ExecutorService readers = java.util.concurrent.Executors.newVirtualThreadPerTaskExecutor();
        try {
            Future<byte[]> stdout = readers.submit(() -> readBounded(process.getInputStream(),
                    Protocol.MAX_RESPONSE_BYTES));
            Future<byte[]> stderr = readers.submit(() -> readBounded(process.getErrorStream(),
                    Protocol.MAX_STDERR_BYTES));
            long deadline = System.nanoTime() + Duration.ofNanos((long) (timeout * 1_000_000_000L)).toNanos();
            while (process.isAlive()) {
                if (stdout.isDone() && failed(stdout) || stderr.isDone() && failed(stderr)) {
                    terminate(process);
                    throw new EngineProtocolError("LeanCTX instance process output exceeds its bound");
                }
                long remaining = deadline - System.nanoTime();
                if (remaining <= 0) {
                    terminate(process);
                    throw new EngineTimeout("LeanCTX instance process exceeded its deadline");
                }
                try {
                    Thread.sleep(Math.min(10L, Math.max(1L,
                            TimeUnit.NANOSECONDS.toMillis(remaining))));
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    terminate(process);
                    throw new EngineTimeout("LeanCTX instance process was interrupted", exception);
                }
            }
            try {
                process.waitFor(2, TimeUnit.SECONDS);
                byte[] output = get(stdout, deadline);
                byte[] error = get(stderr, deadline);
                // The session starter's acknowledgement precedes everything the LeanCTX instance
                // wrote. It is recorded and is not output.
                int ack = SessionContainment.ackPrefixLength(output);
                if (ack > 0) output = java.util.Arrays.copyOfRange(output, ack, output.length);
                lastSessionContainment = ack > 0
                        ? SessionContainment.ENGINE_STARTER : SessionContainment.UNAVAILABLE;
                int exitCode = process.exitValue();
                if (exitCode != 0) {
                    throw mapExit(error);
                }
                if (output.length == 0) {
                    throw new EngineProtocolError("LeanCTX instance returned empty stdout");
                }
                return output;
            } catch (InstanceError exception) {
                throw exception;
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                terminate(process);
                throw new EngineTimeout("LeanCTX instance process was interrupted", exception);
            } catch (ExecutionException exception) {
                terminate(process);
                Throwable cause = exception.getCause();
                if (cause instanceof OutputLimitExceeded) {
                    throw new EngineProtocolError("LeanCTX instance process output exceeds its bound", cause);
                }
                throw new EngineProtocolError("LeanCTX instance process output could not be read", cause);
            } catch (java.util.concurrent.TimeoutException exception) {
                terminate(process);
                throw new EngineTimeout("LeanCTX instance process exceeded its deadline", exception);
            }
        } finally {
            try {
                if (process.isAlive()) {
                    terminate(process);
                }
            } finally {
                // Stop the readers without waiting for them, then release this
                // client's own pipe ends.
                readers.shutdownNow();
                closeQuietly(process.getOutputStream());
                closeDetached(process.getInputStream());
                closeDetached(process.getErrorStream());
            }
        }
    }

    private static boolean failed(Future<byte[]> future) {
        try {
            future.get();
            return false;
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            return true;
        } catch (ExecutionException exception) {
            return true;
        }
    }

    private static byte[] get(Future<byte[]> future, long deadline)
            throws InterruptedException, ExecutionException, java.util.concurrent.TimeoutException {
        long remaining = deadline - System.nanoTime();
        if (remaining <= 0) {
            throw new java.util.concurrent.TimeoutException();
        }
        return future.get(remaining, TimeUnit.NANOSECONDS);
    }

    private static byte[] readBounded(InputStream input, int maximum)
            throws IOException, OutputLimitExceeded {
        ByteArrayOutputStream output = new ByteArrayOutputStream(Math.min(maximum, 8192));
        byte[] buffer = new byte[8192];
        int total = 0;
        int count;
        while ((count = input.read(buffer)) >= 0) {
            total += count;
            if (total > maximum) {
                throw new OutputLimitExceeded();
            }
            output.write(buffer, 0, count);
        }
        return output.toByteArray();
    }

    private static InstanceError mapExit(byte[] stderr) {
        String text = new String(stderr, StandardCharsets.UTF_8);
        Matcher matcher = STDERR_CODE.matcher(text);
        String code = matcher.find() ? matcher.group(1) : null;
        if (Set.of("unsafe_root", "source_outside_root", "source_symlink",
                "policy_rejected").contains(code)) {
            return new PolicyAdmissionError("LeanCTX instance rejected request: " + code);
        }
        if ("source_unavailable".equals(code)) {
            return new SourceUnavailableError("LeanCTX instance source is unavailable");
        }
        if ("unsupported_mode".equals(code)) {
            return new UnsupportedEngineError("LeanCTX instance operation is unsupported");
        }
        return new EngineExecutionError("LeanCTX instance process failed: "
                + (code == null ? "nonzero_exit" : code));
    }

    private ContextView buildView(ContextSource source, EngineProtocol.ParsedResponse parsed) {
        if (parsed.records == null) {
            throw new EngineProtocolError("LeanCTX instance records are missing");
        }
        Map<String, Object> invocation = parsed.records.invocation;
        Map<String, Object> observation = parsed.records.observation;
        Object rawMeasurements = observation.get("measurements");
        if (!(rawMeasurements instanceof List<?> list)) {
            throw new EngineProtocolError("LeanCTX instance measurements are malformed");
        }
        List<ContextMeasurement> measurements = new ArrayList<>();
        for (Object item : list) {
            if (!(item instanceof ContextMeasurement measurement)) {
                throw new EngineProtocolError("LeanCTX instance measurements are malformed");
            }
            measurements.add(measurement);
        }
        Object rawFailure = observation.get("failure");
        Object rawReceipt = observation.get("receipt_link");
        if (rawFailure != null && !(rawFailure instanceof ContextFailure)
                || rawReceipt != null && !(rawReceipt instanceof ContextReceiptLink)) {
            throw new EngineProtocolError("LeanCTX instance observation records are malformed");
        }
        try {
            ContextView result = new ContextView(
                    source,
                    parsed.view.text,
                    parsed.view.outputRef,
                    parsed.view.outputDigest,
                    parsed.recovery.sourceRef,
                    parsed.recovery.sourceDigest,
                    parsed.recovery.recoveryRef,
                    (String) observation.get("status"),
                    measurements,
                    (ContextFailure) rawFailure,
                    (ContextReceiptLink) rawReceipt,
                    invocation,
                    observation);
            if (InstanceStatus.SUCCEEDED.value().equals(result.status()) && !result.verify()) {
                throw new ArtifactIntegrityError("succeeded LeanCTX instance evidence is not sealed");
            }
            return result;
        } catch (ValidationError exception) {
            throw new EngineProtocolError(exception.getMessage(), exception);
        }
    }

    private static Path createPrivateDirectory(Path root, String prefix) throws IOException {
        Path directory = Files.createTempDirectory(root, prefix, privateDirectoryAttribute());
        setPermissions(directory, true);
        return directory;
    }

    private static void createPrivateFile(Path path) throws IOException {
        Files.createFile(path, privateFileAttribute());
        setPermissions(path, false);
    }

    private static FileAttribute<Set<PosixFilePermission>> privateDirectoryAttribute() {
        return PosixFilePermissions.asFileAttribute(Set.of(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
                PosixFilePermission.OWNER_EXECUTE));
    }

    private static FileAttribute<Set<PosixFilePermission>> privateFileAttribute() {
        return PosixFilePermissions.asFileAttribute(Set.of(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE));
    }

    private static void setPermissions(Path path, boolean directory) {
        try {
            PosixFileAttributeView view = Files.getFileAttributeView(path,
                    PosixFileAttributeView.class, LinkOption.NOFOLLOW_LINKS);
            if (view != null) {
                view.setPermissions(directory ? Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.OWNER_WRITE,
                        PosixFilePermission.OWNER_EXECUTE) : Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.OWNER_WRITE));
            }
        } catch (IOException exception) {
            throw new EngineUnavailable("LeanCTX instance temporary state permissions could not be secured",
                    exception);
        }
    }

    private static void terminate(Process process) {
        ProcessTreeTermination.terminate(process);
    }

    private static void deleteQuietly(Path path) {
        if (path == null) {
            return;
        }
        try {
            Files.deleteIfExists(path);
        } catch (IOException ignored) {
            // Temporary state is best-effort after process termination; no path
            // outside the SDK-owned directory is ever traversed here.
        }
    }

    /**
     * Closes a pipe end without making the caller wait for it: closing a stream
     * another thread is still reading from can block until that read returns.
     */
    private static void closeDetached(InputStream input) {
        Thread.ofPlatform().daemon(true).name("leanctx-engine-close").start(() -> closeQuietly(input));
    }

    private static void closeQuietly(InputStream input) {
        try {
            input.close();
        } catch (IOException ignored) {
            // already closed
        }
    }

    private static void closeQuietly(java.io.OutputStream output) {
        try {
            output.close();
        } catch (IOException ignored) {
            // already closed
        }
    }

    static EngineProtocol.ParsedResponse parseResponse(byte[] raw) {
        return EngineProtocol.parseResponse(raw);
    }

    private static final class OutputLimitExceeded extends Exception {
        private static final long serialVersionUID = 1L;
    }
}
