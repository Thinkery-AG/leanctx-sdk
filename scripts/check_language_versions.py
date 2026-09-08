"""Verify that every SDK package carries the same release version."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def _match(path: Path, pattern: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ValueError(f"version not found: {path}")
    return match.group(1)


def versions(root: Path) -> dict[str, str]:
    maven = ET.parse(root / "packages/jvm/pom.xml").getroot()
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    jvm = maven.findtext("m:version", namespaces=namespace)
    if jvm is None:
        raise ValueError("JVM project version not found")
    dotnet = ET.parse(root / "packages/dotnet/Thinkery.LeanCtx.csproj").getroot()
    dotnet_version = dotnet.findtext("./PropertyGroup/Version")
    if dotnet_version is None:
        raise ValueError(".NET package version not found")
    return {
        "python": _match(root / "setup.cfg", r"^version\s*=\s*([^\s]+)$"),
        "typescript": json.loads(
            (root / "packages/typescript/package.json").read_text(encoding="utf-8")
        )["version"],
        "go": _match(root / "packages/go/version.go", r'^\s*Version\s*=\s*"([^"]+)"'),
        "rust": _match(
            root / "packages/rust/Cargo.toml",
            r"(?ms)^\[package\].*?^version\s*=\s*\"([^\"]+)\"",
        ),
        "jvm": jvm,
        "dotnet": dotnet_version,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--expected")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    found = versions(args.repository.resolve(strict=True))
    unique = set(found.values())
    if len(unique) != 1:
        raise SystemExit(f"SDK package version mismatch: {found}")
    version = unique.pop()
    if args.expected is not None and version != args.expected:
        raise SystemExit(f"tag version {args.expected!r} != package version {version!r}")
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"version={version}\n")
    print(json.dumps({"status": "PASS", "version": version, "packages": found}, sort_keys=True))


if __name__ == "__main__":
    main()
