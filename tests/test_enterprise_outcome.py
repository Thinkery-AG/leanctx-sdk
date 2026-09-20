# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused loopback checks for the authenticated Engine outcome carrier."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Callable, Iterator, Mapping, Optional
import unittest

from leanctx_sdk.enterprise_engine import EnterpriseEngineClient
from leanctx_sdk.errors import EngineProtocolError, ValidationError
from leanctx_sdk.protocol import canonical_json, canonical_bytes, sha256_digest


TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "33333333-3333-3333-3333-333333333333"
RECEIPT_DIGEST = sha256_digest(b"original receipt")
DECISION_DIGEST = sha256_digest(b"context decision")
PREVIOUS_RECEIPT_ID = sha256_digest(b"previous receipt id")


def _receipt_document(
    *,
    task_id: str = "enterprise-task",
    acceptance: str = "accepted",
    previous_receipt_id: str | None = PREVIOUS_RECEIPT_ID,
    decision_digest: str = DECISION_DIGEST,
) -> str:
    # Structural transport fixture, not a cryptographically verified receipt.
    document = {
            "schema_version": 1,
            "chain": {"previous_receipt_id": previous_receipt_id},
            "lineage": {"task_id": task_id},
            "status": "succeeded",
            "values": [],
            "outcome": {"state": acceptance},
            "issued_at": "2026-09-20T00:00:00Z",
            "signer": {"algorithm": "ed25519", "key_id": "fixture-only",
                       "key_admission": "external_trust_store"},
            "evidence_refs": [
                {
                    "kind": "runtime",
                    "digest": decision_digest,
                    "uri": "artifact://execution/evidence/" + decision_digest[7:],
                }
            ],
        }
    document["receipt_id"] = sha256_digest(canonical_bytes(document))
    document["signature"] = "fixture-only-not-a-trusted-signature"
    return canonical_json(document)


RECEIPT_DOCUMENT = _receipt_document()
RECEIPT_ID = json.loads(RECEIPT_DOCUMENT)["receipt_id"]


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, body_factory: Callable[[bytes], bytes]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.body_factory = body_factory
        self.calls = 0
        self.bodies: list[bytes] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:
        server = self.server
        if not isinstance(server, _Server):
            raise RuntimeError("unexpected test server")
        server.calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server.bodies.append(body)
        response = server.body_factory(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *args: object) -> None:
        return None


@contextmanager
def _server(body_factory: Callable[[bytes], bytes]) -> Iterator[_Server]:
    server = _Server(body_factory)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _client(server: _Server) -> EnterpriseEngineClient:
    return EnterpriseEngineClient(
        f"http://127.0.0.1:{server.server_port}/",
        "credential-test",
        TENANT_ID,
        allow_loopback_http=True,
    )


def _response(
    *,
    schema_version: int = 1,
    outcome_schema_version: int = 1,
    tenant_id: str = TENANT_ID,
    receipt_document_json: str = RECEIPT_DOCUMENT,
    receipt_digest: str | None = None,
    original_receipt_digest: str = RECEIPT_DIGEST,
    acceptance: object = "accepted",
    extra: Optional[Mapping[str, object]] = None,
) -> bytes:
    document_digest = sha256_digest(receipt_document_json.encode("utf-8"))
    try:
        document_value = json.loads(receipt_document_json)
    except (json.JSONDecodeError, RecursionError):
        document_value = None
    document_receipt_id = (
        document_value.get("receipt_id")
        if isinstance(document_value, dict)
        else document_digest
    )
    outcome: dict[str, object] = {
        "schema_version": outcome_schema_version,
        "receipt_id": document_receipt_id,
        "receipt_digest": receipt_digest or document_digest,
        "original_receipt_digest": original_receipt_digest,
        "acceptance": acceptance,
        "already_recorded": False,
        "receipt_document_json": receipt_document_json,
    }
    if extra:
        outcome.update(extra)
    return json.dumps(
        {
            "schema_version": schema_version,
            "tenant_id": tenant_id,
            "outcome": outcome,
        },
        ensure_ascii=False,
    ).encode("utf-8")


