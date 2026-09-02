package com.thinkery.leanctx;

import java.util.LinkedHashMap;
import java.util.Map;

/** Exact source bytes recovered from an Engine content-addressed binding. */
public final class RecoveredSource {
    private final String text;
    private final String sourceRef;
    private final String sourceDigest;
    private final String recoveryRef;

    public RecoveredSource(String text, String sourceRef, String sourceDigest,
                           String recoveryRef) {
        this.text = Protocol.text(text, "recovered text", Protocol.MAX_TEXT_BYTES, false);
        this.sourceRef = Protocol.ref(sourceRef, "source_ref");
        this.sourceDigest = Protocol.digest(sourceDigest, "source_digest");
        this.recoveryRef = Protocol.ref(recoveryRef, "recovery_ref");
        if (!Protocol.sha256Digest(text).equals(this.sourceDigest)) {
            throw new ValidationError("recovered text digest does not match source_digest");
        }
    }

    public String text() {
        return text;
    }

    public String getText() {
        return text;
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

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("text", text);
        result.put("source_ref", sourceRef);
        result.put("source_digest", sourceDigest);
        result.put("recovery_ref", recoveryRef);
        return Json.immutableMap(result);
    }

    public Map<String, Object> toDict() {
        return toMap();
    }

    public Map<String, Object> to_dict() {
        return toMap();
    }
}
