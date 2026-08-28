# Recovery

`ContextView` carries a source digest and recovery reference. Calling
`session.recover(view)` returns `RecoveredSource` only when the Engine returns
bytes matching that exact digest. A mismatch is an integrity failure, never a
best-effort success.

Recovery unavailable or integrity failures require aborting the dependent
operation and restoring a supported Engine/source binding. Cloud is not used
as a recovery dependency.
