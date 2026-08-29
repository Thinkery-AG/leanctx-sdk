#!/usr/bin/env python3
"""Provider-free two-process Preview Workspace acceptance proof."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from leanctx_sdk import (
    ContextSource,
)
from leanctx_sdk.preview import (
    ContextWorkspace,
    ProjectContextEntry,
    SourceAnchor,
    SourceFreshness,
    SourceScope,
    SourceTrust,
)


_SOURCE_ID = "clean-machine-source"
_FACT = "deliberate durable workspace fact"


def _anchor(project_root: Path) -> SourceAnchor:
    source = ContextSource("source.txt", project_root=str(project_root))
    return SourceAnchor(
        _SOURCE_ID,
        "filesystem",
        "file://source.txt",
        freshness=SourceFreshness("2026-08-26T00:00:00Z", "current"),
        trust=SourceTrust("local"),
        scope=SourceScope("project", "clean-machine-gate"),
        engine_binding=source.to_dict(),
    )


def _phase_a(state_root: Path, project_root: Path) -> dict:
    workspace = ContextWorkspace.create(state_root, "Preview clean-machine gate")
    workspace.attach_source(_anchor(project_root))
    source = ContextSource("source.txt", project_root=str(project_root))
    attachment = workspace.start_session(
        "Preview process A",
        source_id=_SOURCE_ID,
        source=source,
    )
    session = attachment.session
    view = session.view
    if view is None or not view.verify():
        raise RuntimeError("process A received an invalid Engine view")
    recovered = session.recover(view)
    if recovered.text != project_root.joinpath("source.txt").read_text(encoding="utf-8"):
        raise RuntimeError("process A recovery changed source text")
    engine_receipt = session.complete()
    if not engine_receipt.verify():
        raise RuntimeError("process A received an invalid Engine receipt")
    workspace.commit_context(
        [ProjectContextEntry(str(uuid.uuid4()), "facts", _FACT)],
        session=session,
    )
    entry = workspace.project_context().entries[0]
    return {
        "workspace_id": workspace.workspace_id,
        "session_a": session.session_id,
        "receipt_refs": list(entry.receipt_refs),
        "recovery_refs": list(entry.recovery_refs),
    }


def _phase_b(state_root: Path, project_root: Path, workspace_id: str, session_a: str) -> dict:
    workspace = ContextWorkspace.open(state_root, workspace_id)
    context = workspace.project_context()
    if len(context.entries) != 1 or context.entries[0].value != _FACT:
        raise RuntimeError("process B did not recover deliberate Workspace state")
    entry = context.entries[0]
    if not entry.receipt_refs or not entry.recovery_refs:
        raise RuntimeError("process A lineage was not preserved")
    source = ContextSource("source.txt", project_root=str(project_root))
    attachment = workspace.start_session(
        "Preview process B",
        source_id=_SOURCE_ID,
        source=source,
    )
    session = attachment.session
    if session.session_id == session_a:
        raise RuntimeError("process B reused process A session identity")
    view = session.view
    if view is None or not view.verify():
        raise RuntimeError("process B received an invalid Engine view")
    recovered = session.recover(view)
    expected = project_root.joinpath("source.txt").read_text(encoding="utf-8")
    if recovered.text != expected:
        raise RuntimeError("process B recovery changed source text")
    receipt = session.complete()
    if not receipt.verify():
        raise RuntimeError("process B received an invalid Engine receipt")
    status = workspace.status()
    if status.session_count != 2:
        raise RuntimeError("Workspace did not preserve both session attachments")
    return {
        "context_reused_without_transcript": True,
        "engine_interface": view.engine_interface_version,
        "lineage_preserved": True,
        "provider_credentials": False,
        "recovery_exact": True,
        "session_count": status.session_count,
        "workspace_health": status.health,
    }


def _clean_environment(engine: Path, shim_directory: Path) -> dict:
    environment = dict(os.environ)
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
    shim = shim_directory / "lean-ctx"
    shim.symlink_to(engine)
    environment["PATH"] = str(shim_directory) + os.pathsep + environment.get("PATH", "")
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run_phase(script: Path, phase: str, state: Path, project: Path, environment: dict, **values) -> dict:
    command = [
        sys.executable,
        str(script),
        "--phase",
        phase,
        "--state-root",
        str(state),
        "--project-root",
        str(project),
    ]
    for key, value in values.items():
        command.extend(("--" + key.replace("_", "-"), value))
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    return json.loads(completed.stdout)


def verify(engine: Path) -> dict:
    if not engine.is_absolute() or not engine.is_file() or not os.access(engine, os.X_OK):
        raise SystemExit("--engine must name an absolute executable file")
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="leanctx-p5-clean-") as temporary:
        root = Path(temporary)
        state = root / "state"
        project = root / "project"
        shim = root / "bin"
        state.mkdir(mode=0o700)
        project.mkdir(mode=0o700)
        shim.mkdir(mode=0o700)
        project.joinpath("source.txt").write_text(
            "provider-free Preview source\n", encoding="utf-8"
        )
        environment = _clean_environment(engine, shim)
        first = _run_phase(script, "a", state, project, environment)
        second = _run_phase(
            script,
            "b",
            state,
            project,
            environment,
            workspace_id=first["workspace_id"],
            session_a=first["session_a"],
        )
        return {"processes": 2, **second}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine")
    parser.add_argument("--phase", choices=("a", "b"))
    parser.add_argument("--state-root")
    parser.add_argument("--project-root")
    parser.add_argument("--workspace-id")
    parser.add_argument("--session-a")
    args = parser.parse_args()
    if args.phase:
        if not args.state_root or not args.project_root:
            raise SystemExit("phase execution requires state and project roots")
        state = Path(args.state_root)
        project = Path(args.project_root)
        if args.phase == "a":
            result = _phase_a(state, project)
        else:
            if not args.workspace_id or not args.session_a:
                raise SystemExit("phase B requires workspace and prior session identities")
            result = _phase_b(state, project, args.workspace_id, args.session_a)
    else:
        if not args.engine:
            raise SystemExit("--engine is required")
        result = verify(Path(args.engine).resolve(strict=True))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
