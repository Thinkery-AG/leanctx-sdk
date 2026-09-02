"""Validate the certified offline Agents wheelhouse without network I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Mapping


_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$", re.IGNORECASE)
_REQUIRED_KEYS = {
    "artifacts",
    "artifacts_digest",
    "environment",
    "requirements",
    "schema_version",
    "source_overrides",
    "stack",
    "stack_version",
}
_CERTIFIED_ENVIRONMENT = {"python": "3.11", "platform": "macOS 11+ arm64"}
_CERTIFIED_REQUIREMENTS = [
    {"distribution": "openai-agents", "version": "0.8.4"},
    {"distribution": "openai", "version": "2.19.0"},
    {"distribution": "pydantic", "version": "2.12.3"},
    {"distribution": "requests", "version": "2.33.0"},
    {"distribution": "urllib3", "version": "2.7.0"},
]
_CERTIFIED_SOURCE_OVERRIDES = [
    {
        "distribution": "requests",
        "version": "2.33.0",
        "repository": "https://github.com/psf/requests.git",
        "commit": "bc04dfd6dad4cb02cd92f5daa81eb562d280a761",
    },
    {
        "distribution": "urllib3",
        "version": "2.7.0",
        "repository": "https://github.com/urllib3/urllib3.git",
        "commit": "9a950b92d999f906b6020bb2d1076ee56cddd5d2",
    },
]
_CERTIFIED_ARTIFACTS_DIGEST = (
    "2a0a53adb0bf16f78653b001db8bf667bfb9c5ab8fe9d5344544471a8a1e6cd5"
)
_CERTIFIED_ARTIFACT_COUNT = 41


def _canonical_name(value: object) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError("distribution names must be ASCII package names")
    return re.sub(r"[-_.]+", "-", value.lower())


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid manifest: {path}") from exc
    if not isinstance(value, dict) or set(value) != _REQUIRED_KEYS:
        raise ValueError("manifest keys do not match the wheelhouse schema")
    if value["schema_version"] != 1:
        raise ValueError("unsupported wheelhouse manifest schema")
    return value


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_manifest(path: Path) -> Mapping[str, object]:
    manifest = _read_json(path)
    if _canonical_name(manifest["stack"]) != "openai-agents":
        raise ValueError("manifest stack must be openai-agents")
    if manifest["stack_version"] != "0.8.4":
        raise ValueError("manifest stack version must be 0.8.4")
    if manifest["environment"] != _CERTIFIED_ENVIRONMENT:
        raise ValueError("manifest environment differs from certified scope")
    if manifest["requirements"] != _CERTIFIED_REQUIREMENTS:
        raise ValueError("manifest requirements differ from certified pins")
    if manifest["source_overrides"] != _CERTIFIED_SOURCE_OVERRIDES:
        raise ValueError("manifest source provenance differs from certified commits")
    for source in manifest["source_overrides"]:
        if not _HEX40.fullmatch(source["commit"]):
            raise ValueError("source override commit must be exact")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != _CERTIFIED_ARTIFACT_COUNT:
        raise ValueError("manifest must contain the complete certified wheel closure")
    digest = _canonical_digest(artifacts)
    if manifest["artifacts_digest"] != digest or digest != _CERTIFIED_ARTIFACTS_DIGEST:
        raise ValueError("manifest artifact closure digest mismatch")

    names = set()
    filenames = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
            "distribution",
            "filename",
            "sha256",
            "version",
        }:
            raise ValueError("invalid artifact record")
        name = _canonical_name(artifact["distribution"])
        filename = artifact["filename"]
        version = artifact["version"]
        digest = artifact["sha256"]
        if name in names or filename in filenames:
            raise ValueError("artifact names and filenames must be unique")
        if not isinstance(version, str) or not version:
            raise ValueError("artifact version is invalid")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".whl")
            or not isinstance(digest, str)
            or not _HEX64.fullmatch(digest)
        ):
            raise ValueError("artifact filename/hash is invalid")
        names.add(name)
        filenames.add(filename)
    required_names = {
        _canonical_name(item["distribution"]) for item in _CERTIFIED_REQUIREMENTS
    }
    if not required_names.issubset(names):
        raise ValueError("artifact closure does not cover direct requirements")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_wheelhouse(manifest_path: Path, wheelhouse: Path | None = None) -> dict:
    manifest = load_manifest(manifest_path)
    artifacts = manifest["artifacts"]
    if wheelhouse is None or not wheelhouse.is_dir():
        raise ValueError("a wheelhouse directory is required")
    expected = {artifact["filename"]: artifact for artifact in artifacts}
    actual = {path.name: path for path in wheelhouse.iterdir() if path.suffix == ".whl"}
    if set(actual) != set(expected):
        raise ValueError("wheelhouse files do not match the manifest")
    for filename, artifact in expected.items():
        path = actual[filename]
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha256(path) != artifact["sha256"]
        ):
            raise ValueError(f"wheel hash mismatch: {filename}")
    return {
        "artifacts_checked": len(artifacts),
        "requirements": len(manifest["requirements"]),
        "status": "valid",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    result = validate_wheelhouse(args.manifest.resolve(strict=True), args.wheelhouse)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
