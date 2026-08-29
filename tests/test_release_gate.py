import base64
import csv
import hashlib
import io
import json
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


DIST_INFO = "leanctx_sdk-1.0.0.dist-info"


def _record_hash(data):
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _wheel(path, *, extra=None, python_requires=">=3.9,<3.13"):
    metadata = f"""Metadata-Version: 2.1
Name: leanctx-sdk
Version: 1.0.0
Requires-Python: {python_requires}
Provides-Extra: openai-agents
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
            "LeanCTX SDK v1.0.0 — Third-Party Notices\n"
            "The exact 41-wheel audit includes openai-agents 0.8.4 — MIT.\n"
        ).encode(
            "utf-8"
        ),
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
        modern = (
            'openai==2.19.0; python_version >= "3.10" '
            'and extra == "openai-agents"'
        )
        self.assertEqual(
            _canonical_requirement(legacy), _canonical_requirement(modern)
        )
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
            {
                "leanctx_sdk/key.txt": b"-----BEGIN "
                + b"PRIVATE KEY-----"
            },
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
        self.assertTrue(any(line.startswith("openai-agents==0.8.4 ") for line in requirements))
        self.assertTrue(any(line.startswith("requests==2.33.0 ") for line in requirements))
        self.assertTrue(any(line.startswith("urllib3==2.7.0 ") for line in requirements))
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
            with patch(
                "scripts.dependency_wheel_audit.load_manifest", return_value=data
            ), self.assertRaisesRegex(ValueError, "forbidden copyleft"):
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
            with patch(
                "scripts.dependency_wheel_audit.load_manifest", return_value=data
            ), self.assertRaisesRegex(ValueError, "reviewed policy"):
                audit_dependencies(manifest, wheelhouse, policy)

    def test_release_workflow_is_immutable_and_binds_required_gates(self):
        workflow = (
            Path(__file__).parents[1]
            / ".github/workflows/release-candidate.yml"
        ).read_text(encoding="utf-8")
        self.assertNotRegex(workflow, r"uses:\s+[^\s]+@(v\d+|main|master)\b")
        for required in (
            "ENGINE_COMMIT: 5a90893092a7d31a8dae41ea6710b5a0c5048d15",
            "ENGINE_VERSION: 3.9.20",
            "ENGINE_TAG: PENDING_PUBLIC_ENGINE_RELEASE",
            "PYTHONPATH: src:.",
            "static-quality:",
            "source-secret-scan:",
            "engine-artifact:",
            "engine-macos-artifact:",
            "ENGINE_MACOS_ARM64_SHA256: e98b3367feea41298469a27c4e87fea7956117bc5b2c48072e6e7d55e0b08857",
            "engine-linux-x86_64-",
            "engine-macos-arm64-",
            "sdk-artifact:",
            "installed-engine-contract:",
            "dependency-audit:",
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
            "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
            "technical-release-gate:",
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
        publication = workflow.split("  publish-pypi:", 1)[1].split(
            "\n  pull-request-validation:", 1
        )[0]
        self.assertIn("path: download", publication)
        self.assertIn("cp download/leanctx_sdk-1.0.0-py3-none-any.whl", publication)
        self.assertNotIn("path: dist", publication)

    def test_workspace_clean_install_drops_source_pythonpath(self):
        verifier = (
            Path(__file__).parents[1] / "scripts/verify_workspace_installed.py"
        ).read_text(encoding="utf-8")
        self.assertIn('name == "PYTHONPATH"', verifier)

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
            "dependency_manifest_sha256": "f" * 64,
            "dependency_policy_sha256": "1" * 64,
            "build_lock_sha256": "2" * 64,
            "quality_lock_sha256": "3" * 64,
            "audit_lock_sha256": "4" * 64,
            "python_matrix": "3.9,3.10,3.11,3.12",
            "verified_python_matrix": "3.9,3.10,3.11,3.12",
            "framework_version": "openai-agents==0.8.4",
            "release_workflow": ".github/workflows/release-candidate.yml@" + "a" * 40,
            "release_run": "fixture-run",
            "signing_provenance": "GITHUB_OIDC_TRUSTED_PUBLISHING",
            "license_sha256": "5" * 64,
            "public_repository": "thinkery-ag/leanctx-sdk",
            "pypi_project": "leanctx-sdk",
            "publication_authorization": "APPROVED",
        }
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "release-evidence/sdk-v1" / values["sdk_commit"]
            generate(output, values)
            self.assertEqual(
                {path.name for path in output.iterdir()}, set(REQUIRED_FILES)
            )
            manifest = (output / "RELEASE-MANIFEST.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(values["sdk_commit"], manifest)
            self.assertIn(values["framework_version"], manifest)
            self.assertIn(values["release_workflow"], manifest)
            license_status = (output / "LICENSE-STATUS.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("CUSTOM_LICENSE_TEXT_APPROVED", license_status)
            self.assertIn(values["license_sha256"], license_status)
            self.assertNotIn("license family: BSL", license_status)
            decision_gate = (output / "DECISION-GATE.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Custom-license product policy and exact release text", decision_gate)
            self.assertNotIn("All ten authority rows remain", decision_gate)

            blocked = dict(values, publication_authorization="PENDING")
            blocked_output = Path(root) / "blocked"
            with self.assertRaisesRegex(ValueError, "publication authorization"):
                generate(blocked_output, blocked)
            self.assertFalse(blocked_output.exists())


if __name__ == "__main__":
    unittest.main()
