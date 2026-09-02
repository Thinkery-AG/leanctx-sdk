"""Verify an installed SDK against one explicit local Engine binary."""

from __future__ import annotations

import argparse
import json
import tempfile
from importlib.metadata import version
from pathlib import Path

from leanctx_sdk import ContextSession, ContextSource, SubprocessEngineClient
from leanctx_sdk.integrations.native_embed import complete, prepare


def verify_installed(engine: Path) -> dict:
    if not engine.is_file():
        raise SystemExit("--engine must name an executable file")

    expected = "installed SDK verification\n"
    with tempfile.TemporaryDirectory(prefix="leanctx-sdk-verify-") as root:
        Path(root, "source.txt").write_text(expected, encoding="utf-8")
        session = ContextSession(
            "verify installed SDK",
            project_root=root,
            session_id="installed-sdk-session",
            task_id="installed-sdk-task",
            engine=SubprocessEngineClient(engine),
        )
        view = prepare(session, ContextSource("source.txt", project_root=root))
        if view is None:
            raise RuntimeError("installed SDK returned no Engine view")
        if not view.verify():
            raise RuntimeError("installed SDK returned an invalid Engine view")
        recovered = session.recover(view)
        if recovered.text != expected:
            raise RuntimeError("installed SDK recovery changed source text")
        receipt = complete(session, outcome="completed")
        if not receipt.verify():
            raise RuntimeError("installed SDK returned an invalid receipt")

    return {
        "distribution": version("thinkery-leanctx-sdk"),
        "engine_interface": view.engine_interface_version,
        "integrity": receipt.integrity_status,
        "recovery_exact": True,
        "status": view.status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--engine", required=True, help="absolute path to the lean-ctx binary"
    )
    args = parser.parse_args()
    engine = Path(args.engine)
    if not engine.is_absolute():
        raise SystemExit("--engine must be an absolute path")
    try:
        engine = engine.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SystemExit("--engine must name an executable file") from exc
    print(json.dumps(verify_installed(engine), sort_keys=True))


if __name__ == "__main__":
    main()