class EnterpriseOutcomeTests(unittest.TestCase):
    def test_rejects_control_task_ids_before_network(self) -> None:
        controls = (*range(0x20), *range(0x7F, 0xA0))
        signals = [{"signal_type": "human_acceptance", "value": {"boolean": True}}]
        with _server(lambda _body: _response()) as server:
            client = _client(server)
            for codepoint in controls:
                with self.subTest(codepoint=codepoint):
                    with self.assertRaises(ValidationError):
                        client.context_outcome(
                            "task-" + chr(codepoint),
                            RECEIPT_DIGEST,
                            DECISION_DIGEST,
                            signals,
                        )
            self.assertEqual(server.calls, 0)

    def test_serialized_receipt_is_not_revalidated_as_an_opaque_id(self) -> None:
        # Structural carrier fixture only: signer verification remains external.
        document = json.loads(RECEIPT_DOCUMENT)
        document["signature"] += "\u0085"
        receipt = canonical_json(document)
        with _server(lambda _body: _response(receipt_document_json=receipt)) as server:
            result = _client(server).context_outcome(
                "enterprise-task",
                RECEIPT_DIGEST,
                DECISION_DIGEST,
                [{"signal_type": "human_acceptance", "value": {"boolean": True}}],
            )
        self.assertEqual(result["outcome"]["receipt_document_json"], receipt)

    def test_context_outcome_preserves_document_and_server_owned_fields(self) -> None:
        signals = [
            {"signal_type": "human_acceptance", "value": {"boolean": True}},
            {"signal_type": "tests_passing", "value": {"count": 3}},
        ]
        with _server(lambda _body: _response()) as server:
            result = _client(server).context_outcome(
                "enterprise-task", RECEIPT_DIGEST, DECISION_DIGEST, signals
            )
        self.assertEqual(set(result), {"schema_version", "tenant_id", "outcome"})
        self.assertEqual(result["tenant_id"], TENANT_ID)
        outcome = result["outcome"]
        self.assertIsInstance(outcome, Mapping)
        self.assertEqual(outcome["receipt_document_json"], RECEIPT_DOCUMENT)
        body = json.loads(server.bodies[0].decode("utf-8"))
        self.assertEqual(
            set(body),
            {"task_id", "receipt_digest", "context_decision_digest", "signals"},
        )
        self.assertEqual(body["task_id"], "enterprise-task")
        self.assertNotIn("tenant_id", body)
        self.assertNotIn("agent_id", body)
        self.assertNotIn("learn", body)

    def test_rejects_tenant_schema_join_and_receipt_mutations(self) -> None:
        cases = (
            ("tenant", _response(tenant_id=OTHER_TENANT_ID)),
            ("schema", _response(schema_version=2)),
            ("nested schema", _response(outcome_schema_version=2)),
            ("unknown field", _response(extra={"unexpected": True})),
            ("acceptance", _response(acceptance="unknown")),
            ("non-hashable acceptance", _response(acceptance=[])),
            ("original digest", _response(original_receipt_digest=DECISION_DIGEST)),
            ("receipt digest", _response(receipt_digest=RECEIPT_DIGEST)),
            ("incomplete document", _response(receipt_document_json='{}')),
            ("noncanonical document", _response(receipt_document_json=" " + RECEIPT_DOCUMENT)),
            ("document identity", _response(receipt_document_json=RECEIPT_DOCUMENT.replace(
                '"fixture-only"', '"changed-fixture"'))),
            ("malformed predecessor", _response(receipt_document_json=_receipt_document(
                previous_receipt_id="x"))),
            (
                "missing predecessor",
                _response(receipt_document_json=_receipt_document(previous_receipt_id=None)),
            ),
            (
                "document outcome",
                _response(receipt_document_json=_receipt_document(acceptance="rejected")),
            ),
            (
                "runtime evidence digest",
                _response(receipt_document_json=_receipt_document(decision_digest=RECEIPT_DIGEST)),
            ),
        )
        for label, body in cases:
            with self.subTest(label=label):
                with _server(lambda _request, body=body: body) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_outcome(
                            "enterprise-task", RECEIPT_DIGEST, DECISION_DIGEST, [
                                {"signal_type": "human_acceptance", "value": {"boolean": True}}
                            ]
                        )

    def test_rejects_matching_transport_hash_for_other_task_document(self) -> None:
        body = _response(receipt_document_json=_receipt_document(task_id="other-task"))
        with _server(lambda _request: body) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_outcome(
                    "enterprise-task",
                    RECEIPT_DIGEST,
                    DECISION_DIGEST,
                    [{"signal_type": "human_acceptance", "value": {"boolean": True}}],
                )

    def test_rejects_invalid_restricted_signals_before_network(self) -> None:
        with _server(lambda _body: _response()) as server:
            client = _client(server)
            invalid_signals: tuple[Any, ...] = (
                [{"signal_type": "agent_completion", "value": {"boolean": True}}],
                [{"signal_type": "human_acceptance", "value": {"boolean": 1}}],
                [{"signal_type": "human_acceptance", "value": {"count": 2**32}}],
                [{"signal_type": "human_acceptance", "value": "passed"}],
                [{"signal_type": "human_acceptance", "value": {"boolean": True, "extra": 1}}],
            )
            for signals in invalid_signals:
                with self.subTest(signals=signals):
                    with self.assertRaises(ValidationError):
                        client.context_outcome(
                            "enterprise-task", RECEIPT_DIGEST, DECISION_DIGEST, signals
                        )
            self.assertEqual(server.calls, 0)

    def test_rejects_oversized_receipt_document(self) -> None:
        oversized = "x" * (1024 * 1024 + 1)
        with _server(lambda _body: _response(receipt_document_json=oversized)) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_outcome(
                    "enterprise-task", RECEIPT_DIGEST, DECISION_DIGEST,
                    [{"signal_type": "human_acceptance", "value": {"boolean": True}}],
                )

    def test_rejects_deeply_nested_outcome_and_document_as_typed_errors(self) -> None:
        deep = "[" * 1100 + "0" + "]" * 1100
        response = (
            '{"schema_version":1,"tenant_id":"'
            + TENANT_ID
            + '\",\"outcome\":'
            + deep
            + "}"
        ).encode("utf-8")
        with _server(lambda _body: response) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_outcome(
                    "enterprise-task", RECEIPT_DIGEST, DECISION_DIGEST,
                    [{"signal_type": "human_acceptance", "value": {"boolean": True}}],
                )

        deep_document = (
            '{"receipt_id":"'
            + RECEIPT_ID
            + '\",\"chain\":{\"previous_receipt_id\":\"'
            + PREVIOUS_RECEIPT_ID
            + '\"},\"lineage\":{\"task_id\":\"enterprise-task\"},'
            + '\"outcome\":{\"state\":\"accepted\"},\"evidence_refs\":'
            + deep
            + "}"
        )
        body = _response(receipt_document_json=deep_document)
        with _server(lambda _body: body) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_outcome(
                    "enterprise-task", RECEIPT_DIGEST, DECISION_DIGEST,
                    [{"signal_type": "human_acceptance", "value": {"boolean": True}}],
                )


if __name__ == "__main__":
    unittest.main()
