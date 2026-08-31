package com.thinkery.leanctx;

import java.util.LinkedHashMap;
import java.util.Map;

/** Receipt digest and invocation binding emitted by the Engine. */
public final class ContextReceiptLink {
    private final int schemaVersion;
    private final String receiptId;
    private final String receiptRef;
    private final String receiptDigest;
    private final String invocationId;

    public ContextReceiptLink(int schemaVersion, String receiptId, String receiptRef,
                              String receiptDigest, String invocationId) {
        if (schemaVersion != LeanCtx.SCHEMA_VERSION) {
            throw new ValidationError("receipt link schema_version must be 1");
        }
        String checkedId = Protocol.ref(receiptId, "receipt_id");
        String checkedRef = Protocol.ref(receiptRef, "receipt_ref");
        String checkedDigest = Protocol.digest(receiptDigest, "receipt_digest");
        String checkedInvocation = Protocol.text(invocationId, "invocation_id", Protocol.MAX_REF_BYTES);
        if (!checkedRef.equals("receipt:" + checkedDigest)) {
            throw new ValidationError("receipt_ref does not match receipt_digest");
        }
        this.schemaVersion = schemaVersion;
        this.receiptId = checkedId;
        this.receiptRef = checkedRef;
        this.receiptDigest = checkedDigest;
        this.invocationId = checkedInvocation;
    }

    public int schemaVersion() {
        return schemaVersion;
    }

    public int getSchemaVersion() {
        return schemaVersion;
    }

    public int schema_version() {
        return schemaVersion;
    }

    public String receiptId() {
        return receiptId;
    }

    public String getReceiptId() {
        return receiptId;
    }

    public String receipt_id() {
        return receiptId;
    }

    public String receiptRef() {
        return receiptRef;
    }

    public String getReceiptRef() {
        return receiptRef;
    }

    public String receipt_ref() {
        return receiptRef;
    }

    public String receiptDigest() {
        return receiptDigest;
    }

    public String getReceiptDigest() {
        return receiptDigest;
    }

    public String receipt_digest() {
        return receiptDigest;
    }

    public String invocationId() {
        return invocationId;
    }

    public String getInvocationId() {
        return invocationId;
    }

    public String invocation_id() {
        return invocationId;
    }

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", schemaVersion);
        result.put("receipt_id", receiptId);
        result.put("receipt_ref", receiptRef);
        result.put("receipt_digest", receiptDigest);
        result.put("invocation_id", invocationId);
        return Json.immutableMap(result);
    }

    public Map<String, Object> toDict() {
        return toMap();
    }

    public Map<String, Object> to_dict() {
        return toMap();
    }
}
