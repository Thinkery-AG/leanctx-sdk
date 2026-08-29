"""Fan one real LeanCTX ContextView out to three provider-neutral specialists."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from leanctx_sdk import ContextSession, ContextSource, SubprocessEngineClient


Specialist = Callable[[str], Dict[str, object]]


def _architect(text: str) -> Dict[str, object]:
    headings = [line.strip() for line in text.splitlines() if line.lstrip().startswith("#")]
    return {"signal": "structure", "headings": headings[:5]}


def _risk_scout(text: str) -> Dict[str, object]:
    lowered = text.lower()
    terms = ("security", "verify", "recover", "fail")
    return {
        "signal": "risk",
        "term_hits": {term: lowered.count(term) for term in terms},
    }


def _operator(text: str) -> Dict[str, object]:
    commands = [
        line.strip()
        for line in text.splitlines()
        if "pip install" in line.lower() or "lean-ctx" in line.lower()
        if not line.lstrip().startswith("... [lean-ctx:")
    ]
    return {"signal": "operations", "commands": commands[:5]}


SPECIALISTS: Tuple[Tuple[str, Specialist], ...] = (
    ("architect", _architect),
    ("risk-scout", _risk_scout),
    ("operator", _operator),
)


def _run_specialist(
    specialist: Tuple[str, Specialist], text: str, fingerprint: str
) -> Tuple[str, Dict[str, object]]:
    name, analyze = specialist
    result = analyze(text)
    result["context_fingerprint"] = fingerprint
    return name, result


def run(engine: Path, project_root: Path, relative_path: str) -> Dict[str, object]:
    root = project_root.resolve(strict=True)
    source_path = root.joinpath(relative_path).resolve(strict=True)
    try:
        source_path.relative_to(root)
    except ValueError as error:
        raise ValueError("source path escapes project root") from error

    original = source_path.read_text(encoding="utf-8")
    session = ContextSession(
        "fan one shaped context view out to three specialists",
        project_root=str(root),
        engine=SubprocessEngineClient(engine),
    )
    view = session.prepare(ContextSource(relative_path, project_root=str(root)))
    if view is None:
        raise RuntimeError("Context Reactor requires a materialized ContextView")

    relayed_views = [view]
    for _ in range(len(SPECIALISTS) - 1):
        reused = session.prepare()
        if reused is not view:
            raise RuntimeError("ContextSession did not reuse its materialized ContextView")
        relayed_views.append(reused)

    shaped = view.require_text()
    fingerprint = hashlib.sha256(shaped.encode("utf-8")).hexdigest()[:16]
    with ThreadPoolExecutor(max_workers=len(SPECIALISTS)) as executor:
        analyses: List[Tuple[str, Dict[str, object]]] = list(
            executor.map(
                lambda specialist: _run_specialist(specialist, shaped, fingerprint),
                SPECIALISTS,
            )
        )

    host_result = {"specialists": dict(analyses)}
    receipt = session.complete(host_result, outcome="completed")
    receipt.require_verified()
    recovered = session.recover(view)
    if recovered.text != original:
        raise RuntimeError("exact source recovery failed")

    return {
        "status": "PASS",
        "context": {
            "prepared_once": True,
            "sdk_prepare_calls": len(relayed_views),
            "materialized_views": 1,
            "same_view_identity": all(candidate is view for candidate in relayed_views),
            "parallel_specialists": len(SPECIALISTS),
            "reused_prepare_calls": len(relayed_views) - 1,
            "source_characters": len(original),
            "shaped_characters": len(shaped),
            "representation_delta_characters": len(original) - len(shaped),
            "representation_reduction_percent": round(
                (len(original) - len(shaped)) * 100 / len(original), 1
            )
            if original
            else 0.0,
            "fingerprint": fingerprint,
            "engine_version": view.engine_version,
        },
        "specialists": host_result["specialists"],
        "receipt": {
            "integrity_status": receipt.integrity_status,
            "outcome": receipt.outcome,
            "verified": receipt.verify(),
        },
        "recovery_exact": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()
    result = run(
        args.engine.resolve(strict=True),
        args.project_root.resolve(strict=True),
        args.path,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
