# Rust SDK async lock ordering

The async adapter reuses `AgentState::terminate`; it adds no second transport or
process ownership authority. Its standard-library mutexes protect short in-memory
transitions, not Engine I/O on the polling thread.

- `OperationState` may precede `OperationControl` slot/termination locks or the
  cleanup-state deferred-result/condition locks. No reverse acquisition exists.
- Slot and termination guards are released before acquiring the cleanup queue.
  Queue guards are released before process termination, condition waiting or
  deferred-result destruction. Worker-count admission is never held during I/O.
- Completion publication and the condition-variable predicate use the same
  `result_lock`; a notifier cannot race between the waiter's check and sleep.
- The cleanup worker terminates first, then waits for the operation's result,
  releases the deferred-result lock before destroying that result, and finally
  releases admission. Waiting never holds the queue or operation-state lock.
- Termination takes the process lock, kills the process group and reaps the
  child, releases that lock, then takes the writer lock only to drop the
  request queue. It never takes the exchange lock and never waits for pipe I/O;
  cancellation can interrupt a caller holding exchange, whether that caller is
  waiting for a write or for a response. Policy cleanup occurs afterwards.
- Engine stdin is owned by one writer thread. `exchange` holds the writer lock
  only for the non-blocking enqueue; no lock is held across a pipe write. The
  order is `exchange` → `writer` (enqueue) → `responses`; `process` and `writer`
  are never held together, and neither is held while waiting.
- One deadline covers the request write and the response. A write that does not
  complete in time terminates the session and yields `EngineTimeout`; a write
  that fails because the Engine is gone yields `EngineCrashed`. Killing the
  process group closes the pipe, which ends the blocked write and the writer
  thread. A descendant that left the process group and still holds the pipe's
  read end would keep that one thread parked; callers and termination are
  unaffected.
- Process cleanup has exactly one owner. The `cleanup` lock guards only the
  `Idle` → `Running` → `Done(outcome)` transitions and is never held across
  the kill or the reap. The owner reaps within `REAP_BUDGET` (5 s, polled);
  other callers wait on `cleanup_done` for at most `CLEANUP_WAIT_BUDGET`
  (10 s). Every caller reports the stored outcome: a failed cleanup stays
  failed, a waiter whose budget runs out gets "cleanup is still pending".
  `cleanup` is taken before `process` and never together with `exchange`.

Four admitted operations bound workers and outstanding cleanup jobs. Admission
requires the single cleanup worker to exist; exhaustion fails with a typed error.
This is not a promise of lock-free polling or asynchronous `AgentContext::drop`.
