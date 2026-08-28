"""Reject private paths and host-specific residue from the public SDK source tree."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable


_FORBIDDEN_PREFIXES = (
    "docs/internal/",
    "legal/",
    "research/",
    "src/leanctx_sdk/internal/",
    "src/leanctx_sdk/research/",
)
_FORBIDDEN_NAMES = {
    "LICENSING-STATUS.md",
    "P4-RELEASE-CANDIDATE.md",
    "R2-PARITY.md",
    "RECOVERY-PROVENANCE.md",
    "V1-RELEASE-CANDIDATE.md",
}
_CONTENT_PATTERNS = (
    re.compile(r"/(?:Users|home)/[^/\s]+/(?:Documents|Downloads)/"),
    re.compile(r"/private/tmp/" + r"leanctx-final"),
    re.compile(r"\bP[89]_RESEARCH_COMPLETE\b"),
    re.compile(r"\bCANONICAL_" + r"P0_P9\b"),
)


class PublicReleaseGuardError(ValueError):
    """A tracked input violates the public-source boundary."""


def check_files(repository: Path, files: Iterable[Path]) -> dict[str, object]:
    checked = 0
    for relative in sorted(files, key=lambda value: value.as_posix().casefold()):
        name = PurePosixPath(relative.as_posix()).as_posix()
        if name.startswith(_FORBIDDEN_PREFIXES) or PurePosixPath(name).name in _FORBIDDEN_NAMES:
            raise PublicReleaseGuardError(f"forbidden public-source path: {name}")
        path = repository / relative
        if not path.is_file() or path.is_symlink():
            continue
        checked += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in _CONTENT_PATTERNS:
            if pattern.search(text):
                raise PublicReleaseGuardError(
                    f"private marker in public-source path: {name}"
                )
    return {"files_checked": checked, "status": "PASS"}


def tracked_files(repository: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repository = args.repository.resolve(strict=True)
    print(json.dumps(check_files(repository, tracked_files(repository)), sort_keys=True))


if __name__ == "__main__":
    main()
