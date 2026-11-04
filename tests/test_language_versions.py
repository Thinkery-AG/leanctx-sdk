from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_language_versions import check_agent_tools_versions, versions


class LanguageVersionContractTests(unittest.TestCase):
    def test_current_clients_and_installer_require_the_same_engine(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(set(check_agent_tools_versions(root).values()), {"3.12.1"})

    def test_release_configured_versions_must_match_contract_before_build(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            check_agent_tools_versions(
                root,
                configured_engine_version="3.12.1",
                configured_agent_tools_version="3.12.1",
            )["release_engine"],
            "3.12.1",
        )
        with self.assertRaisesRegex(
            ValueError, r"Engine pairing mismatch: contract requires 3\.12\.1"
        ):
            check_agent_tools_versions(
                root,
                configured_engine_version="3.10.5",
                configured_agent_tools_version="3.10.5",
            )

    def test_candidate_binary_version_is_checked_against_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        binary = Path(sys.executable).resolve(strict=True)
        argv = [str(binary), "--version"]
        with patch("scripts.check_language_versions.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                argv,
                0,
                stdout="lean-ctx 3.12.1 (official, https://github.com/yvgude/lean-ctx)\n",
                stderr="",
            )
            self.assertEqual(
                check_agent_tools_versions(root, engine_binary=binary)[
                    "engine_binary"
                ],
                "3.12.1",
            )
            self.assertEqual(run.call_args.args, (argv,))
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            run.return_value = subprocess.CompletedProcess(
                argv, 0, stdout="lean-ctx 3.10.5\n", stderr=""
            )
            with self.assertRaisesRegex(ValueError, "Engine pairing mismatch"):
                check_agent_tools_versions(root, engine_binary=binary)
            run.return_value = subprocess.CompletedProcess(
                argv, 1, stdout="lean-ctx 3.12.1\n", stderr="failed"
            )
            with self.assertRaisesRegex(ValueError, "--version exited 1"):
                check_agent_tools_versions(root, engine_binary=binary)
            run.return_value = subprocess.CompletedProcess(
                argv, 0, stdout="unrecognized version\n", stderr=""
            )
            with self.assertRaisesRegex(ValueError, "unrecognized --version"):
                check_agent_tools_versions(root, engine_binary=binary)

    def test_rejects_stale_client_installer_and_engine_before_build(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        for changed in (
            "packages/dotnet/src/Protocol.cs",
            "setup.cfg",
            "engine.toml",
            "Cargo.lock",
        ):
            with (
                self.subTest(changed=changed),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                for name in (
                    "src/leanctx_sdk/agent.py",
                    "packages/typescript/src/agent.ts",
                    "packages/go/version.go",
                    "packages/rust/src/agent.rs",
                    "packages/jvm/src/main/java/com/thinkery/leanctx/AgentContext.java",
                    "packages/jvm/src/main/java/com/thinkery/leanctx/LeanCtx.java",
                    "packages/dotnet/src/Protocol.cs",
                    "contracts/agent-tools-v1.json",
                ):
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(repository / name, destination)
                shutil.copyfile(repository / "setup.cfg", root / "setup.cfg")
                manifest = root / "engine.toml"
                manifest.write_text(
                    '[package]\nname = "lean-ctx"\nversion = "3.12.1"\n'
                )
                (root / "Cargo.lock").write_text(
                    'version = 4\n[[package]]\nname = "lean-ctx"\nversion = "3.12.1"\n'
                )
                check_agent_tools_versions(root, manifest)
                path = root / changed
                path.write_text(path.read_text().replace("3.12.1", "3.11.1"))
                with self.assertRaisesRegex(ValueError, "Engine pairing mismatch"):
                    check_agent_tools_versions(root, manifest)

    def test_repository_versions_are_synchronized(self) -> None:
        root = Path(__file__).resolve().parents[1]
        found = versions(root)
        self.assertEqual(set(found.values()), {"1.3.0"})
        self.assertEqual(
            set(found),
            {"python", "typescript", "go", "rust", "jvm", "dotnet"},
        )

    def test_parser_reports_each_package_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in (
                "packages/typescript",
                "packages/go",
                "packages/rust",
                "packages/jvm",
                "packages/dotnet",
            ):
                (root / directory).mkdir(parents=True)
            (root / "setup.cfg").write_text("[metadata]\nversion = 2.0.0\n")
            (root / "packages/typescript/package.json").write_text(
                json.dumps({"version": "2.0.1"})
            )
            (root / "packages/go/version.go").write_text(
                'package leanctx\nconst (\n\tVersion = "2.0.2"\n)\n'
            )
            (root / "packages/rust/Cargo.toml").write_text(
                '[package]\nname = "sdk"\nversion = "2.0.3"\n'
            )
            (root / "packages/jvm/pom.xml").write_text(
                '<project xmlns="http://maven.apache.org/POM/4.0.0">'
                "<version>2.0.4</version></project>"
            )
            (root / "packages/dotnet/Thinkery.LeanCtx.csproj").write_text(
                "<Project><PropertyGroup><Version>2.0.5</Version>"
                "</PropertyGroup></Project>"
            )
            self.assertEqual(
                versions(root),
                {
                    "python": "2.0.0",
                    "typescript": "2.0.1",
                    "go": "2.0.2",
                    "rust": "2.0.3",
                    "jvm": "2.0.4",
                    "dotnet": "2.0.5",
                },
            )


if __name__ == "__main__":
    unittest.main()
