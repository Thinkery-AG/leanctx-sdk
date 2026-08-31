package com.thinkery.leanctx;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Immutable result envelope returned by an Agent Tool. */
public final class ToolResult {
    private final String tool;
    private final String text;
    private final List<Map<String, Object>> contentBlocks;
    private final long originalTokens;
    private final long outputTokens;
    private final long savedTokens;
    private final String mode;
    private final boolean changed;
    private final Map<String, Object> shell;

    public ToolResult(String tool, String text, List<? extends Map<String, ?>> contentBlocks,
                      long originalTokens, long outputTokens, long savedTokens,
                      String mode, boolean changed, Map<String, ?> shell) {
        if (tool == null || tool.isEmpty()) {
            throw new ValidationError("tool must be a non-empty string");
        }
        this.tool = tool;
        this.text = Protocol.boundedNullableText(text, "text", Protocol.MAX_TEXT_BYTES);
        this.contentBlocks = immutableBlocks(contentBlocks);
        this.originalTokens = nonNegative(originalTokens, "original_tokens");
        this.outputTokens = nonNegative(outputTokens, "output_tokens");
        this.savedTokens = nonNegative(savedTokens, "saved_tokens");
        if (outputTokens + savedTokens != originalTokens) {
            throw new ValidationError("saved_tokens must equal original_tokens - output_tokens");
        }
        if (mode != null) {
            this.mode = Protocol.text(mode, "mode", Protocol.MAX_REF_BYTES);
        } else {
            this.mode = null;
        }
        this.changed = changed;
        this.shell = shell == null ? null : Json.immutableMap(shell);
    }

    public ToolResult(String tool, Map<String, ?> value) {
        this(tool,
                requireString(value, "text"),
                requireBlocks(value),
                requireMetric(value, "original_tokens"),
                requireMetric(value, "output_tokens"),
                requireMetric(value, "saved_tokens"),
                optionalString(value, "mode"),
                requireBoolean(value, "changed"),
                optionalMap(value, "shell"));
        validateWireKeys(value);
    }

    public String tool() {
        return tool;
    }

    public String getTool() {
        return tool;
    }

    public String text() {
        return text;
    }

    public String getText() {
        return text;
    }

    public List<Map<String, Object>> contentBlocks() {
        return contentBlocks;
    }

    public List<Map<String, Object>> getContentBlocks() {
        return contentBlocks;
    }

    public List<Map<String, Object>> content_blocks() {
        return contentBlocks;
    }

    public long originalTokens() {
        return originalTokens;
    }

    public long getOriginalTokens() {
        return originalTokens;
    }

    public long original_tokens() {
        return originalTokens;
    }

    public long outputTokens() {
        return outputTokens;
    }

    public long getOutputTokens() {
        return outputTokens;
    }

    public long output_tokens() {
        return outputTokens;
    }

    public long savedTokens() {
        return savedTokens;
    }

    public long getSavedTokens() {
        return savedTokens;
    }

    public long saved_tokens() {
        return savedTokens;
    }

    public double savedRatio() {
        return originalTokens == 0 ? 0.0 : (double) savedTokens / originalTokens;
    }

    public double getSavedRatio() {
        return savedRatio();
    }

    public double saved_ratio() {
        return savedRatio();
    }

    public String mode() {
        return mode;
    }

    public String getMode() {
        return mode;
    }

    public boolean changed() {
        return changed;
    }

    public boolean isChanged() {
        return changed;
    }

    public Map<String, Object> shell() {
        return shell;
    }

    public Map<String, Object> getShell() {
        return shell;
    }

    private static List<Map<String, Object>> immutableBlocks(
            List<? extends Map<String, ?>> values) {
        if (values == null) {
            throw new ValidationError("content_blocks must be a list");
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, ?> value : values) {
            if (value == null) {
                throw new ValidationError("content_blocks must contain objects");
            }
            result.add(Json.immutableMap(value));
        }
        return Collections.unmodifiableList(result);
    }

    private static void validateWireKeys(Map<String, ?> value) {
        if (value == null || !value.keySet().equals(
                java.util.Set.of("text", "content_blocks", "original_tokens",
                        "output_tokens", "saved_tokens", "mode", "changed", "shell"))) {
            throw new EngineProtocolError("Agent Tools result fields are invalid");
        }
    }

    private static String requireString(Map<String, ?> value, String field) {
        Object item = value == null ? null : value.get(field);
        if (!(item instanceof String string)) {
            throw new EngineProtocolError(field + " must be a string");
        }
        return string;
    }

    private static String optionalString(Map<String, ?> value, String field) {
        Object item = value == null ? null : value.get(field);
        if (item != null && !(item instanceof String)) {
            throw new EngineProtocolError(field + " must be a string or null");
        }
        return (String) item;
    }

    private static long requireMetric(Map<String, ?> value, String field) {
        return Json.integer(value == null ? null : value.get(field), field);
    }

    private static boolean requireBoolean(Map<String, ?> value, String field) {
        Object item = value == null ? null : value.get(field);
        if (!(item instanceof Boolean bool)) {
            throw new EngineProtocolError(field + " must be boolean");
        }
        return bool;
    }

    private static Map<String, ?> optionalMap(Map<String, ?> value, String field) {
        Object item = value == null ? null : value.get(field);
        if (item == null) {
            return null;
        }
        if (!(item instanceof Map<?, ?> map)) {
            throw new EngineProtocolError(field + " must be object or null");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new EngineProtocolError(field + " keys must be strings");
            }
            result.put(key, entry.getValue());
        }
        return result;
    }

    private static List<Map<String, ?>> requireBlocks(Map<String, ?> value) {
        Object item = value == null ? null : value.get("content_blocks");
        if (!(item instanceof List<?> list)) {
            throw new EngineProtocolError("content_blocks must be a list");
        }
        List<Map<String, ?>> result = new ArrayList<>();
        for (Object block : list) {
            if (!(block instanceof Map<?, ?> map)) {
                throw new EngineProtocolError("content_blocks must contain objects");
            }
            Map<String, Object> typed = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String key)) {
                    throw new EngineProtocolError("content_blocks keys must be strings");
                }
                typed.put(key, entry.getValue());
            }
            result.add(typed);
        }
        return result;
    }

    private static long nonNegative(long value, String field) {
        if (value < 0 || value > Json.MAX_SAFE_INTEGER) {
            throw new ValidationError(field + " must be a non-negative safe integer");
        }
        return value;
    }
}
