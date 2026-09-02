"""Fail-closed integrity, content, secret, and license scan for a wheelhouse."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from email import message_from_bytes
from pathlib import Path, PurePosixPath

from scripts.validate_wheelhouse import load_manifest

_MAX_ENTRY = 100 * 1024 * 1024
_MAX_WHEEL = 500 * 1024 * 1024
_PRIVATE_KEY = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+"
    rb"[A-Za-z0-9+/=\r\n]{100,}\s+"
    rb"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
_TOKENS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
)
_HOST_PATHS = (b"/Users/", b"/private/tmp/", b"C:\\Users\\")
_FORBIDDEN_LICENSE = re.compile(r"(?:^|[^A-Z])(AGPL|GPL|SSPL)(?:[^A-Z]|$)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _declared_licenses(metadata: bytes) -> list[str]:
    message = message_from_bytes(metadata)
    values = list(message.get_all("License", []))
    values.extend(message.get_all("License-Expression", []))
    values.extend(
        value
        for value in message.get_all("Classifier", [])
        if value.startswith("License ::")
    )
    return sorted(
        {value.strip() for value in values if value.strip()}, key=str.casefold
    )


def audit(
    manifest_path: Path, wheelhouse: Path, policy_path: Path
) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if (
        set(policy)
        != {
            "accepted_build_path_entries",
            "build_path_rationale",
            "schema_version",
        }
        or policy["schema_version"] != 1
    ):
        raise ValueError("unsupported dependency audit policy")
    accepted_build_paths = policy["accepted_build_path_entries"]
    rationale = policy["build_path_rationale"]
    if (
        not isinstance(accepted_build_paths, list)
        or not all(isinstance(item, str) for item in accepted_build_paths)
        or len(accepted_build_paths) != len(set(accepted_build_paths))
        or not isinstance(rationale, str)
        or not rationale.startswith("ACCEPTED_WITH_RATIONALE:")
    ):
        raise ValueError("invalid dependency audit policy")
    expected = {item["filename"]: item["sha256"] for item in manifest["artifacts"]}
    actual = {path.name: path for path in wheelhouse.glob("*.whl")}
    if set(actual) != set(expected):
        raise ValueError("wheelhouse filenames differ from manifest")

    entries = 0
    build_path_entries: list[str] = []
    licenses: dict[str, list[str]] = {}
    review_required: list[str] = []
    for filename in sorted(actual):
        wheel = actual[filename]
        if wheel.is_symlink() or not wheel.is_file():
            raise ValueError(f"wheel must be a regular non-symlink: {filename}")
        if _sha256(wheel) != expected[filename]:
            raise ValueError(f"wheel digest mismatch: {filename}")
        try:
            archive = zipfile.ZipFile(wheel)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"unreadable wheel: {filename}") from exc
        with archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if not names or len(names) != len(set(names)):
                raise ValueError(
                    f"wheel entries must be non-empty and unique: {filename}"
                )
            if sum(info.file_size for info in infos) > _MAX_WHEEL:
                raise ValueError(f"uncompressed wheel is too large: {filename}")
            metadata_names = [
                name for name in names if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise ValueError(f"wheel must contain exactly one METADATA: {filename}")
            declared = _declared_licenses(archive.read(metadata_names[0]))
            licenses[filename] = declared
            if not declared:
                review_required.append(f"{filename}:license-not-declared")
            if any(_FORBIDDEN_LICENSE.search(value.upper()) for value in declared):
                raise ValueError(f"forbidden copyleft license declaration: {filename}")
            for info in infos:
                name = info.filename
                path = PurePosixPath(name)
                mode = info.external_attr >> 16
                if name.startswith("/") or "\\" in name:
                    raise ValueError(f"invalid wheel entry: {filename}:{name}")
                if any(part in {"", ".", ".."} for part in path.parts):
                    raise ValueError(f"unsafe wheel entry: {filename}:{name}")
                if stat.S_ISLNK(mode):
                    raise ValueError(f"wheel symlink is forbidden: {filename}:{name}")
                if path.suffix in {".pth", ".egg-link"}:
                    raise ValueError(
                        f"executable import hook is forbidden: {filename}:{name}"
                    )
                if info.file_size > _MAX_ENTRY:
                    raise ValueError(f"wheel entry is too large: {filename}:{name}")
                if info.is_dir():
                    continue
                content = archive.read(info)
                if _PRIVATE_KEY.search(content) or any(
                    pattern.search(content) for pattern in _TOKENS
                ):
                    raise ValueError(f"secret-like payload: {filename}:{name}")
                if any(marker in content for marker in _HOST_PATHS):
                    build_path_entries.append(f"{filename}:{name}")
                entries += 1

    if review_required:
        raise ValueError("dependency license declaration missing")
    if sorted(build_path_entries) != sorted(accepted_build_paths):
        raise ValueError("build path findings differ from reviewed policy")
    return {
        "build_path_entries": build_path_entries,
        "entries_checked": entries,
        "finding_classification": {
            "build_path_entries": rationale,
            "license_not_declared": "BLOCK",
        },
        "licenses": licenses,
        "review_required": review_required,
        "status": "PASS",
        "wheels_checked": len(actual),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    args = parser.parse_args()
    result = audit(
        args.manifest.resolve(strict=True),
        args.wheelhouse.resolve(strict=True),
        args.policy.resolve(strict=True),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
