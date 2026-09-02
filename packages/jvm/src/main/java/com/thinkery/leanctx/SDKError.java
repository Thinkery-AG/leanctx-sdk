package com.thinkery.leanctx;

import java.util.LinkedHashMap;
import java.util.Map;

/** Base class for stable, non-secret SDK failures. */
public class SDKError extends RuntimeException {
    private final String code;
    private final String guidance;
    private boolean retryable;
    private final boolean degradeAllowed;
    private final boolean abortRequired;
    private final boolean configurationFix;
    private final boolean versionChange;

    public SDKError() {
        this(null);
    }

    public SDKError(String message) {
        this(message, null, "sdk_error",
                "preserve original evidence and classify the concrete error",
                false, false, true, false, false);
    }

    public SDKError(String message, Throwable cause) {
        this(message, cause, "sdk_error",
                "preserve original evidence and classify the concrete error",
                false, false, true, false, false);
    }

    protected SDKError(String message, Throwable cause, String code, String guidance,
                       boolean retryable, boolean degradeAllowed, boolean abortRequired,
                       boolean configurationFix, boolean versionChange) {
        super(message == null || message.isEmpty() ? code : message, cause);
        this.code = code;
        this.guidance = guidance;
        this.retryable = retryable;
        this.degradeAllowed = degradeAllowed;
        this.abortRequired = abortRequired;
        this.configurationFix = configurationFix;
        this.versionChange = versionChange;
    }

    public final String code() {
        return code;
    }

    public final String getCode() {
        return code;
    }

    public final String guidance() {
        return guidance;
    }

    public final String getGuidance() {
        return guidance;
    }

    public final boolean retryable() {
        return retryable;
    }

    public final boolean isRetryable() {
        return retryable;
    }

    public final boolean degradeAllowed() {
        return degradeAllowed;
    }

    public final boolean isDegradeAllowed() {
        return degradeAllowed;
    }

    public final boolean abortRequired() {
        return abortRequired;
    }

    public final boolean isAbortRequired() {
        return abortRequired;
    }

    public final boolean configurationFix() {
        return configurationFix;
    }

    public final boolean isConfigurationFix() {
        return configurationFix;
    }

    public final boolean versionChange() {
        return versionChange;
    }

    public final boolean isVersionChange() {
        return versionChange;
    }

    protected final void setRetryable(boolean value) {
        retryable = value;
    }

    /** Stable host guidance; exception messages are deliberately excluded. */
    public Map<String, Object> asMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("abort_required", abortRequired);
        result.put("code", code);
        result.put("configuration_fix", configurationFix);
        result.put("degrade_allowed", degradeAllowed);
        result.put("guidance", guidance);
        result.put("retryable", retryable);
        result.put("version_change", versionChange);
        return Map.copyOf(result);
    }

    public Map<String, Object> asDict() {
        return asMap();
    }
}
