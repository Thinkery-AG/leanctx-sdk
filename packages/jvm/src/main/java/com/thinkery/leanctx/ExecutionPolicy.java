package com.thinkery.leanctx;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TreeSet;

/** Immutable, fail-closed policy for structured Agent Tools execution. */
public final class ExecutionPolicy {
    private static final Set<String> FORBIDDEN_ENV = Set.of(
            "COMSPEC", "DYLD_INSERT_LIBRARIES", "HOME", "LD_PRELOAD", "PATH",
            "PATHEXT", "PYTHONPATH", "RUSTC_WRAPPER", "SHELL");
    private final double maxTimeout;
    private final List<String> allowedExecutables;
    private final List<String> allowedEnv;

    public ExecutionPolicy() {
        this(30.0, List.of(), List.of());
    }

    public ExecutionPolicy(double maxTimeout) {
        this(maxTimeout, List.of(), List.of());
    }

    public ExecutionPolicy(double maxTimeout, List<String> allowedExecutables,
                           List<String> allowedEnv) {
        if (!Double.isFinite(maxTimeout) || maxTimeout < 0.1 || maxTimeout > 120.0) {
            throw new ValidationError("max_timeout must be between 0.1 and 120 seconds");
        }
        this.maxTimeout = maxTimeout;
        this.allowedExecutables = immutableExecutables(allowedExecutables);
        this.allowedEnv = immutableEnvironment(allowedEnv);
    }

    public double maxTimeout() {
        return maxTimeout;
    }

    public double getMaxTimeout() {
        return maxTimeout;
    }

    public double max_timeout() {
        return maxTimeout;
    }

    public List<String> allowedExecutables() {
        return allowedExecutables;
    }

    public List<String> getAllowedExecutables() {
        return allowedExecutables;
    }

    public List<String> allowed_executables() {
        return allowedExecutables;
    }

    public List<String> allowedEnv() {
        return allowedEnv;
    }

    public List<String> getAllowedEnv() {
        return allowedEnv;
    }

    public List<String> allowed_env() {
        return allowedEnv;
    }

    private static List<String> immutableExecutables(List<String> values) {
        if (values == null) {
            throw new ValidationError("allowed_executables must be a list");
        }
        Set<String> sorted = new TreeSet<>();
        for (String value : values) {
            if (value == null || value.isEmpty() || !value.matches("[A-Za-z0-9._+-]+")) {
                throw new ValidationError("allowed_executables must contain executable basenames");
            }
            sorted.add(value);
        }
        return Collections.unmodifiableList(new ArrayList<>(sorted));
    }

    private static List<String> immutableEnvironment(List<String> values) {
        if (values == null) {
            throw new ValidationError("allowed_env must be a list");
        }
        Set<String> sorted = new TreeSet<>();
        for (String value : values) {
            if (value == null || !value.matches("[A-Za-z_][A-Za-z0-9_]*")
                    || FORBIDDEN_ENV.contains(value.toUpperCase(Locale.ROOT))) {
                throw new ValidationError("allowed_env must contain safe environment names");
            }
            sorted.add(value);
        }
        return Collections.unmodifiableList(new ArrayList<>(sorted));
    }
}
