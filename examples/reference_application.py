"""Provider-free SDK v1 reference application using one real Engine binary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from leanctx_sdk import ContextSession, ContextSource, SubprocessEngineClient


def run(engine: Path, project_root: Path, relative_path: str) -> dict:
    session = ContextSession(
        "reference application",
        project_root=str(project_root),
        session_id="sdk-v1-reference-session",
        task_id="sdk-v1-reference-task",
        engine=SubprocessEngineClient(engine),
    )
    view = session.prepare(ContextSource(relative_path, project_root=str(project_root)))
    if view is None:
        raise RuntimeError("reference application requires a materialized ContextView")
    host_result = {"characters": len(view.require_text())}
    receipt = session.complete(host_result, outcome="completed")
    receipt.require_verified()
    receipt_summary = {
        "integrity_status": receipt.integrity_status,
        "outcome": receipt.outcome,
        "plan_id": receipt.plan_id,
        "status": receipt.status,
    }
    recovered = session.recover(view)
    return {
        "host_result": host_result,
        "receipt": receipt_summary,
        "receipt_verified": receipt.verify(),
        "recovery_exact": recovered.text
        == Path(project_root, relative_path).read_text(encoding="utf-8"),
        "status": view.status,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.engine.resolve(strict=True),
                args.project_root.resolve(strict=True),
                args.path,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
