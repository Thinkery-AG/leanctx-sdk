package com.thinkery.leanctx;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Map;

/**
 * How the LeanCTX instance's own process tree is contained.
 *
 * <p>The JVM cannot put a child into a process group of its own. With
 * {@link #ENGINE_STARTER} the LeanCTX instance's session starter is what provides that
 * group: the process this SDK started is the starter; it runs the LeanCTX instance in a
 * new process group, ends that group when the LeanCTX instance exits, when this SDK
 * ends the starter or when this JVM is gone, and exits only afterwards.
 *
 * <p>{@link #UNAVAILABLE} is reduced containment: the LeanCTX instance binary has no
 * such mode, or the platform is Windows. Then only the processes the JDK can
 * still enumerate as descendants of a live LeanCTX instance are ended; what an Engine
 * that exited first left behind is not reachable.
 */
public enum SessionContainment {
    ENGINE_STARTER("engine-starter"),
    UNAVAILABLE("unavailable");

    private static final String REQUEST_VARIABLE = "LEAN_CTX_SESSION_CONTAINMENT";
    /** The starter's first output line, written before the LeanCTX instance exists. */
    private static final byte[] ACK =
            "{\"leanctx_session_containment\":1}".getBytes(StandardCharsets.UTF_8);

    private final String wireName;

    SessionContainment(String wireName) {
        this.wireName = wireName;
    }

    public String wireName() {
        return wireName;
    }

    /**
     * Asks the LeanCTX instance binary to contain its session: on POSIX it runs as its
     * own session starter, on Windows it joins a kill-on-close job. A LeanCTX instance
     * without the mode ignores the variable and acknowledges nothing.
     */
    static void request(Map<String, String> environment) {
        environment.put(REQUEST_VARIABLE, "1");
    }

    /** Whether {@code line} (without its newline) is the acknowledgement. */
    static boolean isAck(byte[] line) {
        return Arrays.equals(line, ACK);
    }

    /** Length of the acknowledgement at the start of {@code output}, or 0. */
    static int ackPrefixLength(byte[] output) {
        int length = ACK.length + 1;
        if (output.length < length || output[ACK.length] != '\n') return 0;
        return Arrays.equals(output, 0, ACK.length, ACK, 0, ACK.length) ? length : 0;
    }
}
