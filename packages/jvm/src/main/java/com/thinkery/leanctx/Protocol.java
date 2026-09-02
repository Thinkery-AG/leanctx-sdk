package com.thinkery.leanctx;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.regex.Pattern;

/** Internal v1 validation and digest helpers. */
final class Protocol {
    static final int MAX_REQUEST_BYTES = 64 * 1024;
    static final int MAX_PATH_BYTES = 4096;
    static final int MAX_REF_BYTES = 512;
    static final int MAX_TASK_BYTES = 16 * 1024;
    static final int MAX_TEXT_BYTES = 8 * 1024 * 1024;
    static final int MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
    static final int MAX_STDERR_BYTES = 64 * 1024;
    static final int MAX_REFS = 32;
    static final int MAX_MEASUREMENTS = 32;

    static final Pattern DIGEST = Pattern.compile("sha256:[0-9a-f]{64}");
    static final Pattern OUTPUT_REF = Pattern.compile("output:[0-9a-f]{64}");
    static final Pattern PLAN_REF = Pattern.compile("plan:sha256:[0-9a-f]{64}");
    static final Pattern ASCII_NAME = Pattern.compile("[a-z0-9_]+");
    static final Pattern SEMVER = Pattern.compile("[0-9]+\\.[0-9]+\\.[0-9]+");

    private Protocol() {
    }

    static String text(String value, String field, int maximumBytes) {
        return text(value, field, maximumBytes, true);
    }

    static String text(String value, String field, int maximumBytes, boolean controls) {
        if (value == null) {
            throw new ValidationError(field + " must be a string");
        }
        Json.validateUnicode(value, field);
        int bytes = value.getBytes(StandardCharsets.UTF_8).length;
        if (bytes == 0) {
            throw new ValidationError(field + " must not be empty");
        }
        if (bytes > maximumBytes) {
            throw new ValidationError(field + " exceeds " + maximumBytes + " UTF-8 bytes");
        }
        if (value.indexOf('\0') >= 0) {
            throw new ValidationError(field + " contains NUL");
        }
        if (controls && value.codePoints().anyMatch(codePoint -> codePoint < 0x20)) {
            throw new ValidationError(field + " contains a control character");
        }
        return value;
    }

    static String boundedNullableText(String value, String field, int maximumBytes) {
        if (value == null) {
            return null;
        }
        Json.validateUnicode(value, field);
        if (value.getBytes(StandardCharsets.UTF_8).length > maximumBytes
                || value.indexOf('\0') >= 0) {
            throw new ValidationError(field + " violates its bound");
        }
        return value;
    }

    static String ref(String value, String field) {
        Json.utf8(value, field);
        if (value.isEmpty() || value.getBytes(StandardCharsets.UTF_8).length > MAX_REF_BYTES
                || value.codePoints().anyMatch(codePoint -> codePoint < 0x20 || codePoint > 0x7e)) {
            throw new ValidationError(field + " must be 1.." + MAX_REF_BYTES
                    + " printable ASCII bytes");
        }
        return value;
    }

    static String digest(String value, String field) {
        String checked = ref(value, field);
        if (!DIGEST.matcher(checked).matches()) {
            throw new ValidationError(field + " must be sha256:<64 lowercase hex>");
        }
        return checked;
    }

    static String outputRef(String value, String field) {
        String checked = ref(value, field);
        if (!OUTPUT_REF.matcher(checked).matches()) {
            throw new ValidationError(field + " must be output:<64 lowercase hex>");
        }
        return checked;
    }

    static String planRef(String value) {
        String checked = ref(value, "plan_id");
        if (!PLAN_REF.matcher(checked).matches()) {
            throw new ValidationError("plan_id must be a deterministic plan reference");
        }
        return checked;
    }

    static String sha256Digest(String value) {
        return sha256Digest(value.getBytes(StandardCharsets.UTF_8));
    }

    static String sha256Digest(byte[] value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value);
            return "sha256:" + HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new AssertionError("JDK lacks SHA-256", exception);
        }
    }

    static String canonicalPlanId(Object intent) {
        return "plan:sha256:" + sha256Hex(Json.canonicalBytes(intent));
    }

    static String sha256Hex(byte[] value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException exception) {
            throw new AssertionError("JDK lacks SHA-256", exception);
        }
    }

    static Path absolutePath(String value, String field) {
        text(value, field, MAX_PATH_BYTES, false);
        return Path.of(value).toAbsolutePath().normalize();
    }

    static boolean contained(Path candidate, Path root) {
        return candidate.equals(root) || candidate.startsWith(root);
    }

    static void checkStatus(String status) {
        for (EngineStatus item : EngineStatus.values()) {
            if (item.value().equals(status)) {
                return;
            }
        }
        throw new ValidationError("invalid Engine observation status");
    }

    static boolean isSuccess(String status) {
        return EngineStatus.SUCCEEDED.value().equals(status)
                || EngineStatus.DEGRADED.value().equals(status);
    }
}
