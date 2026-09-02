# Security policy

## Supported versions

Security fixes target the current Stable major release. Preview APIs receive
best-effort fixes but do not have the Stable compatibility guarantee.

## Reporting

Use GitHub Private Vulnerability Reporting for the Thinkery-controlled
repository. Do not include secrets, prompts, customer data, proprietary source,
or credentials in public issues. No email address is published until its
operation is independently verified.

## Product boundary

The SDK treats Engine output and persisted Preview state as untrusted input. It
validates versions, duplicate/unknown fields, bounds, rooted paths, lineage,
digests, receipt links, and lifecycle transitions. Subprocess calls use an
argument vector, rooted working directory, owner-only temporary request,
bounded output and deadline, and a minimal environment.

Integrity, policy, lifecycle, compatibility, path, and malformed-protocol
failures fail closed. Only explicitly configured Engine-unavailable and timeout
conditions may degrade within host policy. The SDK never forwards provider
credentials or Cloud configuration and does not persist raw transcripts.

The core wheel has no third-party runtime dependency. Optional OpenAI Agents
dependencies are accepted only through the exact reviewed closure and release
audit.

## Agent Tools boundary

`AgentContext` is read-only by default. Write and process execution are
separate immutable permissions, recorded in an owner-only policy file and
enforced again by the Engine. The Engine canonicalizes the project root,
rejects unsafe roots, restricts the callable capability set, and returns
bounded NDJSON frames. The SDK never passes a shell string: `run()` accepts an
argv sequence; the Engine revalidates argv, executable, env, and timeout before
constructing its internal command and applying its own shell policy.

Timeout, process exit, malformed frames, unknown capabilities, permission
violations, and protocol mismatches fail closed. The SDK does not retry a
write or execution request after an ambiguous failure.

Executable allowlisting is not an operating-system sandbox. A permitted tool
such as `git` may itself access the network or execute configured helpers. Use
an OS/container sandbox when the host requires filesystem or network isolation
beyond the Engine's project jail and command policy.
