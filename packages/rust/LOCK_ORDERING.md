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
- Existing termination closes stdin, releases that lock, then takes the process
  lock. It never takes the exchange lock; cancellation can interrupt a reader
  holding exchange. Policy cleanup occurs after those handles are released.

Four admitted operations bound workers and outstanding cleanup jobs. Admission
requires the single cleanup worker to exist; exhaustion fails with a typed error.
This is not a promise of lock-free polling or asynchronous `AgentContext::drop`.
