// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import java.nio.file.Path;
import java.nio.file.InvalidPathException;
import java.util.LinkedHashMap;
import java.util.Map;

/** Host-owned GitLab source selection for one Agent Tools session. */
public record GitLabSource(String host, long project, String namespace, Path glab, Path configDir) {
    private static final long MAX_SAFE_INTEGER = 9_007_199_254_740_991L;

    public GitLabSource {
        host = checkedText(host, "GitLab host");
        namespace = checkedText(namespace, "GitLab namespace");
        if (project < 1 || project > MAX_SAFE_INTEGER) {
            throw new ValidationError("GitLab project must be a positive safe integer");
        }
        glab = absolutePath(glab, "glab");
        if (configDir != null) {
            configDir = absolutePath(configDir, "config_dir");
        }
    }

    public GitLabSource(String host, long project, String namespace, Path glab) {
        this(host, project, namespace, glab, null);
    }

    Map<String, Object> policyValue() {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("glab", glab.toString());
        value.put("host", host);
        value.put("namespace", namespace);
        value.put("project", project);
        if (configDir != null) {
            value.put("config_dir", configDir.toString());
        }
        return value;
    }

    private static String checkedText(String value, String name) {
        if (value == null || value.isBlank()
                || value.codePoints().anyMatch(Character::isISOControl)) {
            throw new ValidationError(name + " must be a non-empty string without control characters");
        }
        return value;
    }

    private static Path absolutePath(Path path, String name) {
        if (path == null || path.toString().isBlank()
                || path.toString().codePoints().anyMatch(Character::isISOControl)) {
            throw new ValidationError(name + " must be an absolute path without control characters");
        }
        try {
            Path copy = Path.of(path.toString());
            if (!copy.isAbsolute()) {
                throw new ValidationError(name + " must be an absolute path without control characters");
            }
            return copy;
        } catch (InvalidPathException exception) {
            throw new ValidationError(name + " must be an absolute path without control characters");
        }
    }
}
