from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_language_versions import versions


class LanguageVersionContractTests(unittest.TestCase):
    def test_repository_versions_are_synchronized(self) -> None:
        root = Path(__file__).resolve().parents[1]
        found = versions(root)
        self.assertEqual(set(found.values()), {"1.1.0"})
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
