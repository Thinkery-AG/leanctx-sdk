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


def agent_tools_versions(root: Path) -> dict[str, str]:
    """Read independent client and installer pins; package semver is separate."""
    declarations = {
        "python": ("src/leanctx_sdk/agent.py", "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION"),
        "typescript": (
            "packages/typescript/src/agent.ts",
            "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
        ),
        "go": ("packages/go/version.go", "SupportedAgentToolsEngineVersion"),
        "rust": ("packages/rust/src/agent.rs", "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION"),
        "jvm": (
            "packages/jvm/src/main/java/com/thinkery/leanctx/AgentContext.java",
            "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
        ),
        "jvm_public": (
            "packages/jvm/src/main/java/com/thinkery/leanctx/LeanCtx.java",
            "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
        ),
        "dotnet": (
            "packages/dotnet/src/Protocol.cs",
            "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
        ),
    }
    found = {
        name: _match(root / path, rf'\b{constant}\b[^=\n]*=\s*"([^"]+)"')
        for name, (path, constant) in declarations.items()
    }
    found["contract"] = json.loads(
        (root / "contracts/agent-tools-v1.json").read_text(encoding="utf-8")
    )["engine_version"]
    for package in (
        "thinkery-leanctx-engine",
        "thinkery-leanctx-engine-cuda",
        "thinkery-leanctx-engine-windows-gnu",
    ):
        found[package] = _match(root / "setup.cfg", rf"^\s*{package}==([^;\s]+)")
    return found


def check_agent_tools_versions(
    root: Path, engine_manifest: Path | None = None
) -> dict[str, str]:
    found = agent_tools_versions(root)
    if engine_manifest is not None:
        found["engine_candidate"] = _match(
            engine_manifest, r'(?ms)^\[package\].*?^version\s*=\s*"([^"]+)"'
        )
        found["engine_lock"] = _match(
            engine_manifest.with_name("Cargo.lock"),
            r'(?ms)^\[\[package\]\]\s*name\s*=\s*"lean-ctx"\s*version\s*=\s*"([^"]+)"',
        )
    if len(set(found.values())) != 1:
        raise ValueError(f"Agent Tools Engine pairing mismatch: {found}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--expected")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--engine-manifest",
        type=Path,
        help="optional local Engine Cargo.toml to pair before building",
    )
    args = parser.parse_args()
    found = versions(args.repository.resolve(strict=True))
    unique = set(found.values())
    if len(unique) != 1:
        raise SystemExit(f"SDK package version mismatch: {found}")
    version = unique.pop()
    if args.expected is not None and version != args.expected:
        raise SystemExit(
            f"tag version {args.expected!r} != package version {version!r}"
        )
    try:
        engine_versions = check_agent_tools_versions(
            args.repository.resolve(strict=True), args.engine_manifest
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"version={version}\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "version": version,
                "packages": found,
                "agent_tools_engines": engine_versions,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
