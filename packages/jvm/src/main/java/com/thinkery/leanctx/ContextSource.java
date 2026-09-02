package com.thinkery.leanctx;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** A bounded, project-contained source descriptor. */
public final class ContextSource {
    private final String path;
    private final String projectRoot;
    private final String mediaType;
    private final String sourceRef;
    private final String sourceDigest;

    public ContextSource(String path) {
        this(path, Path.of(System.getProperty("user.dir")).toString());
    }

    public ContextSource(String path, String projectRoot) {
        this(path, projectRoot, "text/plain", null, null);
    }

    public ContextSource(String path, Path projectRoot) {
        this(path, projectRoot == null ? null : projectRoot.toString());
    }

    public ContextSource(String path, String projectRoot, String mediaType,
                         String sourceRef, String sourceDigest) {
        String suppliedPath = Protocol.text(path, "path", Protocol.MAX_PATH_BYTES);
        Path root = Protocol.absolutePath(projectRoot, "project_root");
        if (root.toString().getBytes(StandardCharsets.UTF_8).length > Protocol.MAX_PATH_BYTES) {
            throw new ValidationError("project_root exceeds the path bound");
        }
        Path candidate = Path.of(suppliedPath).isAbsolute()
                ? Path.of(suppliedPath).toAbsolutePath().normalize()
                : root.resolve(suppliedPath).normalize();
        if (!Protocol.contained(candidate, root)) {
            throw new ValidationError("source path escapes project_root");
        }
        String storedPath = Path.of(suppliedPath).isAbsolute()
                ? candidate.toString()
                : Path.of(suppliedPath).normalize().toString().replace('\\', '/');
        if (candidate.toString().getBytes(StandardCharsets.UTF_8).length > Protocol.MAX_PATH_BYTES) {
            throw new ValidationError("path exceeds the path bound");
        }
        String checkedMediaType = Protocol.text(
                mediaType == null ? "text/plain" : mediaType,
                "media_type", Protocol.MAX_REF_BYTES);
        String checkedSourceRef = sourceRef == null ? null : Protocol.ref(sourceRef, "source_ref");
        String checkedSourceDigest = sourceDigest == null
                ? null : Protocol.digest(sourceDigest, "source_digest");
        this.path = storedPath;
        this.projectRoot = root.toString();
        this.mediaType = checkedMediaType;
        this.sourceRef = checkedSourceRef;
        this.sourceDigest = checkedSourceDigest;
    }

    public String path() {
        return path;
    }

    public String getPath() {
        return path;
    }

    public String projectRoot() {
        return projectRoot;
    }

    public String getProjectRoot() {
        return projectRoot;
    }

    public String mediaType() {
        return mediaType;
    }

    public String getMediaType() {
        return mediaType;
    }

    public String sourceRef() {
        return sourceRef;
    }

    public String getSourceRef() {
        return sourceRef;
    }

    public String sourceDigest() {
        return sourceDigest;
    }

    public String getSourceDigest() {
        return sourceDigest;
    }

    public String relativePath() {
        Path root = Path.of(projectRoot).toAbsolutePath().normalize();
        Path absolute = root.resolve(path).normalize();
        if (!Protocol.contained(absolute, root)) {
            throw new ValidationError("source containment cannot be proven");
        }
        String relative = root.relativize(absolute).toString().replace('\\', '/');
        if (relative.isEmpty() || relative.equals(".") || relative.equals("..")
                || relative.startsWith("../")
                || relative.codePoints().anyMatch(codePoint -> codePoint < 0x20)) {
            throw new ValidationError("source path must be a rooted relative file path");
        }
        return relative;
    }

    public String getRelativePath() {
        return relativePath();
    }

    public String relative_path() {
        return relativePath();
    }

    public Map<String, Object> descriptor() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("path", relativePath());
        result.put("media_type", mediaType);
        if (sourceRef != null) {
            result.put("source_ref", sourceRef);
        }
        if (sourceDigest != null) {
            result.put("source_digest", sourceDigest);
        }
        return Json.immutableMap(result);
    }

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>(descriptor());
        result.put("project_root", projectRoot);
        return Json.immutableMap(result);
    }

    public Map<String, Object> toDict() {
        return toMap();
    }

    public Map<String, Object> to_dict() {
        return toMap();
    }
}
