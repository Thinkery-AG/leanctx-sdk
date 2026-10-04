// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ProcessTerminationTest {
    @TempDir Path root;

    @Test
    void oneShotTimeoutReapsCapturedChildAndRemovesRequest() throws Exception {
        SubprocessEngineClient client = new SubprocessEngineClient(fixture(), 2.0);
        try {
            assertThrows(EngineTimeout.class, () -> client.contextView(plan()));
            assertReaped();
            assertNoRequest();
        } finally {
            cleanFixture();
        }
    }

    @Test
    void interruptedOneShotReapsChildAndRestoresInterruptFlag() throws Exception {
        SubprocessEngineClient client = new SubprocessEngineClient(fixture(), 10.0);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        AtomicBoolean interrupted = new AtomicBoolean();
        Thread caller = new Thread(() -> {
            try {
                client.contextView(plan());
            } catch (Throwable exception) {
                failure.set(exception);
            } finally {
                interrupted.set(Thread.currentThread().isInterrupted());
            }
        });
        caller.start();
        try {
            awaitFixture();
            caller.interrupt();
            caller.join(5000);
            assertFalse(caller.isAlive(), "interrupted call finishes bounded cleanup");
            assertInstanceOf(EngineTimeout.class, failure.get());
            assertTrue(interrupted.get(), "caller interrupt flag is preserved");
            assertReaped();
            assertNoRequest();
        } finally {
            cleanFixture();
            caller.interrupt();
            caller.join(5000);
        }
    }

    @Test
    void unsuccessfulRootWaitIsNotSilentlyAccepted() throws Exception {
        Process actual = new ProcessBuilder("/bin/sleep", "20").start();
        // Inject only the OS wait result; termination still targets a real,
        // test-owned process. This deterministically exercises the old silent branch.
        Process unreaped = new Process() {
            @Override public OutputStream getOutputStream() { return actual.getOutputStream(); }
            @Override public InputStream getInputStream() { return actual.getInputStream(); }
            @Override public InputStream getErrorStream() { return actual.getErrorStream(); }
            @Override public int waitFor() throws InterruptedException { return actual.waitFor(); }
            @Override public boolean waitFor(long timeout, TimeUnit unit) { return false; }
            @Override public int exitValue() { return actual.exitValue(); }
            @Override public void destroy() { actual.destroy(); }
            @Override public Process destroyForcibly() { actual.destroyForcibly(); return this; }
            @Override public ProcessHandle toHandle() { return actual.toHandle(); }
        };
        try {
            EngineExecutionError error = assertThrows(EngineExecutionError.class,
                    () -> ProcessTreeTermination.terminate(unreaped));
            assertEquals("Engine process could not be reaped", error.getMessage());
        } finally {
            actual.destroyForcibly();
            assertTrue(actual.waitFor(2, TimeUnit.SECONDS));
        }
    }

    private Path fixture() throws Exception {
        Path script = root.resolve("engine-fixture");
        Files.writeString(script, "#!/bin/sh\n"
                + "printf '%s' \"$$\" > engine.pid\n"
                + "/bin/sleep 20 &\n"
                + "printf '%s' \"$!\" > child.pid\n"
                + "printf 'ready' > ready\n"
                + "wait \"$!\"\n");
        assertTrue(script.toFile().setExecutable(true));
        return script;
    }

    private ContextPlan plan() {
        return new ContextPlan("fixture-session", "fixture-task", "inspect",
                new ContextSource("README.md", root));
    }

    private void awaitFixture() throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (!Files.exists(root.resolve("ready")) && System.nanoTime() < deadline) {
            Thread.sleep(10);
        }
        assertTrue(Files.exists(root.resolve("ready")), "fixture reached its blocking operation");
    }

    private void assertReaped() throws Exception {
        assertTrue(Files.exists(root.resolve("ready")), "fixture started before termination");
        for (String name : new String[] {"engine.pid", "child.pid"}) {
            long pid = Long.parseLong(Files.readString(root.resolve(name)));
            assertFalse(ProcessHandle.of(pid).map(ProcessHandle::isAlive).orElse(false),
                    name + " must be dead before the call returns");
        }
    }

    private void assertNoRequest() throws Exception {
        try (var entries = Files.list(root)) {
            assertTrue(entries.noneMatch(path -> path.getFileName().toString().startsWith(".leanctx-sdk-")));
        }
    }

    private void cleanFixture() throws Exception {
        for (String name : new String[] {"child.pid", "engine.pid"}) {
            Path path = root.resolve(name);
            if (Files.exists(path)) {
                long pid = Long.parseLong(Files.readString(path));
                ProcessHandle.of(pid).ifPresent(ProcessHandle::destroyForcibly);
            }
        }
    }
}
