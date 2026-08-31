package com.thinkery.leanctx;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Engine-shaped context view with explicit source and receipt bindings. */
public final class ContextView {
    private final ContextSource source;
    private final String text;
    private final String outputRef;
    private final String outputDigest;
    private final String sourceRef;
    private final String sourceDigest;
    private final String recoveryRef;
    private final String status;
    private final List<ContextMeasurement> measurements;
    private final ContextFailure failure;
    private final ContextReceiptLink receiptLink;
    private final Map<String, Object> invocation;
    private final Map<String, Object> observation;
    private final int schemaVersion;
    private final int transportVersion;
    private final String engineInterfaceVersion;

    public ContextView(ContextSource source, String text, String outputRef, String outputDigest,
                       String sourceRef, String sourceDigest, String recoveryRef,
                       String status) {
        this(source, text, outputRef, outputDigest, sourceRef, sourceDigest, recoveryRef,
                status, List.of(), null, null, Map.of(), Map.of());
    }

    public ContextView(ContextSource source, String text, String outputRef, String outputDigest,
                       String sourceRef, String sourceDigest, String recoveryRef,
                       String status, List<ContextMeasurement> measurements,
                       ContextFailure failure, ContextReceiptLink receiptLink,
                       Map<String, ?> invocation, Map<String, ?> observation) {
        this(source, text, outputRef, outputDigest, sourceRef, sourceDigest, recoveryRef,
                status, measurements, failure, receiptLink, invocation, observation,
                LeanCtx.SCHEMA_VERSION, LeanCtx.TRANSPORT_VERSION,
                LeanCtx.ENGINE_INTERFACE_VERSION);
    }

    public ContextView(ContextSource source, String text, String outputRef, String outputDigest,
                       String sourceRef, String sourceDigest, String recoveryRef,
                       String status, List<ContextMeasurement> measurements,
                       ContextFailure failure, ContextReceiptLink receiptLink,
                       Map<String, ?> invocation, Map<String, ?> observation,
                       int schemaVersion, int transportVersion,
                       String engineInterfaceVersion) {
        if (source == null) {
            throw new ValidationError("view source must be ContextSource");
        }
        Protocol.boundedNullableText(text, "view text", Protocol.MAX_TEXT_BYTES);
        String checkedOutputRef = outputRef == null ? null : Protocol.outputRef(outputRef, "output_ref");
        String checkedOutputDigest = outputDigest == null
                ? null : Protocol.digest(outputDigest, "output_digest");
        if ((checkedOutputRef == null) != (checkedOutputDigest == null)) {
            throw new ValidationError("output_ref and output_digest must be paired");
        }
        if (checkedOutputDigest != null && text != null) {
            if (!Protocol.sha256Digest(text).equals(checkedOutputDigest)) {
                throw new ValidationError("view output digest mismatch");
            }
            if (!checkedOutputRef.equals("output:"
                    + checkedOutputDigest.substring("sha256:".length()))) {
                throw new ValidationError("view output reference mismatch");
            }
        }
        String checkedSourceRef = Protocol.ref(sourceRef, "source_ref");
        String checkedSourceDigest = Protocol.digest(sourceDigest, "source_digest");
        String checkedRecoveryRef = recoveryRef == null
                ? null : Protocol.ref(recoveryRef, "recovery_ref");
        Protocol.checkStatus(status);
        if (measurements == null || measurements.size() > Protocol.MAX_MEASUREMENTS
                || measurements.stream().anyMatch(item -> item == null)) {
            throw new ValidationError("invalid measurements");
        }
        if (failure != null && !(failure instanceof ContextFailure)) {
            throw new ValidationError("failure must be ContextFailure");
        }
        if (receiptLink != null && !(receiptLink instanceof ContextReceiptLink)) {
            throw new ValidationError("receipt_link must be ContextReceiptLink");
        }
        if (invocation == null || observation == null) {
            throw new ValidationError("invocation and observation must be mappings");
        }
        if (schemaVersion != LeanCtx.SCHEMA_VERSION) {
            throw new ValidationError("view schema_version must be 1");
        }
        if (transportVersion != LeanCtx.TRANSPORT_VERSION) {
            throw new ValidationError("view transport_version must be integer 1");
        }
        if (!LeanCtx.ENGINE_INTERFACE_VERSION.equals(engineInterfaceVersion)) {
            throw new ValidationError("unsupported Engine Interface version");
        }
        this.source = source;
        this.text = text;
        this.outputRef = checkedOutputRef;
        this.outputDigest = checkedOutputDigest;
        this.sourceRef = checkedSourceRef;
        this.sourceDigest = checkedSourceDigest;
        this.recoveryRef = checkedRecoveryRef;
        this.status = status;
        this.measurements = Collections.unmodifiableList(new ArrayList<>(measurements));
        this.failure = failure;
        this.receiptLink = receiptLink;
        this.invocation = Json.immutableMapPreserving(invocation);
        this.observation = Json.immutableMapPreserving(observation);
        this.schemaVersion = schemaVersion;
        this.transportVersion = transportVersion;
        this.engineInterfaceVersion = engineInterfaceVersion;
    }

    public ContextSource source() {
        return source;
    }

    public ContextSource getSource() {
        return source;
    }

    public String text() {
        return text;
    }

    public String getText() {
        return text;
    }

    public String outputRef() {
        return outputRef;
    }

    public String getOutputRef() {
        return outputRef;
    }

