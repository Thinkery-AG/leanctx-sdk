package com.thinkery.leanctx;

import java.util.LinkedHashMap;
import java.util.Map;

/** Typed Engine failure evidence. */
public final class ContextFailure {
    private final FailureCode code;
    private final boolean retryableByHost;
    private final String recoveryRef;

    public ContextFailure(FailureCode code, boolean retryableByHost, String recoveryRef) {
        if (code == null) {
            throw new ValidationError("invalid failure code");
        }
        if (recoveryRef != null) {
            Protocol.ref(recoveryRef, "recovery_ref");
        }
        this.code = code;
        this.retryableByHost = retryableByHost;
        this.recoveryRef = recoveryRef;
    }

    public ContextFailure(String code, boolean retryableByHost, String recoveryRef) {
        this(FailureCode.fromValue(code), retryableByHost, recoveryRef);
    }

    public FailureCode code() {
        return code;
    }

    public FailureCode getCode() {
        return code;
    }

    public boolean retryableByHost() {
        return retryableByHost;
    }

    public boolean isRetryableByHost() {
        return retryableByHost;
    }

    public boolean retryable_by_host() {
        return retryableByHost;
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
        result.put("code", code.value());
        result.put("retryable_by_host", retryableByHost);
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
