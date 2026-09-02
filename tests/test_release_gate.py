import base64
import csv
import hashlib
import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.dependency_wheel_audit import audit as audit_dependencies
from scripts.create_release_evidence import REQUIRED_FILES, generate
from scripts.inspect_release_artifact import _canonical_requirement, inspect_wheel
from scripts.manifest_requirements import render
from scripts.public_release_guard import PublicReleaseGuardError, check_files
from scripts.source_secret_scan import SecretScanError, scan_files


DIST_INFO = "thinkery_leanctx_sdk-1.1.0.dist-info"


def _record_hash(data):
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _wheel(path, *, extra=None, python_requires=">=3.9,<3.15"):
    metadata = f"""Metadata-Version: 2.1
Name: thinkery-leanctx-sdk
Version: 1.1.0
Requires-Python: {python_requires}
Provides-Extra: openai-agents
Provides-Extra: agent
Requires-Dist: thinkery-leanctx-engine (==3.10.1) ; extra == 'agent'
Provides-Extra: agent-cuda
Requires-Dist: thinkery-leanctx-engine-cuda (==3.10.1) ; (platform_system == "Linux" and platform_machine == "x86_64") and extra == 'agent-cuda'
Provides-Extra: agent-windows-gnu
Requires-Dist: thinkery-leanctx-engine-windows-gnu (==3.10.1) ; (platform_system == "Windows" and platform_machine == "AMD64") and extra == 'agent-windows-gnu'
Requires-Dist: openai-agents (==0.8.4) ; (python_version >= "3.10") and extra == 'openai-agents'
Requires-Dist: openai (==2.19.0) ; (python_version >= "3.10") and extra == 'openai-agents'
Requires-Dist: pydantic (==2.12.3) ; (python_version >= "3.10") and extra == 'openai-agents'
Requires-Dist: requests (==2.33.0) ; (python_version >= "3.10") and extra == 'openai-agents'
Requires-Dist: urllib3 (==2.7.0) ; (python_version >= "3.10") and extra == 'openai-agents'

""".encode()
    files = {
        "leanctx_sdk/__init__.py": b"value = 1\n",
        f"{DIST_INFO}/METADATA": metadata,
        f"{DIST_INFO}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{DIST_INFO}/top_level.txt": b"leanctx_sdk\n",
        f"{DIST_INFO}/licenses/LICENSE": (
            b"LeanCTX SDK Source License 1.0\n"
            b"There is no Change Date or automatic relicensing.\n"
        ),
        f"{DIST_INFO}/licenses/COMMERCIAL-LICENSE.md": (
            b"Commercial Production licensing\n"
            b"Terms require an executed agreement with Thinkery AG.\n"
        ),
        f"{DIST_INFO}/licenses/THIRD_PARTY_NOTICES": (
            "LeanCTX SDK v1.1.0 — Third-Party Notices\n"
            "The exact 41-wheel audit includes openai-agents 0.8.4 — MIT.\n"
        ).encode("utf-8"),
    }
    if extra:
        files.update(extra)
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, data in files.items():
        writer.writerow((name, _record_hash(data), len(data)))
    writer.writerow((f"{DIST_INFO}/RECORD", "", ""))
    files[f"{DIST_INFO}/RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


class ReleaseGateTests(unittest.TestCase):
    def test_custom_license_release_material_is_final_and_consistent(self):
        repository = Path(__file__).resolve().parents[1]
        required = (
            "LICENSE",
            "LICENSING.md",
            "COMMERCIAL-LICENSE.md",
            "THIRD_PARTY_NOTICES",
        )
        for relative in required:
            self.assertTrue((repository / relative).is_file(), relative)

        license_text = (repository / "LICENSE").read_text(encoding="utf-8")
        for required_text in (
            "LeanCTX SDK Source License 1.0",
            "Permitted Non-Production Use",
            "Production Use",
            "redistribution of the Software",
            "There is no Change Date",
            "WITHOUT WARRANTIES",
            "Thinkery AG",
        ):
            self.assertIn(required_text, license_text)

        licensing = (repository / "LICENSING.md").read_text(encoding="utf-8")
        self.assertRegex(licensing, r"source[- ]available")
        self.assertIn("artifact-specific hashes", licensing)
        notices = (repository / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
        self.assertIn("exact 41-wheel", notices)
        self.assertIn("openai-agents 0.8.4 — MIT", notices)
        self.assertNotRegex(
            "\n".join((license_text, licensing, notices)).lower(),
            r"draft|placeholder|todo legal|counsel must",
        )
        self.assertFalse((repository / "legal").exists())
        self.assertFalse((repository / "docs/internal").exists())

        readme = (repository / "README.md").read_text(encoding="utf-8")
        self.assertRegex(readme, r"source[- ]available")
        self.assertIn("LeanCTX Engine", readme)
        self.assertIn("Apache-2.0", readme)

        runtime = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (repository / "src").rglob("*.py")
        ).lower()
        for forbidden in (
            "phone-home",
            "activation server",
            "license key",
            "kill switch",
        ):
            self.assertNotIn(forbidden, runtime)

    def test_source_secret_scan_is_fail_closed_and_deterministic(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            safe = root_path / "safe.txt"
            safe.write_text("deterministic release input\n", encoding="utf-8")
            first = scan_files(root_path, [Path("safe.txt")])
            second = scan_files(root_path, [Path("safe.txt")])
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "PASS")

            secret = root_path / "secret.txt"
            secret.write_bytes(b"sk-" + b"A" * 24)
            with self.assertRaisesRegex(SecretScanError, "openai_token"):
                scan_files(root_path, [Path("secret.txt")])

    def test_public_release_guard_rejects_private_paths(self):
        with tempfile.TemporaryDirectory() as root:
            repository = Path(root)
            safe = repository / "src/leanctx_sdk/module.py"
            safe.parent.mkdir(parents=True)
            safe.write_text("value = 1\n", encoding="utf-8")
            self.assertEqual(
                check_files(repository, [safe.relative_to(repository)])["status"],
                "PASS",
            )
            private = repository / "docs/internal/decision.md"
            private.parent.mkdir(parents=True)
            private.write_text("private\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicReleaseGuardError, "forbidden"):
                check_files(repository, [private.relative_to(repository)])

    def test_requirement_normalization_accepts_backend_format_only(self):
        legacy = (
            'openai (==2.19.0) ; (python_version >= "3.10") '
            "and extra == 'openai-agents'"
        )
        modern = 'openai==2.19.0; python_version >= "3.10" and extra == "openai-agents"'
        self.assertEqual(_canonical_requirement(legacy), _canonical_requirement(modern))
        self.assertNotEqual(
            _canonical_requirement(modern),
            _canonical_requirement(modern.replace("2.19.0", "2.18.0")),
        )
        self.assertNotEqual(
            _canonical_requirement(modern),
            _canonical_requirement(modern.replace('"3.10"', '"3. 10"')),
        )
        self.assertNotEqual(
            _canonical_requirement(modern),
            _canonical_requirement(
                modern.replace('"openai-agents"', '"openai-agents "')
            ),
        )

    def test_valid_exact_wheel_passes(self):
        with tempfile.TemporaryDirectory() as root:
            wheel = Path(root, "sdk.whl")
            _wheel(wheel)
            self.assertEqual(inspect_wheel(wheel)["status"], "PASS")

    def test_wheel_python_matrix_is_exact(self):
        with tempfile.TemporaryDirectory() as root:
            wheel = Path(root, "sdk.whl")
            _wheel(wheel, python_requires=">=3.9")
            with self.assertRaisesRegex(ValueError, "Python requirement"):
                inspect_wheel(wheel)

    def test_unexpected_path_and_secret_fail_closed(self):
        cases = [
            {"notes.txt": b"unexpected"},
            {"leanctx_sdk/key.txt": b"-----BEGIN " + b"PRIVATE KEY-----"},
            {"leanctx_sdk/path.txt": b"/Users/private/source.py"},
            {"leanctx_sdk/research/private.py": b"value = 1\n"},
            {"leanctx_sdk/internal/decision.py": b"value = 1\n"},
        ]
        for extra in cases:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as root:
                wheel = Path(root, "sdk.whl")
                _wheel(wheel, extra=extra)
                with self.assertRaises(ValueError):
                    inspect_wheel(wheel)

    def test_dependency_audit_requirements_are_exact_and_deterministic(self):
        manifest = (
            Path(__file__).parents[1]
            / "fixtures/openai-agents-0.8.4/wheelhouse-manifest.json"
        )
        requirements = render(manifest).splitlines()
        self.assertEqual(len(requirements), 41)
        self.assertEqual(requirements, sorted(requirements, key=str.casefold))
        self.assertTrue(
            any(line.startswith("openai-agents==0.8.4 ") for line in requirements)
        )
        self.assertTrue(
            any(line.startswith("requests==2.33.0 ") for line in requirements)
        )
        self.assertTrue(
            any(line.startswith("urllib3==2.7.0 ") for line in requirements)
        )
        self.assertTrue(all(" --hash=sha256:" in line for line in requirements))

    def test_dependency_audit_rejects_manifest_outside_certified_closure(self):
        source = (
            Path(__file__).parents[1]
            / "fixtures/openai-agents-0.8.4/wheelhouse-manifest.json"
        )
        data = json.loads(source.read_text(encoding="utf-8"))
        data["requirements"][0]["version"] = "0.8.3"
        with tempfile.TemporaryDirectory() as root:
            mutated = Path(root) / "manifest.json"
            mutated.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requirements differ"):
                render(mutated)

    def _dependency_fixture(self, root_path, license_expression):
        wheelhouse = root_path / "wheels"
        wheelhouse.mkdir()
        wheel = wheelhouse / "sample-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                "sample-1.0.dist-info/METADATA",
                "Metadata-Version: 2.4\nName: sample\nVersion: 1.0\n"
                f"License-Expression: {license_expression}\n",
            )
            archive.writestr("sample/__init__.py", "value = 1\n")
        manifest = root_path / "manifest.json"
        data = {
            "artifacts": [
                {
                    "filename": wheel.name,
                    "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                }
            ]
        }
        manifest.write_text(json.dumps(data), encoding="utf-8")
        policy = root_path / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "accepted_build_path_entries": [],
                    "build_path_rationale": "ACCEPTED_WITH_RATIONALE: fixture",
                    "schema_version": 1,
                }
            ),
            encoding="utf-8",
        )
        return manifest, wheelhouse, data, policy

    def test_dependency_wheel_audit_checks_license_and_secret_policy(self):
        with tempfile.TemporaryDirectory() as root:
            manifest, wheelhouse, data, policy = self._dependency_fixture(
                Path(root), "MIT"
            )
            with patch(
                "scripts.dependency_wheel_audit.load_manifest", return_value=data
            ):
                result = audit_dependencies(manifest, wheelhouse, policy)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["wheels_checked"], 1)

    def test_dependency_wheel_audit_rejects_forbidden_license(self):
        with tempfile.TemporaryDirectory() as root:
            manifest, wheelhouse, data, policy = self._dependency_fixture(
                Path(root), "GPL-3.0-only"
            )
            with (
                patch(
                    "scripts.dependency_wheel_audit.load_manifest", return_value=data
                ),
                self.assertRaisesRegex(ValueError, "forbidden copyleft"),
            ):
                audit_dependencies(manifest, wheelhouse, policy)

    def test_dependency_wheel_audit_rejects_unreviewed_findings(self):
        with tempfile.TemporaryDirectory() as root:
            manifest, wheelhouse, data, policy = self._dependency_fixture(
                Path(root), "MIT"
            )
            wheel = next(wheelhouse.glob("*.whl"))
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("sample/build.txt", "/Users/unreviewed/build")
            data["artifacts"][0]["sha256"] = hashlib.sha256(
                wheel.read_bytes()
            ).hexdigest()
            with (
                patch(
                    "scripts.dependency_wheel_audit.load_manifest", return_value=data
                ),
                self.assertRaisesRegex(ValueError, "reviewed policy"),
            ):
                audit_dependencies(manifest, wheelhouse, policy)

    def test_release_workflow_is_immutable_and_binds_required_gates(self):
        workflow = (
            Path(__file__).parents[1] / ".github/workflows/release-candidate.yml"
        ).read_text(encoding="utf-8")
        self.assertNotRegex(workflow, r"uses:\s+[^\s]+@(v\d+|main|master)\b")
        action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", workflow)
        self.assertTrue(action_refs)
        for action_ref in action_refs:
            self.assertRegex(action_ref, r"^[0-9a-f]{40}$")
        for required in (
            "ENGINE_COMMIT: 5b6920216177b01f48694efff1d6be9505665263",
            "AGENT_TOOLS_ENGINE_VERSION: 3.10.1",
            "ENGINE_VERSION: 3.10.0",
            "ENGINE_TAG: v3.10.0",
            "ENGINE_RELEASE_REPOSITORY: yvgude/lean-ctx",
            "ENGINE_LINUX_ARCHIVE_SHA256: f5ad20cbf3eba9ff3024348cc0abe71199f47ae0e13d5554bfeb6345154928e0",
            "ENGINE_MACOS_ARCHIVE_SHA256: ecd773971d118a19a3de723e82d9f0831c8e1543094d350b3861bcaa75dc6035",
            "ENGINE_CHECKSUMS_SHA256: 0fab38178ac0cbb4b1f807c602f77bc738082672f627fe02448b8be8e7f5d8e4",
            "PYTHONPATH: src:.",
            "static-quality:",
            "source-secret-scan:",
            "engine-artifact:",
            "engine-macos-artifact:",
            "ENGINE_LINUX_X86_64_SHA256: 735f60243cf4030ee6bbb292f06fb23742483fd4c857aac91e02914b3a80ac03",
            "ENGINE_MACOS_ARM64_SHA256: 8f7787ccc6376f1d34b8d342fbc916bd082673e6797ea384e6e10edc3641b4eb",
            "cosign verify-blob",
            "runs-on: macos-26",
            "engine-linux-x86_64-",
            "engine-macos-arm64-",
            "sdk-artifact:",
            "installed-engine-contract:",
            "dependency-audit:",
            "typescript-sdk:",
            "go-sdk:",
            "rust-sdk:",
            "jvm-sdk:",
            "dotnet-sdk:",
            "cross-sdk-proof:",
            "framework-offline:",
            "provenance:",
            "publication-guard:",
            "publish-pypi:",
            "SDK_V1_APPROVED_SDK_COMMIT",
            "SDK_V1_APPROVED_VERSION",
            "SDK_V1_APPROVED_WHEEL_SHA256",
            "SDK_V1_APPROVED_LICENSE_SHA256",
            "SDK_V1_APPROVED_REPOSITORY",
            "SDK_V1_APPROVED_PYPI_PROJECT",
            "SDK_V1_APPROVED_ENGINE_COMMIT",
            "SDK_V1_APPROVED_ENGINE_VERSION",
            "SDK_V1_APPROVED_ENGINE_TAG",
            "SDK_V1_APPROVED_ENGINE_LINUX_ARCHIVE_SHA256",
            "SDK_V1_APPROVED_ENGINE_MACOS_ARCHIVE_SHA256",
            "SDK_V1_APPROVED_ENGINE_CHECKSUMS_SHA256",
            "SDK_V1_APPROVED_ENGINE_COSIGN_IDENTITY",
            "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
            "technical-release-gate:",
            'test "$ENGINE_VERSION" = "$AGENT_TOOLS_ENGINE_VERSION"',
            "pull-request-validation:",
            "scripts/verify_wheel_install.py",
            "scripts.dependency_wheel_audit",
            "scripts/verify_openai_agents_runtime.py",
            "scripts/source_secret_scan.py",
            "scripts/public_release_guard.py",
            "requirements/release-quality.txt",
            "requirements/release-audit.txt",
            'pattern: "*"',
            "pip-audit --require-hashes --disable-pip",
        ):
            self.assertIn(required, workflow)
        framework = workflow.split("  framework-offline:", 1)[1].split(
            "\n  provenance:", 1
        )[0]
        self.assertIn("path: dist", framework)
        self.assertIn("realpath dist/*.whl", framework)
        self.assertNotIn("realpath download/*.whl", framework)
        self.assertIn("pip download --require-hashes", framework)
        self.assertNotIn("self-hosted", framework)
        engine_jobs = workflow.split("  engine-artifact:", 1)[1].split(
            "\n  sdk-artifact:", 1
        )[0]
        self.assertIn("git ls-remote", engine_jobs)
        self.assertNotIn("cargo ", engine_jobs)
        self.assertNotIn("rustup", engine_jobs)
        publication = workflow.split("  publish-pypi:", 1)[1].split(
            "\n  pull-request-validation:", 1
        )[0]
        self.assertIn("path: download", publication)
        self.assertIn(
            "cp download/thinkery_leanctx_sdk-1.1.0-py3-none-any.whl",
            publication,
        )
        self.assertNotIn("path: dist", publication)

    def test_release_workflow_proves_every_language_sdk(self):
        workflow = (
            Path(__file__).parents[1] / ".github/workflows/release-candidate.yml"
        ).read_text(encoding="utf-8")
        for required in (
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
            "actions/setup-go@40f1582b2485089dde7abd97c1529aa768e1baff",
            "actions/setup-java@cf277c60eb25467037889841efdb72551f06f6c3",
            "actions/setup-dotnet@67a3573c9a986a3f9c594539f4ab511d57bb3ce9",
            "npm ci --ignore-scripts",
            "npm test",
            "npm run pack:dry-run",
            "go test ./...",
            "go vet ./...",
            'rust: ["1.76.0", "stable"]',
            "cargo +${{ matrix.rust }} test --locked",
            "cargo +${{ matrix.rust }} clippy --locked --all-targets --all-features -- -D warnings",
            "mvn --batch-mode --no-transfer-progress verify",
            "dotnet build tests/Thinkery.LeanCtx.Tests/Thinkery.LeanCtx.Tests.csproj",
            "dotnet run --project tests/Thinkery.LeanCtx.Tests/Thinkery.LeanCtx.Tests.csproj",
            "dotnet pack Thinkery.LeanCtx.csproj",
        ):
            self.assertIn(required, workflow)
        proof = workflow.split("  cross-sdk-proof:", 1)[1].split(
            "\n  engine-artifact:", 1
        )[0]
        for dependency in (
            "typescript-sdk",
            "go-sdk",
            "rust-sdk",
            "jvm-sdk",
            "dotnet-sdk",
        ):
            self.assertIn(dependency, proof)
        provenance = workflow.split("  provenance:", 1)[1].split(
            "\n  publication-guard:", 1
        )[0]
        self.assertIn("needs.cross-sdk-proof.result == 'success'", provenance)
        self.assertIn("- cross-sdk-proof", provenance)
        pull_request_gate = workflow.split("  pull-request-validation:", 1)[1]
        self.assertIn("- cross-sdk-proof", pull_request_gate)

    def test_every_language_consumes_the_canonical_fingerprint_fixture(self):
        root = Path(__file__).parents[1]
        consumers = {
            "typescript": root / "packages/typescript/test/core.test.mjs",
            "go": root / "packages/go/protocol_test.go",
            "rust": root / "packages/rust/tests/sdk.rs",
            "jvm": root
            / "packages/jvm/src/test/java/com/thinkery/leanctx/ProductConformanceTest.java",
            "dotnet": root / "packages/dotnet/tests/Thinkery.LeanCtx.Tests/Program.cs",
        }
        for language, path in consumers.items():
            with self.subTest(language=language):
                content = path.read_text(encoding="utf-8")
                self.assertIn("serialization-sha256.json", content)
                self.assertNotIn(
                    "a948177b44cfd1fd22b5aa59bd4d0210510675eb0742d219ac2ac36ed09a6d75",
                    content,
                )

    def test_workspace_clean_install_drops_source_pythonpath(self):
        verifier = (
            Path(__file__).parents[1] / "scripts/verify_workspace_installed.py"
        ).read_text(encoding="utf-8")
        self.assertIn('name == "PYTHONPATH"', verifier)

    def test_package_metadata_binds_public_repository(self):
        metadata = (Path(__file__).parents[1] / "setup.cfg").read_text(encoding="utf-8")
        self.assertIn("url = https://github.com/Thinkery-AG/leanctx-sdk", metadata)
        self.assertIn("Source = https://github.com/Thinkery-AG/leanctx-sdk", metadata)

    def test_release_evidence_generator_emits_complete_required_index(self):
        values = {
            "sdk_commit": "a" * 40,
            "sdk_version": "1.0.0",
            "wheel_sha256": "b" * 64,
            "engine_commit": "c" * 40,
            "engine_version": "3.10.0",
            "engine_tag": "v3.10.0",
            "engine_linux_sha256": "d" * 64,
            "engine_macos_sha256": "e" * 64,
            "engine_release_repository": "yvgude/lean-ctx",
            "engine_release_url": (
                "https://github.com/yvgude/lean-ctx/releases/tag/v3.10.0"
            ),
            "engine_linux_asset": "lean-ctx-x86_64-unknown-linux-gnu.tar.gz",
            "engine_macos_asset": "lean-ctx-aarch64-apple-darwin.tar.gz",
            "engine_linux_archive_sha256": "6" * 64,
            "engine_macos_archive_sha256": "7" * 64,
            "engine_checksums_sha256": "8" * 64,
            "engine_cosign_identity": (
                "https://github.com/yvgude/lean-ctx/.github/workflows/"
                "release.yml@refs/tags/v3.10.0"
            ),
            "dependency_manifest_sha256": "f" * 64,
            "dependency_policy_sha256": "1" * 64,
            "build_lock_sha256": "2" * 64,
            "quality_lock_sha256": "3" * 64,
            "audit_lock_sha256": "4" * 64,
            "python_matrix": "3.9,3.10,3.11,3.12,3.13,3.14",
            "verified_python_matrix": "3.9,3.10,3.11,3.12,3.13,3.14",
            "framework_version": "openai-agents==0.8.4",
            "release_workflow": ".github/workflows/release-candidate.yml@" + "a" * 40,
            "release_run": "fixture-run",
            "signing_provenance": "GITHUB_OIDC_TRUSTED_PUBLISHING",
            "license_sha256": "5" * 64,
            "public_repository": "thinkery-ag/leanctx-sdk",
            "pypi_project": "thinkery-leanctx-sdk",
            "publication_authorization": "APPROVED",
        }
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "release-evidence/sdk-v1" / values["sdk_commit"]
            generate(output, values)
            self.assertEqual(
                {path.name for path in output.iterdir()}, set(REQUIRED_FILES)
            )
            manifest = (output / "RELEASE-MANIFEST.md").read_text(encoding="utf-8")
            self.assertIn(values["sdk_commit"], manifest)
            self.assertIn(values["framework_version"], manifest)
            self.assertIn(values["release_workflow"], manifest)
            self.assertIn(values["engine_release_url"], manifest)
            self.assertIn(values["engine_linux_archive_sha256"], manifest)
            license_status = (output / "LICENSE-STATUS.md").read_text(encoding="utf-8")
            self.assertIn("CUSTOM_LICENSE_TEXT_APPROVED", license_status)
            self.assertIn(values["license_sha256"], license_status)
            self.assertNotIn("license family: BSL", license_status)
            decision_gate = (output / "DECISION-GATE.md").read_text(encoding="utf-8")
            self.assertIn(
                "Custom-license product policy and exact release text", decision_gate
            )
            self.assertNotIn("All ten authority rows remain", decision_gate)

            blocked = dict(values, publication_authorization="PENDING")
            blocked_output = Path(root) / "blocked"
            with self.assertRaisesRegex(ValueError, "publication authorization"):
                generate(blocked_output, blocked)
            self.assertFalse(blocked_output.exists())


if __name__ == "__main__":
    unittest.main()
