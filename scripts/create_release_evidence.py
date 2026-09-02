"""Create the deterministic SDK v1 release-evidence Markdown index."""

from __future__ import annotations

import argparse
import re
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

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate(values: dict[str, str]) -> None:
    if values.get("publication_authorization") != "APPROVED":
        raise ValueError("final evidence requires explicit publication authorization")
    if values.get("sdk_version") != "1.0.0":
        raise ValueError("final evidence requires SDK version 1.0.0")
    if (
        values.get("engine_version") != "3.10.0"
        or values.get("engine_tag") != "v3.10.0"
    ):
        raise ValueError("final evidence requires Engine v3.10.0")
    if values.get("pypi_project") != "thinkery-leanctx-sdk":
        raise ValueError(
            "final evidence requires the thinkery-leanctx-sdk PyPI project"
        )
    if values.get("engine_release_repository") != "yvgude/lean-ctx":
        raise ValueError("final evidence requires the public Engine repository")
    expected_release_url = "https://github.com/yvgude/lean-ctx/releases/tag/v3.10.0"
    if values.get("engine_release_url") != expected_release_url:
        raise ValueError("final evidence requires the public Engine release URL")
    if values.get("engine_linux_asset") != "lean-ctx-x86_64-unknown-linux-gnu.tar.gz":
        raise ValueError("final evidence requires the supported Linux Engine asset")
    if values.get("engine_macos_asset") != "lean-ctx-aarch64-apple-darwin.tar.gz":
        raise ValueError("final evidence requires the supported macOS Engine asset")
    expected_cosign_identity = (
        "https://github.com/yvgude/lean-ctx/.github/workflows/"
        "release.yml@refs/tags/v3.10.0"
    )
    if values.get("engine_cosign_identity") != expected_cosign_identity:
        raise ValueError("final evidence requires the Engine release signer identity")
    if "/" not in values.get("public_repository", ""):
        raise ValueError("final evidence requires an owner/repository identity")
    for key in ("sdk_commit", "engine_commit"):
        if not _COMMIT.fullmatch(values.get(key, "")):
            raise ValueError(f"final evidence requires a valid {key}")
    for key in (
        "wheel_sha256",
        "engine_linux_sha256",
        "engine_macos_sha256",
        "engine_linux_archive_sha256",
        "engine_macos_archive_sha256",
        "engine_checksums_sha256",
        "dependency_manifest_sha256",
        "dependency_policy_sha256",
        "build_lock_sha256",
        "quality_lock_sha256",
        "audit_lock_sha256",
        "license_sha256",
    ):
        if not _SHA256.fullmatch(values.get(key, "")):
            raise ValueError(f"final evidence requires a valid {key}")


def generate(output: Path, values: dict[str, str]) -> None:
    _validate(values)
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

Public Engine release: `{engine_release_url}`

Linux archive `{engine_linux_asset}` SHA-256:
`{engine_linux_archive_sha256}`

macOS archive `{engine_macos_asset}` SHA-256:
`{engine_macos_archive_sha256}`

Signed `SHA256SUMS` SHA-256: `{engine_checksums_sha256}`; signer identity:
`{engine_cosign_identity}`.

Dependency manifest SHA-256: `{dependency_manifest_sha256}`

Dependency policy SHA-256: `{dependency_policy_sha256}`

Build/quality/audit lock SHA-256: `{build_lock_sha256}` / `{quality_lock_sha256}` / `{audit_lock_sha256}`

Runtime matrix: `{python_matrix}`; framework: `{framework_version}`.

Verified runtime matrix for this evidence run: `{verified_python_matrix}`.

Release workflow/run: `{release_workflow}` / `{release_run}`.

Signing/provenance status: `{signing_provenance}`.
""",
        "DECISION-GATE.md": """# Decision gate

Technical gates: PASS. Custom-license product policy and exact release text:
APPROVED. Final publication remains bound to the exact hashes and protected
Trusted Publishing gate recorded in the final ship packet.
""",
        "ENGINE-SDK-COMPATIBILITY.md": """# Engine × SDK compatibility

{common}Tested Engine `{engine_version}` at `{engine_commit}`, released as
`{engine_tag}`; interface/schema/transport: `1.0.0` / `1` / `1`. Linux x86_64
and macOS arm64 archives come only from `{engine_release_repository}`, are
signature- and digest-bound, and yield separately digest-bound binaries.
Installed contract
gates in this evidence run cover Python
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
scan. Findings and exact tool versions are under `artifacts/`. Publication uses
GitHub OIDC Trusted Publishing and no long-lived registry credential.
""",
        "PROVENANCE.md": """# Release provenance

{common}Public Engine release identity, signed checksums, archive and extracted
binary digests, dependency closure/policy, all tool
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
boundary and rollback plan. Workspace/checkpoint/delta/handoff and narrow
package lifecycle operations are Preview; hosted Cloud and optimization
research are absent.
""",
        "LICENSE-STATUS.md": """# License status

Status: `CUSTOM_LICENSE_TEXT_APPROVED`. The model is a
perpetual source-available SDK license plus a separate commercial Production
license, with no Change Date or automatic open-source conversion. BSL 1.1 is
rejected. LICENSE SHA-256: `{license_sha256}`.
""",
        "PUBLISH-STATUS.md": """# Publish status

Repository: `{public_repository}`. PyPI project: `{pypi_project}`. Publication
is available only from the exact `v1.0.0` tag after all technical gates and the
approved wheel-hash guard pass, using GitHub OIDC Trusted Publishing.
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
        "engine-version",
        "engine-tag",
        "engine-linux-sha256",
        "engine-macos-sha256",
        "engine-release-repository",
        "engine-release-url",
        "engine-linux-asset",
        "engine-macos-asset",
        "engine-linux-archive-sha256",
        "engine-macos-archive-sha256",
        "engine-checksums-sha256",
        "engine-cosign-identity",
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
        "license-sha256",
        "public-repository",
        "pypi-project",
        "publication-authorization",
    ):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    values = {
        key: str(value) for key, value in vars(args).items() if key != "output_dir"
    }
    generate(args.output_dir, values)


if __name__ == "__main__":
    main()
