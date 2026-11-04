// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.DisabledOnOs;
import org.junit.jupiter.api.condition.OS;
import org.junit.jupiter.api.io.TempDir;

/**
 * Deadlines, cancellation and terminal conditions against a real child process
 * tree. The Engine is a shell stand-in; nothing here starts a LeanCTX instance.
 */
@DisabledOnOs(value = OS.WINDOWS, disabledReason = "POSIX shell stand-in; Windows runs in the CI matrix")
class AgentTerminalConditionsTest {
    private static final String HELLO = "{\"agent_tools_interface_version\":\"1.0.0\",\"allow_exec\":false,"
            + "\"allow_write\":false,\"capabilities\":[\"ctx_compose\",\"ctx_glob\",\"ctx_read\",\"ctx_search\","
            + "\"ctx_symbol\",\"ctx_tree\"],\"engine_version\":\"3.11.0\",\"schema_version\":1,\"transport_version\":1}";

    @TempDir Path root;

    /** Answers the hello, keeps one descendant alive, records both identifiers, then runs afterHello. */
    private Path standIn(String afterHello) throws Exception {
        Path script = root.resolve("stand-in-engine.sh");
        String source = "#!/bin/sh\n"
                + "IFS= read -r line\n"
                + "printf '%s' \"$$\" > leader.pid.tmp && mv leader.pid.tmp leader.pid\n"
                + "sleep 30 >/dev/null 2>&1 &\n"
                + "printf '%s' \"$!\" > descendant.pid.tmp && mv descendant.pid.tmp descendant.pid\n"
                + "printf '%s\\n' '{\"id\":\"1\",\"ok\":true,\"result\":" + HELLO + "}'\n"
                + afterHello;
        Files.writeString(script, source, StandardCharsets.UTF_8);
        assertEquals(0, new ProcessBuilder("/bin/sh", "-n", script.toString()).start().waitFor(), source);
        assertTrue(script.toFile().setExecutable(true));
        return script;
    }

    private AgentContext open(String afterHello, double timeout) throws Exception {
        return new AgentContext(root.toRealPath().toString(), "terminal", new AgentPermissions(),
                new ExecutionPolicy(), standIn(afterHello).toString(), timeout);
    }

