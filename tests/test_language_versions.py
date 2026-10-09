from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.check_language_versions import check_agent_tools_versions, versions


class LanguageVersionContractTests(unittest.TestCase):
    def test_current_clients_and_installer_require_the_same_engine(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(set(check_agent_tools_versions(root).values()), {"3.11.2"})

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
                    '[package]\nname = "lean-ctx"\nversion = "3.11.2"\n'
                )
                (root / "Cargo.lock").write_text(
                    'version = 4\n[[package]]\nname = "lean-ctx"\nversion = "3.11.2"\n'
                )
                check_agent_tools_versions(root, manifest)
                path = root / changed
                path.write_text(path.read_text().replace("3.11.2", "3.11.3"))
                with self.assertRaisesRegex(ValueError, "Engine pairing mismatch"):
                    check_agent_tools_versions(root, manifest)

    def test_repository_versions_are_synchronized(self) -> None:
        root = Path(__file__).resolve().parents[1]
        found = versions(root)
        self.assertEqual(set(found.values()), {"1.2.0"})
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
