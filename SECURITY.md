# Security policy

## Supported versions

Security fixes target the current Stable major release. Preview APIs receive
best-effort fixes but do not have the Stable compatibility guarantee.

## Reporting

Use GitHub Private Vulnerability Reporting for the final Thinkery-controlled
repository. Do not include secrets, prompts, customer data, proprietary source,
or credentials in public issues. No email address is published until its
operation is independently verified.

The repository destination and Private Vulnerability Reporting setting must be
verified before public release. Until then, this local candidate is not a
public security intake.

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
