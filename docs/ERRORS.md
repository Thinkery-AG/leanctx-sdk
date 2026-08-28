# Errors and troubleshooting

Every `SDKError` exposes deterministic `as_dict()` guidance.

| Family | Action |
| --- | --- |
| Configuration | Fix configuration; abort the current attempt. |
| Unsupported Engine | Install the exact supported Engine release/artifact. |
| Engine unavailable/timeout | Retry within host policy or explicitly degrade where allowed. |
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
