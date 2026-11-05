"""Verify that every SDK package carries the same release version."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
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
        "go": ("packages/go/version.go", "SupportedAgentToolsInstanceVersion"),
        "rust": ("packages/rust/src/agent.rs", "SUPPORTED_AGENT_TOOLS_INSTANCE_VERSION"),
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


def _engine_binary_version(binary: Path) -> str:
    try:
        executable = binary.resolve(strict=True)
        if not executable.is_file():
            raise ValueError(f"Engine candidate is not a file: {binary}")
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            f"could not inspect Engine candidate {binary}: {error}"
        ) from error
    if result.returncode != 0:
        raise ValueError(
            f"Engine candidate --version exited {result.returncode}: {binary}"
        )
    output = result.stdout.strip()
    match = re.fullmatch(
        r"lean-ctx\s+v?(\d+\.\d+\.\d+)"
        r"(?: \(official(?:, https://github\.com/yvgude/lean-ctx)?\))?",
        output,
    )
    if match is None:
        raise ValueError(
            f"Engine candidate returned an unrecognized --version value: {output!r}"
        )
    return match.group(1)


def check_agent_tools_versions(
    root: Path,
    engine_manifest: Path | None = None,
    engine_binary: Path | None = None,
    configured_engine_version: str | None = None,
    configured_agent_tools_version: str | None = None,
) -> dict[str, str]:
    found = agent_tools_versions(root)
    expected = found["contract"]
    if engine_manifest is not None:
        found["engine_manifest"] = _match(
            engine_manifest, r'(?ms)^\[package\].*?^version\s*=\s*"([^"]+)"'
        )
        found["engine_lock"] = _match(
            engine_manifest.with_name("Cargo.lock"),
            r'(?ms)^\[\[package\]\]\s*'
            r'name\s*=\s*"lean-ctx"\s*version\s*=\s*"([^"]+)"',
        )
    if engine_binary is not None:
        found["engine_binary"] = _engine_binary_version(engine_binary)
    if configured_engine_version is not None:
        found["release_engine"] = configured_engine_version
    if configured_agent_tools_version is not None:
        found["release_agent_tools"] = configured_agent_tools_version
    if any(version != expected for version in found.values()):
        raise ValueError(
            f"Agent Tools Engine pairing mismatch: contract requires {expected}; "
            f"found {found}"
        )
    return found


def _compatibility():
    """The one matrix validator (`agent_tools_compatibility.py`, next to this
    file), loaded by path: this script runs both as a file and as a module."""
    spec = importlib.util.spec_from_file_location(
        "agent_tools_compatibility",
        Path(__file__).with_name("agent_tools_compatibility.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_compatibility_matrix(
    root: Path, sdk_version: str, engine_version: str | None = None
) -> dict[str, object]:
    """Hold the matrix, the six mirrors and the Agent Tools contract together.

    Nothing about the matrix is decided here: the shared validator does it,
    the same one the Engine's release gate uses.
    """
    return _compatibility().check(
        root, sdk_version=sdk_version, engine_version=engine_version
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--expected")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--engine-manifest",
        type=Path,
        help="optional local Engine Cargo.toml; its adjacent Cargo.lock is also checked",
    )
    parser.add_argument(
        "--engine-binary",
        type=Path,
        help=(
            "optional exact Engine candidate whose --version output "
            "must match the contract"
        ),
    )
    parser.add_argument(
        "--configured-engine-version",
        help=(
            "release workflow Engine version pin, checked against "
            "the Agent Tools contract"
        ),
    )
    parser.add_argument(
        "--configured-agent-tools-version",
        help="release workflow Agent Tools version pin, checked against the contract",
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
            args.repository.resolve(strict=True),
            engine_manifest=args.engine_manifest,
            engine_binary=args.engine_binary,
            configured_engine_version=args.configured_engine_version,
            configured_agent_tools_version=args.configured_agent_tools_version,
        )
        # The Engine a release pins forms a pair with what the contract
        # speaks; that whole pair has to be a row of the matrix.
        compatibility = check_compatibility_matrix(
            args.repository.resolve(strict=True),
            version,
            engine_version=args.configured_engine_version,
        )
    except (OSError, ValueError, KeyError) as error:
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
                "agent_tools_compatibility": compatibility,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