    public String output_ref() {
        return outputRef;
    }

    public String outputDigest() {
        return outputDigest;
    }

    public String getOutputDigest() {
        return outputDigest;
    }

    public String output_digest() {
        return outputDigest;
    }

    public String sourceRef() {
        return sourceRef;
    }

    public String getSourceRef() {
        return sourceRef;
    }

    public String source_ref() {
        return sourceRef;
    }

    public String sourceDigest() {
        return sourceDigest;
    }

    public String getSourceDigest() {
        return sourceDigest;
    }

    public String source_digest() {
        return sourceDigest;
    }

    public String recoveryRef() {
        return recoveryRef;
    }

    public String getRecoveryRef() {
        return recoveryRef;
    }

    public String recovery_ref() {
        return recoveryRef;
    }

    public String status() {
        return status;
    }

    public String getStatus() {
        return status;
    }

    public List<ContextMeasurement> measurements() {
        return measurements;
    }

    public List<ContextMeasurement> getMeasurements() {
        return measurements;
    }

    public ContextFailure failure() {
        return failure;
    }

    public ContextFailure getFailure() {
        return failure;
    }

    public ContextReceiptLink receiptLink() {
        return receiptLink;
    }

    public ContextReceiptLink getReceiptLink() {
        return receiptLink;
    }

    public Map<String, Object> invocation() {
        return invocation;
    }

    public Map<String, Object> getInvocation() {
        return invocation;
    }

    public Map<String, Object> observation() {
        return observation;
    }

    public Map<String, Object> getObservation() {
        return observation;
    }

    public int schemaVersion() {
        return schemaVersion;
    }

    public int getSchemaVersion() {
        return schemaVersion;
    }

    public int transportVersion() {
        return transportVersion;
    }

    public int getTransportVersion() {
        return transportVersion;
    }

    public String engineInterfaceVersion() {
        return engineInterfaceVersion;
    }

    public String getEngineInterfaceVersion() {
        return engineInterfaceVersion;
    }

    public Integrity integrityStatus() {
        return verify() ? Integrity.SEALED : Integrity.UNSEALED;
    }

    public Integrity getIntegrityStatus() {
        return integrityStatus();
    }

    public Integrity integrity_status() {
        return integrityStatus();
    }

    public String inputRef() {
        Object value = invocation.get("input_ref");
        return value instanceof String string ? string : null;
    }

    public String getInputRef() {
        return inputRef();
    }

    public String input_ref() {
        return inputRef();
    }

    public String invocationId() {
        Object value = invocation.get("invocation_id");
        return value instanceof String string ? string : null;
    }

    public String getInvocationId() {
        return invocationId();
    }

    public String invocation_id() {
        return invocationId();
    }

    public String engineVersion() {
        Object value = invocation.get("engine");
        if (value instanceof Map<?, ?> map && map.get("engine_version") instanceof String string) {
            return string;
        }
        return null;
    }

    public String getEngineVersion() {
        return engineVersion();
    }

    public String capabilityVersion() {
        Object value = invocation.get("operation");
        if (value instanceof Map<?, ?> map && map.get("capability_version") instanceof String string) {
            return string;
        }
        return null;
    }

    public String getCapabilityVersion() {
        return capabilityVersion();
    }

    public String requireText() {
        if (text == null) {
            throw new EngineExecutionError("Engine view has no text", null, this);
        }
        return text;
    }

    public String require_text() {
        return requireText();
    }

    public Map<String, String> recoveryBinding() {
        if (recoveryRef == null) {
            throw new ValidationError("view has no recovery binding");
        }
        Map<String, String> result = new LinkedHashMap<>();
        result.put("recovery_ref", recoveryRef);
        result.put("source_ref", sourceRef);
        result.put("source_digest", sourceDigest);
        return Collections.unmodifiableMap(result);
    }

    public Map<String, String> recovery_binding() {
        return recoveryBinding();
    }

    public boolean verify() {
        try {
            if (!Protocol.isSuccess(status) || recoveryRef == null || outputRef == null
                    || outputDigest == null || text == null) {
                return false;
            }
            Object refs = invocation.get("source_refs");
            if (!(refs instanceof List<?> list) || !list.contains(sourceRef)) {
                return false;
            }
            if (!java.util.Objects.equals(observation.get("invocation_id"), invocationId())
                    || !java.util.Objects.equals(observation.get("output_digest"), outputDigest)
                    || !java.util.Objects.equals(observation.get("output_ref"), outputRef)) {
                return false;
            }
            return receiptLink != null
                    && java.util.Objects.equals(receiptLink.invocationId(), invocationId());
        } catch (RuntimeException exception) {
            return false;
        }
    }

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", schemaVersion);
        result.put("transport_version", transportVersion);
        result.put("engine_interface_version", engineInterfaceVersion);
        result.put("source", source.toMap());
        result.put("text", text);
        result.put("output_ref", outputRef);
        result.put("output_digest", outputDigest);
        result.put("source_ref", sourceRef);
        result.put("source_digest", sourceDigest);
        result.put("recovery_ref", recoveryRef);
        result.put("status", status);
        result.put("measurements", measurements);
        result.put("failure", failure);
        result.put("receipt_link", receiptLink);
        result.put("invocation", invocation);
        result.put("observation", observation);
        return Json.immutableMap(result);
    }

    public Map<String, Object> toDict() {
        return toMap();
    }

    public Map<String, Object> to_dict() {
        return toMap();
    }
}
