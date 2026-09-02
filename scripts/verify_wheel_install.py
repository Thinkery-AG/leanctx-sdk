"""Install one supplied SDK wheel in a fresh venv and run the real-Engine gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path
from typing import Sequence

try:
    from scripts.inspect_release_artifact import inspect_wheel
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from inspect_release_artifact import inspect_wheel


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _required_file(value: str, label: str, suffix: str = "") -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{label} must be an absolute path")
    try:
        path = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"{label} must name an existing file") from exc
    if not path.is_file() or (suffix and path.suffix != suffix):
        raise SystemExit(f"{label} must name a {suffix or 'regular'} file")
    return path


def _venv_python(root: Path) -> Path:
    candidate = root / ("Scripts" if os.name == "nt" else "bin") / "python"
    if not candidate.is_file():
        raise RuntimeError(f"venv Python was not created: {candidate}")
    return candidate


def _offline_env() -> dict:
    environment = os.environ.copy()
    for name in tuple(environment):
        upper = name.upper()
        if (
            name == "PYTHONPATH"
            or upper.endswith("_API_KEY")
            or upper.endswith("_ACCESS_TOKEN")
            or upper.endswith("_CREDENTIALS")
            or upper in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"}
        ):
            environment.pop(name, None)
    environment["PIP_NO_INDEX"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run(command: Sequence[str], *, cwd: Path, environment: dict) -> None:
    subprocess.run(command, check=True, cwd=str(cwd), env=environment)


def _verify_package_manager_integrity(
    python: Path, *, cwd: Path, environment: dict
) -> None:
    _run(
        [str(python), "-m", "pip", "check"],
        cwd=cwd,
        environment=environment,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        check=True,
        cwd=str(repository),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _require_clean_worktree(repository: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        check=True,
        cwd=str(repository),
        capture_output=True,
        text=True,
    )
    if result.stdout:
        raise SystemExit("SDK checkout must be clean for the installed gate")


def _validate_wheel_source(wheel: Path, repository: Path, commit: str) -> None:
    try:
        inspect_wheel(wheel)
    except ValueError as exc:
        raise SystemExit(f"wheel inventory validation failed: {exc}") from exc
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", "src/leanctx_sdk"],
        check=True,
        cwd=str(repository),
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    expected = {}
    for source_path in listing:
        package_path = source_path.removeprefix("src/")
        expected[package_path] = subprocess.run(
            ["git", "show", f"{commit}:{source_path}"],
            check=True,
            cwd=str(repository),
            capture_output=True,
        ).stdout
    try:
        with zipfile.ZipFile(wheel) as archive:
            actual_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("leanctx_sdk/") and not name.endswith("/")
            )
            if actual_names != sorted(expected):
                raise SystemExit(
                    "wheel package contents differ from expected SDK commit"
                )
            for name, content in expected.items():
                if archive.read(name) != content:
                    raise SystemExit(
                        f"wheel package file differs from SDK commit: {name}"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit("--wheel is not a readable wheel archive") from exc


def _validate_direct_url(value: str, expected_sha256: str, expected_url: str) -> None:
    try:
        data = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("installed wheel provenance is not valid JSON") from exc
    if not isinstance(data, dict) or data.get("url") != expected_url:
        raise SystemExit("installed wheel provenance URL mismatch")
    archive_info = data.get("archive_info", {})
    if not isinstance(archive_info, dict):
        raise SystemExit("installed wheel archive provenance is invalid")
    hashes = archive_info.get("hashes", {})
    if not isinstance(hashes, dict):
        raise SystemExit("installed wheel provenance hashes are invalid")
    modern_hash = hashes.get("sha256")
    legacy_hash = archive_info.get("hash")
    valid_modern = modern_hash == expected_sha256
    valid_legacy = legacy_hash == f"sha256={expected_sha256}"
    if not valid_modern and not valid_legacy:
        raise SystemExit("installed wheel provenance hash mismatch")


def _validate_installed_provenance(
    python: Path,
    wheel: Path,
    expected_sha256: str,
    *,
    cwd: Path,
    environment: dict,
) -> None:
    code = (
        "from importlib.metadata import distribution; "
        "value = distribution('thinkery-leanctx-sdk').read_text('direct_url.json'); "
        "print(value or '')"
    )
    result = subprocess.run(
        [str(python), "-c", code],
        check=True,
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
    )
    _validate_direct_url(result.stdout, expected_sha256, wheel.as_uri())


def run_gate(
    wheel: Path,
    engine: Path,
    *,
    repository: Path,
    expected_wheel_sha256: str,
    expected_sdk_commit: str,
    expected_engine_sha256: str,
) -> None:
    if not _SHA256.fullmatch(expected_wheel_sha256):
        raise SystemExit("--expected-wheel-sha256 must be 64 lowercase hex characters")
    if not _COMMIT.fullmatch(expected_sdk_commit):
        raise SystemExit("--expected-sdk-commit must be a 40-character commit")
    if not _SHA256.fullmatch(expected_engine_sha256):
        raise SystemExit("--expected-engine-sha256 must be 64 lowercase hex characters")
    actual_sha256 = _sha256(wheel)
    if actual_sha256 != expected_wheel_sha256:
        raise SystemExit("--expected-wheel-sha256 does not match --wheel")
    if _sha256(engine) != expected_engine_sha256:
        raise SystemExit("--expected-engine-sha256 does not match --engine")
    try:
        inspection = inspect_wheel(wheel)
    except ValueError as exc:
        raise SystemExit(f"wheel inspection failed: {exc}") from exc
    if inspection["sha256"] != expected_wheel_sha256:
        raise SystemExit("wheel inspection digest mismatch")
    _require_clean_worktree(repository)
    if _git_head(repository) != expected_sdk_commit:
        raise SystemExit("--expected-sdk-commit does not match the SDK checkout")
    _validate_wheel_source(wheel, repository, expected_sdk_commit)
    script = repository / "scripts" / "verify_installed.py"
    if not script.is_file():
        raise SystemExit(f"missing installed verifier: {script}")
    with tempfile.TemporaryDirectory(prefix="leanctx-sdk-gate-") as temporary:
        venv_root = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_root)
        python = _venv_python(venv_root)
        environment = _offline_env()
        _run(
            [
                str(python),
                "-m",
                "pip",
                "--isolated",
                "install",
                "--no-index",
                "--no-deps",
                "--force-reinstall",
                wheel.as_uri() + "#sha256=" + expected_wheel_sha256,
            ],
            cwd=repository,
            environment=environment,
        )
        _validate_installed_provenance(
            python,
            wheel,
            expected_wheel_sha256,
            cwd=repository,
            environment=environment,
        )
        _verify_package_manager_integrity(
            python,
            cwd=repository,
            environment=environment,
        )
        environment["LEANCTX_TEST_INSTALLED_PACKAGE"] = "1"
        if _sha256(wheel) != expected_wheel_sha256:
            raise SystemExit("wheel changed during installation")
        _run(
            [
                str(python),
                "-m",
                "unittest",
                "-v",
                "tests.test_sdk",
                "tests.test_workspace",
                "tests.test_parallel_context",
            ],
            cwd=repository,
            environment=environment,
        )
        _run(
            [str(python), str(script), "--engine", str(engine)],
            cwd=repository,
            environment=environment,
        )
        workspace_script = repository / "scripts" / "verify_workspace_installed.py"
        if not workspace_script.is_file():
            raise SystemExit(f"missing Workspace verifier: {workspace_script}")
        _run(
            [str(python), str(workspace_script), "--engine", str(engine)],
            cwd=repository,
            environment=environment,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, help="absolute path to the SDK wheel")
    parser.add_argument(
        "--engine", required=True, help="absolute path to the lean-ctx binary"
    )
    parser.add_argument("--expected-wheel-sha256", required=True)
    parser.add_argument("--expected-sdk-commit", required=True)
    parser.add_argument("--expected-engine-sha256", required=True)
    args = parser.parse_args()
    wheel = _required_file(args.wheel, "--wheel", ".whl")
    engine = _required_file(args.engine, "--engine")
    repository = Path(__file__).resolve().parents[1]
    run_gate(
        wheel,
        engine,
        repository=repository,
        expected_wheel_sha256=args.expected_wheel_sha256,
        expected_sdk_commit=args.expected_sdk_commit,
        expected_engine_sha256=args.expected_engine_sha256,
    )
    print("offline wheel install gate: PASS")


if __name__ == "__main__":
    main()
