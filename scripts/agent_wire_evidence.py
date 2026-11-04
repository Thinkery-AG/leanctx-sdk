# PRIVATE_PENDING_REVIEW — no licence, rights or release statement is made for this file.
"""Wire evidence for the pair caller (`verify_agent_context_e2e.py --wire-evidence-out`).

Used by that caller only, and only when it is asked to. It records the ORIGINAL bytes of
one Agent Tools session at the SDK's own byte seam — every line the SDK writes to the
Engine and every line it reads back — and binds them, with what the two sides said in
their Hello and how the session ended, in one closed document.

How the bytes are reached: for the duration of one `verify` call the name `subprocess` in
`leanctx_sdk.agent` is a shim that hands every attribute to the real module, except that
the ONE `Popen` of an `engine tool-session` gets its stdin and stdout wrapped. The wrapper
passes every call through unchanged and only looks at what `write` was given and what
`readline` returned. Nothing is re-encoded, and nothing is taken from a decoded value where
the bytes are meant. The shim is removed in `finally`. Without `--wire-evidence-out` this
module is not imported.

What the document is NOT. It is collected by the caller about itself: the file digests,
the version labels and the path of the Engine binary are the caller's own inputs and
observations. It is no enrolled, released or authorized origin, no signature, no statement
about who built the binary, and no cross-version (N / N-1) result. A missing or
inconsistent observation is a refusal: no document is written for it.

Bounds: at most MAX_FRAMES lines; each line is hashed, and kept for parsing only up to
MAX_KEPT_BYTES (a larger one is bound by digest and length, and its `op`, `id` and `ok` are
reported as unknown); a file is hashed in chunks up to MAX_FILE_BYTES.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

KIND = "leanctx.agent-tools-pair-wire-evidence"
SCHEMA_VERSION = 1
ORIGIN = (
    "self-collected by the pair caller; not an enrolled, released or authorized origin"
)
MAX_FRAMES = 64
MAX_KEPT_BYTES = 64 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
CONTAINMENT_ACK = b'{"leanctx_session_containment":1}\n'


class WireEvidenceError(RuntimeError):
    """The session was not observed completely or consistently; nothing is written."""


class WireRecorder:
    """The lines of one session, in the order this process handled them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: list[dict[str, object]] = []
        self._kept: dict[int, bytes] = {}
        self._transcript = hashlib.sha256()
        self._overflow = False
        self._sessions = 0
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._project_root: Optional[str] = None

    def attach(self, process: subprocess.Popen[bytes], words: list[str]) -> None:
        with self._lock:
            self._sessions += 1
            self._process = process
            if "--project-root" in words[:-1]:
                self._project_root = words[words.index("--project-root") + 1]

    def record(self, direction: str, data: bytes) -> None:
        with self._lock:
            if len(self._frames) >= MAX_FRAMES:
                self._overflow = True
                return
            index = len(self._frames)
            self._frames.append(
                {
                    "index": index,
                    "direction": direction,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            self._transcript.update(
                direction.encode("ascii") + b"\0" + len(data).to_bytes(8, "big") + data
            )
            if len(data) <= MAX_KEPT_BYTES:
                self._kept[index] = data

    def evidence(
        self,
        *,
        engine: Path,
        expected_engine_version: str,
        caller_script: Path,
        sdk_agent_source: Path,
    ) -> dict[str, object]:
        """The closed document, or `WireEvidenceError`."""
        with self._lock:
            frames = [dict(frame) for frame in self._frames]
            kept = dict(self._kept)
            overflow, sessions = self._overflow, self._sessions
            process, project_root = self._process, self._project_root
            transcript = self._transcript.hexdigest()
        if overflow:
            raise WireEvidenceError("more lines than the evidence admits")
        if sessions != 1 or process is None:
            raise WireEvidenceError("not exactly one Engine session was observed")

        sent = [frame for frame in frames if frame["direction"] == "sent"]
        received = [frame for frame in frames if frame["direction"] == "received"]
        acknowledged = bool(received) and kept.get(_index(received[0])) == CONTAINMENT_ACK
        answers = received[1:] if acknowledged else received
        if len(sent) < 2 or len(sent) != len(answers):
            raise WireEvidenceError("requests and answers do not pair")

        exchanges: list[dict[str, object]] = []
        for request, answer in zip(sent, answers):
            asked = _parsed(kept.get(_index(request)))
            told = _parsed(kept.get(_index(answer)))
            if asked is not None and told is not None and asked.get("id") != told.get("id"):
                raise WireEvidenceError("an answer does not carry its request's id")
            exchanges.append(
                {
                    "request": _index(request),
                    "answer": _index(answer),
                    "op": asked.get("op") if asked is not None else None,
                    "ok": told.get("ok") if told is not None else None,
                }
            )

        hello_sent = _parsed(kept.get(_index(sent[0])))
        hello_told = _parsed(kept.get(_index(answers[0])))
        result = hello_told.get("result") if hello_told is not None else None
        if (
            hello_sent is None
            or hello_sent.get("op") != "hello"
            or hello_told is None
            or hello_told.get("ok") is not True
            or not isinstance(result, dict)
        ):
            raise WireEvidenceError("the first exchange is not an accepted Hello")
        if result.get("engine_version") != expected_engine_version:
            raise WireEvidenceError("the Engine reported another version than expected")
        if exchanges[-1]["op"] != "close":
            raise WireEvidenceError("the last request is not the close")

        return {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "classification": "PRIVATE_PENDING_REVIEW",
            "origin": ORIGIN,
            "authority": False,
            "caller": {
                "script_sha256": file_sha256(caller_script),
                "evidence_module_sha256": file_sha256(Path(__file__)),
                "sdk_agent_source_sha256": file_sha256(sdk_agent_source),
                "engine_binary_sha256": file_sha256(engine),
                "engine_binary_bytes": os.stat(engine).st_size,
                "expected_engine_version": expected_engine_version,
            },
            "hello": {
                # As the SDK sent it and as the Engine answered it: read from the two
                # recorded lines, not from a constant of either side.
                "sent": {
                    key: hello_sent.get(key)
                    for key in (
                        "schema_version",
                        "transport_version",
                        "agent_tools_interface_version",
                        "sdk_version",
                    )
                },
                "answered": {
                    key: result.get(key)
                    for key in (
                        "schema_version",
                        "transport_version",
                        "agent_tools_interface_version",
                        "engine_version",
                        "allow_write",
                        "allow_exec",
                        "capabilities",
                    )
                },
            },
            "frames": frames,
            "transcript_sha256": transcript,
            "containment_acknowledged": acknowledged,
            "exchanges": exchanges,
            "close": {
                "requested": True,
                "answered_ok": exchanges[-1]["ok"],
                # What the SDK's own cleanup left on the process object; `None` means
                # this caller did not see an exit status, not that the Engine lives.
                "engine_returncode": process.returncode,
            },
            "cleanup": {
                "workspace_removed": (
                    None if project_root is None else not os.path.lexists(project_root)
                ),
            },
        }


def _index(frame: Mapping[str, object]) -> int:
    value = frame["index"]
    assert isinstance(value, int)
    return value


def _parsed(line: Optional[bytes]) -> Optional[dict[str, Any]]:
    """A kept line as one JSON object; `None` for a line that was not kept."""
    if line is None:
        return None

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise WireEvidenceError("a recorded line repeats a key")
        return value

    try:
        value = json.loads(line.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, ValueError) as error:
        raise WireEvidenceError("a recorded line is not JSON") from error
    if not isinstance(value, dict):
        raise WireEvidenceError("a recorded line is not a JSON object")
    return value


def file_sha256(path: Path) -> str:
    digest, seen = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            seen += len(chunk)
            if seen > MAX_FILE_BYTES:
                raise WireEvidenceError("a file is larger than the evidence hashes")
            digest.update(chunk)


class _Recorded:
    """A pipe end that behaves as the one it wraps and tells the recorder what passed."""

    def __init__(self, inner: Any, recorder: WireRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def write(self, data: Any) -> Any:
        written = self._inner.write(data)
        # After the write was accepted: the line as the SDK handed it to the pipe.
        self._recorder.record("sent", bytes(data))
        return written

    def readline(self, *limit: int) -> Any:
        line = self._inner.readline(*limit)
        if line:
            self._recorder.record("received", bytes(line))
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Subprocess:
    """`subprocess` for `leanctx_sdk.agent`, with the one Engine session observed."""

    def __init__(self, recorder: WireRecorder) -> None:
        self._recorder = recorder

    def Popen(self, args: Any, **options: Any) -> Any:  # noqa: N802 — the module's name
        process = subprocess.Popen(args, **options)
        words = [str(word) for word in args] if isinstance(args, (list, tuple)) else []
        if (
            words[1:3] == ["engine", "tool-session"]
            and options.get("stdin") == subprocess.PIPE
            and options.get("stdout") == subprocess.PIPE
        ):
            self._recorder.attach(process, words)
            process.stdin = _Recorded(process.stdin, self._recorder)  # type: ignore[assignment]
            process.stdout = _Recorded(process.stdout, self._recorder)  # type: ignore[assignment]
        return process

    def __getattr__(self, name: str) -> Any:
        return getattr(subprocess, name)


@contextlib.contextmanager
def recording(recorder: WireRecorder) -> Iterator[None]:
    import leanctx_sdk.agent as agent

    original = agent.subprocess
    agent.subprocess = _Subprocess(recorder)  # type: ignore[assignment]
    try:
        yield
    finally:
        agent.subprocess = original


def verify_recorded(
    verify: Callable[[Path, str], dict[str, object]],
    engine: Path,
    expected_engine_version: str,
    out: Path,
    caller_script: Path,
) -> dict[str, object]:
    """Run the caller's own `verify` under observation and write the evidence to the NEW
    file `out`. A failed pair, or an incomplete observation, writes nothing."""
    import leanctx_sdk.agent as agent

    if os.path.lexists(out):
        raise WireEvidenceError("the wire evidence path exists already")
    recorder = WireRecorder()
    with recording(recorder):
        report = verify(engine, expected_engine_version)
    document = recorder.evidence(
        engine=engine,
        expected_engine_version=expected_engine_version,
        caller_script=caller_script,
        sdk_agent_source=Path(agent.__file__),
    )
    encoded = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(out, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
    return {**report, "wire_evidence_sha256": hashlib.sha256(encoded).hexdigest()}