    private ProcessHandle published(String name) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3);
        while (!Files.exists(root.resolve(name)) && System.nanoTime() < deadline) {
            Thread.sleep(10);
        }
        long pid = Long.parseLong(Files.readString(root.resolve(name)).trim());
        return ProcessHandle.of(pid).orElse(null);
    }

    private static void assertGone(ProcessHandle handle, String what) throws Exception {
        if (handle == null) {
            return;
        }
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3);
        while (handle.isAlive() && System.nanoTime() < deadline) {
            Thread.sleep(10);
        }
        boolean alive = handle.isAlive();
        if (alive) {
            handle.destroyForcibly();
        }
        assertFalse(alive, what + " survived");
    }

    private void assertNoPolicyDirectory() throws Exception {
        try (Stream<Path> children = Files.list(root)) {
            List<String> left = children.map(item -> item.getFileName().toString())
                    .filter(name -> name.startsWith(".leanctx-agent-")).toList();
            assertEquals(List.of(), left, "temporary policy directory left behind");
        }
    }

    private void assertTerminal(AgentContext context) throws Exception {
        assertGone(published("leader.pid"), "instance leader");
        assertGone(published("descendant.pid"), "instance descendant");
        assertNoPolicyDirectory();
        assertThrows(InstanceCrashed.class, () -> context.read("README.md"));
        context.close();
        context.close();
    }

    private static Map<String, Object> oversized() {
        return Map.of("padding", "x".repeat(600_000));
    }

    @Test
    void closedStdoutWithLiveEngineAndDescendantIsTerminalAtOnce() throws Exception {
        AgentContext context = open("exec 1>&-\nsleep 30\n", 20.0);
        long started = System.nanoTime();
        assertThrows(InstanceCrashed.class, () -> context.read("README.md"));
        assertTrue(System.nanoTime() - started < TimeUnit.SECONDS.toNanos(5), "must not wait for the deadline");
        assertTerminal(context);
    }

    @Test
    void invalidToolResultInValidEnvelopeTerminatesTheTree() throws Exception {
        AgentContext context = open("IFS= read -r line\n"
                + "printf '%s\\n' '{\"id\":\"2\",\"ok\":true,\"result\":{\"extra\":true}}'\nsleep 30\n", 5.0);
        assertThrows(InstanceProtocolError.class, () -> context.read("README.md"));
        assertTerminal(context);
    }

    @Test
    void requestTheEngineNeverReadsIsBoundedByTheDeadline() throws Exception {
        AgentContext context = open("sleep 30\n", 0.7);
        long started = System.nanoTime();
        assertThrows(InstanceTimeout.class, () -> context.call("ctx_read", oversized()));
        assertTrue(System.nanoTime() - started < TimeUnit.SECONDS.toNanos(4));
        assertTerminal(context);
    }

    @Test
    void cancelInterruptsStalledWriteAndPendingResponseWithinABound() throws Exception {
        for (Map<String, Object> arguments : List.of(oversized(), Map.<String, Object>of())) {
            Files.deleteIfExists(root.resolve("leader.pid"));
            Files.deleteIfExists(root.resolve("descendant.pid"));
            // The deadline is far away: only cancel() may end this call.
            AgentContext context = open("sleep 30\n", 30.0);
            published("leader.pid");
            AtomicReference<Throwable> failure = new AtomicReference<>();
            Thread caller = new Thread(() -> {
                try {
                    context.call("ctx_read", arguments);
                } catch (Throwable exception) {
                    failure.set(exception);
                }
            });
            caller.start();
            Thread.sleep(300);
            long started = System.nanoTime();
            context.cancel();
            assertTrue(System.nanoTime() - started < TimeUnit.SECONDS.toNanos(4), "cancel is bounded");
            caller.join(3000);
            assertFalse(caller.isAlive(), "cancelled call is still blocked");
            assertInstanceOf(InstanceCrashed.class, failure.get());
            assertTerminal(context);
        }
    }

    @Test
    void policyDirectoryThatCannotBeRemovedIsReportedNotHidden() throws Exception {
        AgentContext context = open("sleep 30\n", 5.0);
        Path owned = root.toRealPath().resolve(".leanctx-agent-undeletable");
        Files.createDirectory(owned);
        Path blocker = Files.writeString(owned.resolve("not-created-by-the-sdk"), "keep");
        setField(context, "policyPath", owned.resolve("policy.json"));
        setField(context, "policyDirectory", owned);
        InstanceExecutionError failure = assertThrows(InstanceExecutionError.class, context::cancel);
        assertTrue(failure.getMessage().contains("policy could not be removed"), failure.getMessage());
        assertEquals("keep", Files.readString(blocker));
        assertThrows(InstanceExecutionError.class, context::close);
        Files.delete(blocker);
        context.close();
        assertFalse(Files.exists(owned));
        context.close();
        assertGone(published("leader.pid"), "instance leader");
        assertGone(published("descendant.pid"), "instance descendant");
    }

    @Test
    void shutdownHasOneOutcomeSharedByRepeatedAndParallelCallers() throws Exception {
        AgentContext context = open("sleep 30\n", 30.0);
        published("leader.pid");
        // Positive control: parallel and repeated shutdowns all succeed.
        List<Throwable> failures = java.util.Collections.synchronizedList(new java.util.ArrayList<>());
        List<Thread> callers = new java.util.ArrayList<>();
        for (int index = 0; index < 8; index++) {
            Runnable shutdown = index % 2 == 0 ? context::cancel : context::close;
            Thread caller = new Thread(() -> {
                try {
                    shutdown.run();
                } catch (Throwable exception) {
                    failures.add(exception);
                }
            });
            callers.add(caller);
            caller.start();
        }
        for (Thread caller : callers) {
            caller.join(15_000);
            assertFalse(caller.isAlive(), "a parallel shutdown did not return");
        }
        assertEquals(List.of(), failures);
        context.cancel();
        context.close();
        assertGone(published("leader.pid"), "instance leader");
        assertGone(published("descendant.pid"), "instance descendant");

        // A stored cleanup failure is reported to every later caller, never success.
        // The failure is injected: the SDK has no seam to make a real kill fail.
        InstanceExecutionError stored = new InstanceExecutionError("LeanCTX instance process tree could not be reaped");
        setField(context, "cleanupFailure", stored);
        for (Runnable shutdown : List.<Runnable>of(context::cancel, context::close, context::cancel)) {
            InstanceExecutionError reported = assertThrows(InstanceExecutionError.class, shutdown::run);
            assertEquals(stored, reported);
        }
    }

    /**
     * Negative control with a real survivor. The context's process handle is
     * replaced by a delegate whose kill does nothing; everything else, the
     * bounded wait included, is the real process. The Engine therefore really
     * keeps running, the SDK's own reap gives up at its budget, and that
     * failure must be the outcome for every later cancel and close.
     */
    @Test
    void engineThatCannotBeTerminatedIsABoundedStoredCleanupFailure() throws Exception {
        // The leader must outlive its children: termination still ends the
        // captured descendants for real, and a shell waiting on one would exit.
        AgentContext context = open("while :; do sleep 1; done\n", 30.0);
        ProcessHandle leader = published("leader.pid");
        Field field = AgentContext.class.getDeclaredField("process");
        field.setAccessible(true);
        Process actual = (Process) field.get(context);
        field.set(context, new Process() {
            @Override public java.io.OutputStream getOutputStream() { return actual.getOutputStream(); }
            @Override public java.io.InputStream getInputStream() { return actual.getInputStream(); }
            @Override public java.io.InputStream getErrorStream() { return actual.getErrorStream(); }
            @Override public int waitFor() throws InterruptedException { return actual.waitFor(); }
            @Override public boolean waitFor(long timeout, TimeUnit unit) throws InterruptedException {
                return actual.waitFor(timeout, unit);
            }
            @Override public int exitValue() { return actual.exitValue(); }
            @Override public boolean isAlive() { return actual.isAlive(); }
            @Override public long pid() { return actual.pid(); }
            @Override public ProcessHandle toHandle() { return actual.toHandle(); }
            @Override public void destroy() { /* signals nothing */ }
            @Override public Process destroyForcibly() { return this; }
        });
        try {
            long started = System.nanoTime();
            InstanceExecutionError first = assertThrows(InstanceExecutionError.class, context::cancel);
            long elapsed = System.nanoTime() - started;
            assertEquals("LeanCTX instance process could not be reaped", first.getMessage());
            assertTrue(elapsed >= TimeUnit.MILLISECONDS.toNanos(1500), "the reap did not wait for its budget");
            assertTrue(elapsed < TimeUnit.SECONDS.toNanos(12), "cancel was not bounded");
            assertTrue(actual.isAlive(), "the instance was expected to survive");
            for (Runnable shutdown : List.<Runnable>of(context::cancel, context::close, context::cancel)) {
                long again = System.nanoTime();
                assertEquals(first, assertThrows(InstanceExecutionError.class, shutdown::run));
                assertTrue(System.nanoTime() - again < TimeUnit.SECONDS.toNanos(2),
                        "a later shutdown waited again for the failed cleanup");
            }
            assertThrows(InstanceCrashed.class, () -> context.read("README.md"));
        } finally {
            // The survivor is this test's own child.
            actual.destroyForcibly();
            assertTrue(actual.waitFor(5, TimeUnit.SECONDS));
        }
        assertGone(leader, "instance leader");
        assertGone(published("descendant.pid"), "instance descendant");
    }

    private static void setField(AgentContext context, String name, Object value) throws Exception {
        Field field = AgentContext.class.getDeclaredField(name);
        field.setAccessible(true);
        field.set(context, value);
    }
}
