"""Render an exact wheelhouse manifest as deterministic audit requirements."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from scripts.validate_wheelhouse import load_manifest


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def render(path: Path) -> str:
    data = load_manifest(path)
    artifacts = data["artifacts"]
    requirements = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("manifest artifact must be an object")
        distribution = artifact.get("distribution")
        version = artifact.get("version")
        digest = artifact.get("sha256")
        if not isinstance(distribution, str) or not distribution:
            raise ValueError("manifest artifact distribution is required")
        if not isinstance(version, str) or not version:
            raise ValueError("manifest artifact version is required")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("manifest artifact SHA-256 is required")
        requirements.add(f"{distribution}=={version} --hash=sha256:{digest}")
    if len(requirements) != len(artifacts):
        raise ValueError("manifest must contain one artifact per distribution")
    return "\n".join(sorted(requirements, key=str.casefold)) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    print(render(args.manifest.resolve(strict=True)), end="")


if __name__ == "__main__":
    main()
