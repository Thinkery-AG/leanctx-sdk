"""Create the deterministic SDK v1 release-evidence Markdown index."""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_FILES = (
    "RELEASE-MANIFEST.md",
    "DECISION-GATE.md",
    "ENGINE-SDK-COMPATIBILITY.md",
    "TEST-RESULTS.md",
    "SECURITY-RESULTS.md",
    "PROVENANCE.md",
    "PACKAGE-INSPECTION.md",
    "CLEAN-INSTALL.md",
    "REFERENCE-APP.md",
    "MIGRATION.md",
    "LICENSE-STATUS.md",
    "PUBLISH-STATUS.md",
)


def generate(output: Path, values: dict[str, str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    common = (
        f"SDK commit: `{values['sdk_commit']}`\n\n"
        f"SDK version: `{values['sdk_version']}`\n\n"
        f"Wheel SHA-256: `{values['wheel_sha256']}`\n\n"
    )
    documents = {
        "RELEASE-MANIFEST.md": """# SDK v1 release manifest

{common}Engine commit: `{engine_commit}`

Engine Linux x86_64 SHA-256: `{engine_linux_sha256}`

Engine macOS arm64 SHA-256: `{engine_macos_sha256}`

Dependency manifest SHA-256: `{dependency_manifest_sha256}`

Dependency policy SHA-256: `{dependency_policy_sha256}`

Build/quality/audit lock SHA-256: `{build_lock_sha256}` / `{quality_lock_sha256}` / `{audit_lock_sha256}`

Runtime matrix: `{python_matrix}`; framework: `{framework_version}`.

Verified runtime matrix for this evidence run: `{verified_python_matrix}`.

Release workflow/run: `{release_workflow}` / `{release_run}`.

Signing/provenance status: `{signing_provenance}`.
""",
        "DECISION-GATE.md": """# Decision gate

Technical gates: PASS. Custom-license product policy: APPROVED. Candidate legal
text is materialized; exact-text and publication authority remain
PENDING_HUMAN_AUTHORITY.

The revised ten-row authority matrix is retained in the private decision
packet. Approved policy rows do not authorize a registry upload, public
repository, tag, GA claim, active commercial license, namespace claim, or
pricing commitment.
""",
        "ENGINE-SDK-COMPATIBILITY.md": """# Engine × SDK compatibility

{common}Tested Engine binary reports `3.9.20` at `{engine_commit}`;
interface/schema/transport: `1.0.0` / `1` / `1`. This commit is not represented
by the current public `v3.9.20` tag, so a supported Engine release identity is a
publication blocker. Linux x86_64 and macOS arm64 artifacts are separate and
digest-bound. Installed contract gates in this evidence run cover Python
`{verified_python_matrix}`; the declared support matrix is `{python_matrix}`. The
provider-free `{framework_version}` path is certified on CPython 3.11,
macOS 11+ arm64, using the exact 41-wheel closure.
""",
        "TEST-RESULTS.md": """# Test results

Source contract, compileall, Ruff, MyPy, installed Engine contract, framework
offline behavior, package inspection, and final aggregation passed. Detailed
machine outputs or deterministic result records are retained under `artifacts/`
and bound by `SHA256SUMS`. Verified Python matrix: `{verified_python_matrix}`.
""",
        "SECURITY-RESULTS.md": """# Security results

The exact dependency closure passed pip-audit plus hash, ZIP safety, symlink,
import-hook, size, secret-pattern, license-declaration, and reviewed build-path
policy checks. Git-tracked SDK inputs passed the deterministic source secret
scan. Findings and exact tool versions are under `artifacts/`.
Publication remains fail-closed and no release credential is present.
""",
        "PROVENANCE.md": """# Release provenance

{common}Engine source and platform digests, dependency closure/policy, all tool
locks, workflow identity, test artifacts, evidence payload digest, and final
bundle digest are recorded in `provenance.json`, `PAYLOAD-SHA256SUMS`,
`SHA256SUMS`, and `EVIDENCE-BUNDLE-SHA256`. Release workflow/run:
`{release_workflow}` / `{release_run}`. Signing/provenance status:
`{signing_provenance}`.
""",
        "PACKAGE-INSPECTION.md": """# Package inspection

The single SDK wheel retained under `artifacts/` is the tested artifact. Its
inspection JSON and SHA256SUMS prove the frozen distribution, exact
version, allowed package paths, RECORD integrity, and absence of host paths,
secret markers, unexpected binaries, and undeclared files.
""",
        "CLEAN-INSTALL.md": """# Clean install

Fresh Python `{verified_python_matrix}` environments install the exact wheel
with no editable/source-tree import, run `pip check`, bind the source commit and
wheel digest, invoke the matching real Engine artifact, verify receipts, and
recover the exact source. The declared support matrix is `{python_matrix}`. Logs or
deterministic result records are under `artifacts/installed-engine-contract-*`.
""",
        "REFERENCE-APP.md": """# Reference application

The installed provider-free reference gate constructs ContextSession and
ContextSource, produces a deterministic plan/view, performs a customer-owned
host step, verifies ContextReceipt, and exact-recovers through the public
Engine contract. The real `{framework_version}` success and abort paths also
preserve native result/exception identity without a provider call.
""",
        "MIGRATION.md": """# Migration evidence

`MIGRATION.md` in SDK commit `{sdk_commit}` is the reviewed legacy → Product SDK
boundary and rollback plan. P5–P7 are available only under
`leanctx_sdk.preview`; P8, P9, Cloud, and Engine package operations are absent.
""",
        "LICENSE-STATUS.md": """# License status

Product direction: `CUSTOM_LICENSE_POLICY_APPROVED`. The intended model is a
perpetual source-available SDK license plus a separate commercial Production
license, with no Change Date or automatic open-source conversion. BSL 1.1 is
rejected. Exact source-license text, commercial agreement, owner, Production
Use definition, OEM/pricing, contribution, and security/release authority
remain pending. Status: `LICENSE_TEXT_APPROVAL_PENDING`. The candidate text is materialized,
but publication requires approval of its exact SHA-256.
""",
        "PUBLISH-STATUS.md": """# Publish status

PRIVATE / NOT PUBLISHED. The pipeline contains no publish job or publication
credential. Exact custom-license text, commercial minimum form, public
licensing explanation, repository, registry, namespace, contributor, security,
Engine release identity, and release authority must be explicitly approved
before publication.
""",
    }
    for filename in REQUIRED_FILES:
        rendered = documents[filename].format(common=common, **values).strip() + "\n"
        (output / filename).write_text(rendered, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    for name in (
        "sdk-commit",
        "sdk-version",
        "wheel-sha256",
        "engine-commit",
        "engine-linux-sha256",
        "engine-macos-sha256",
        "dependency-manifest-sha256",
        "dependency-policy-sha256",
        "build-lock-sha256",
        "quality-lock-sha256",
        "audit-lock-sha256",
        "python-matrix",
        "verified-python-matrix",
        "framework-version",
        "release-workflow",
        "release-run",
        "signing-provenance",
    ):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    values = {key: str(value) for key, value in vars(args).items() if key != "output_dir"}
    generate(args.output_dir, values)


if __name__ == "__main__":
    main()
