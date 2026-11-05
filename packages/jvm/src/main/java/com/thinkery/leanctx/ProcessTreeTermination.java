// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
package com.thinkery.leanctx;

import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** Shared bounded cleanup of a LeanCTX instance process and its captured descendants. */
@SuppressWarnings("removal")
final class ProcessTreeTermination {
    private ProcessTreeTermination() {}

    static void terminate(Process process) {
        // Cancellation commonly arrives with the interrupt flag already set.
        // Finish bounded cleanup before restoring it to the caller.
        boolean interrupted = Thread.interrupted();
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        List<ProcessHandle> descendants = List.of();
        RuntimeException signalFailure = null;
        try {
            try {
                descendants = process.toHandle().descendants().toList();
            } catch (RuntimeException exception) {
                signalFailure = exception;
            }
            for (int i = descendants.size() - 1; i >= 0; i--) {
                try {
                    descendants.get(i).destroyForcibly();
                } catch (RuntimeException exception) {
                    signalFailure = exception;
                }
            }
            try {
                process.destroyForcibly();
            } catch (RuntimeException exception) {
                throw new EngineExecutionError("LeanCTX instance process could not be terminated",
                        null, null, exception);
            }
            for (;;) {
                try {
                    if (!process.waitFor(remaining(deadline), TimeUnit.NANOSECONDS)) {
                        throw new EngineExecutionError("LeanCTX instance process could not be reaped");
                    }
                    for (ProcessHandle descendant : descendants) {
                        if (descendant.isAlive()) {
                            descendant.onExit().get(remaining(deadline), TimeUnit.NANOSECONDS);
                        }
                    }
                    break;
                } catch (InterruptedException exception) {
                    interrupted = true;
                } catch (ExecutionException | TimeoutException exception) {
                    throw new EngineExecutionError("LeanCTX instance process tree could not be reaped",
                            null, null, exception);
                } catch (InstanceExecutionError exception) {
                    throw exception;
                } catch (RuntimeException exception) {
                    throw new EngineExecutionError("LeanCTX instance process tree reaping could not be verified",
                            null, null, exception);
                }
            }
            if (signalFailure != null) {
                throw new EngineExecutionError("LeanCTX instance process tree termination could not be verified",
                        null, null, signalFailure);
            }
        } finally {
            if (interrupted) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private static long remaining(long deadline) throws TimeoutException {
        long nanos = deadline - System.nanoTime();
        if (nanos <= 0) {
            throw new TimeoutException("LeanCTX instance process cleanup deadline exceeded");
        }
        return nanos;
    }
}
