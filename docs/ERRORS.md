# Errors and troubleshooting

Every `SDKError` exposes deterministic `as_dict()` guidance.

| Family | Action |
| --- | --- |
| Configuration | Fix configuration; abort the current attempt. |
| Unsupported Engine | Install the exact supported Engine release/artifact. |
| Engine unavailable/timeout | Retry within host policy or explicitly degrade where allowed. |
| Engine crashed | Treat the session as terminal; create a new context only under host policy. |
| Agent permission | Enable the narrow permission before startup; policy is immutable. |
| Unsupported capability | Use a negotiated capability or install the exact compatible Engine. |
| Source unavailable | Restore or select the source before retrying. |
| Recovery unavailable | Abort and restore exact recovery binding. |
| Policy rejection | Change the request or abort; do not bypass policy. |
| Compatibility mismatch | Use a matrix-supported SDK/Engine/Python combination. |
| Invalid lifecycle | Start a new session with correct transition ordering. |
| Framework integration | Fix the certified framework installation or lifecycle. |
| Artifact integrity | Abort and replace the artifact with digest-verified evidence. |

Only explicitly retryable Engine availability failures may use bounded
fail-open behavior. Integrity, policy, lifecycle, and compatibility failures
fail closed.

Agent Tools adds `EngineCrashed`, `AgentPermissionError`, and
`UnsupportedCapabilityError`. A timeout terminates the persistent Engine
process. Writes and executions are never retried automatically because their
outcome may be ambiguous.
