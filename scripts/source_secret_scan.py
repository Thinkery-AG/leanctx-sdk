"""Deterministic fail-closed secret scan for Git-tracked SDK release inputs."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Iterable


MAX_FILE_BYTES = 8 * 1024 * 1024
PATTERNS = (
    ("private_key", re.compile(br"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_token", re.compile(br"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(br"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("gitlab_token", re.compile(br"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("pypi_token", re.compile(br"\bpypi-[A-Za-z0-9_-]{50,}\b")),
    ("aws_access_key", re.compile(br"\bAKIA[A-Z0-9]{16}\b")),
    ("slack_token", re.compile(br"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)


class SecretScanError(ValueError):
    """A tracked release input cannot be certified secret-free."""


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [Path(os.fsdecode(item)) for item in result.stdout.split(b"\0") if item]


def scan_files(root: Path, relative_paths: Iterable[Path]) -> dict[str, object]:
    files = sorted(set(relative_paths), key=lambda path: path.as_posix())
    bytes_scanned = 0
    for relative in files:
        if relative.is_absolute() or ".." in relative.parts:
            raise SecretScanError("tracked path escapes repository")
        candidate = root / relative
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SecretScanError(f"tracked path is not a regular file: {relative}")
        if metadata.st_size > MAX_FILE_BYTES:
            raise SecretScanError(f"tracked file exceeds scan bound: {relative}")
        content = candidate.read_bytes()
        bytes_scanned += len(content)
        for label, pattern in PATTERNS:
            if pattern.search(content):
                raise SecretScanError(f"{label} pattern in tracked file: {relative}")
    return {
        "bytes_scanned": bytes_scanned,
        "files_scanned": len(files),
        "scanner_schema": 1,
        "status": "PASS",
    }


def scan_repository(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    result = scan_files(root, tracked_files(root))
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    result["git_commit"] = commit
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(scan_repository(args.repository), sort_keys=True))


if __name__ == "__main__":
    main()
