# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused loopback checks for the bounded Enterprise Engine adapter."""

from __future__ import annotations

import json
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence, Tuple, cast
import unittest
from unittest import mock
import uuid

from leanctx_sdk.enterprise_engine import EnterpriseEngineClient
from leanctx_sdk.errors import (
    ConfigurationError,
    EngineProtocolError,
    EngineTimeout,
    EngineUnavailable,
    PolicyAdmissionError,
    ValidationError,
)
from leanctx_sdk.planning import EnginePlanningRequest
from leanctx_sdk.protocol import canonical_bytes, sha256_digest


TENANT_ID = "11111111-1111-1111-1111-111111111111"
SOURCE_ID = "22222222-2222-2222-2222-222222222222"
OTHER_SOURCE_ID = "33333333-3333-3333-3333-333333333333"
REQUEST = EnginePlanningRequest("enterprise-task", "invoice ledger", 64)


class _Reply:
    def __init__(
        self,
        status: int,
        headers: Optional[Mapping[str, str]] = None,
        body: bytes = b"",
        *,
        chunks: Optional[Sequence[Tuple[bytes, float]]] = None,
        header_chunks: Optional[Sequence[Tuple[bytes, float]]] = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})
        self.body = body
        self.chunks = chunks
        self.header_chunks = header_chunks


ReplyFactory = Callable[["_Handler", bytes], _Reply]


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, factory: ReplyFactory) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.factory = factory
        self.calls = 0
        self.bodies: list[bytes] = []
        self.auth_headers: list[Optional[str]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:
        server = self.server
        assert isinstance(server, _Server)
        server.calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server.bodies.append(body)
        server.auth_headers.append(self.headers.get("Authorization"))
        reply = server.factory(self, body)
        if reply.header_chunks is not None:
            try:
                for chunk, delay in reply.header_chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(delay)
            except OSError:
                pass
            self.close_connection = True
            return
        self.send_response(reply.status)
        headers = dict(reply.headers)
        if reply.chunks is not None:
            headers.setdefault(
                "Content-Length", str(sum(len(chunk) for chunk, _ in reply.chunks))
            )
        else:
            headers.setdefault("Content-Length", str(len(reply.body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            if reply.chunks is not None:
                for chunk, delay in reply.chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(delay)
            elif reply.body:
                self.wfile.write(reply.body)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *args: object) -> None:
        return None


@contextmanager
def _server(factory: ReplyFactory) -> Iterator[_Server]:
    server = _Server(factory)
    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _descriptor(source_id: str, permission: str = "permitted") -> Mapping[str, object]:
    content_digest = sha256_digest(b"enterprise source")
    return {
        "object_ref": source_id,
        "source_id": source_id,
        "source_type": "issue_tracker",
        "content_digest": content_digest,
        "revision": None,
        "owner": "owner-1",
        "observed_at": None,
        "valid_until": None,
        "classification": "Internal",
        "permission": permission,
    }


def _plan_response(
    *,
    tenant_id: str = TENANT_ID,
    source_id: str = SOURCE_ID,
    permission: str = "permitted",
) -> bytes:
    descriptor = _descriptor(source_id, permission)
    selection = {
        "source_ref": source_id,
        "provider": source_id,
        "disposition": "selected",
        "token_count": 1,
        "sha256_digest": descriptor["content_digest"],
        "reason_codes": ["relevant"],
    }
    unsigned_plan = {
        "schema_version": 1,
        "context_plan_id": "context-plan-1",
        "task_id": REQUEST.task_id,
        "budget_tokens": REQUEST.budget_tokens,
        "selections": [selection],
    }
    plan = dict(unsigned_plan)
    plan["projection_digest"] = sha256_digest(canonical_bytes(unsigned_plan))
    result = {
        "schema_version": 1,
        "transport_version": 1,
        "engine_interface_version": "1.0.0",
        "plan": plan,
    }
    source_plan = {
        "result": result,
        "source_bindings": [descriptor],
    }
    source_plan["binding_digest"] = sha256_digest(
        canonical_bytes([result, [descriptor]])
    )
    return json.dumps(
        {
            "schema_version": 1,
            "tenant_id": tenant_id,
            "governance_revision": 7,
            "plan": source_plan,
        }
    ).encode("utf-8")


def _client(server: _Server, timeout: float = 30.0) -> EnterpriseEngineClient:
    return EnterpriseEngineClient(
        f"http://127.0.0.1:{server.server_port}/",
        "credential-test",
        TENANT_ID,
        allow_loopback_http=True,
        timeout=timeout,
    )


class EnterpriseEngineTests(unittest.TestCase):
    def test_closed_socket_cannot_reconnect_during_send(self) -> None:
        class InterruptedConnection(http.client.HTTPConnection):
            connects = 0

            def connect(self) -> None:
                self.connects += 1
                super().connect()

            def endheaders(self, *args: Any, **kwargs: Any) -> None:
                # Deterministically model the timer closing the socket at send.
                self.close()
                super().endheaders(*args, **kwargs)

        with _server(
            lambda _handler, _body: _Reply(200, body=_plan_response())
        ) as server:
            connection = InterruptedConnection(
                "127.0.0.1", server.server_port, timeout=1
            )
            client = _client(server)
            with mock.patch.object(client, "_new_connection", return_value=connection):
                with self.assertRaises(EngineUnavailable):
                    client.context_plan(REQUEST, [SOURCE_ID])
            self.assertEqual(connection.connects, 1)
            self.assertEqual(server.calls, 0)

    def test_trickled_headers_cannot_extend_deadline(self) -> None:
        chunks = [(b"HTTP/1.1 200 OK\r\nX-Slow: ", 0.05)] + [(b"x", 0.05)] * 32
        with _server(
            lambda _handler, _body: _Reply(200, header_chunks=chunks)
        ) as server:
            started = time.monotonic()
            with self.assertRaises(EngineTimeout):
                _client(server, timeout=0.2).context_plan(REQUEST, [SOURCE_ID])
            self.assertLess(time.monotonic() - started, 1.5)

    def test_valid_request_uses_canonical_remote_wrapper_and_binding(self) -> None:
        with _server(
            lambda _handler, _body: _Reply(
                200, {"Content-Type": "application/json"}, _plan_response()
            )
        ) as server:
            result = _client(server).context_plan(REQUEST, [SOURCE_ID])

        self.assertEqual(result["tenant_id"], TENANT_ID)
        self.assertEqual(result["governance_revision"], 7)
        result_value = cast(Any, result)
        self.assertEqual(
            result_value["plan"]["result"]["plan"]["task_id"], REQUEST.task_id
        )
        self.assertEqual(server.calls, 1)
        self.assertEqual(server.auth_headers, ["Bearer credential-test"])
        body = json.loads(server.bodies[0].decode("utf-8"))
        self.assertEqual(body["planning"], dict(REQUEST.to_dict()))
        self.assertEqual(body["source_ids"], [SOURCE_ID])

    def test_redirect_is_rejected_without_following_or_resending_credentials(
        self,
    ) -> None:
        def reply(handler: _Handler, _body: bytes) -> _Reply:
            if handler.path == "/v1/engine/context-plan":
                return _Reply(307, {"Location": "/redirect-target"})
            return _Reply(200, {"Content-Type": "application/json"}, _plan_response())

        with _server(reply) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_plan(REQUEST, [SOURCE_ID])
            self.assertEqual(server.calls, 1)

    def test_wrong_tenant_and_unrequested_source_fail_closed(self) -> None:
        with _server(
            lambda _handler, _body: _Reply(
                200,
                {"Content-Type": "application/json"},
                _plan_response(tenant_id=OTHER_SOURCE_ID),
            )
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_plan(REQUEST, [SOURCE_ID])

        with _server(
            lambda _handler, _body: _Reply(
                200,
                {"Content-Type": "application/json"},
                _plan_response(source_id=OTHER_SOURCE_ID),
            )
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_plan(REQUEST, [SOURCE_ID])

    def test_selected_non_permitted_source_fails_closed(self) -> None:
        with _server(
            lambda _handler, _body: _Reply(
                200,
                {"Content-Type": "application/json"},
                _plan_response(permission="denied"),
            )
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_plan(REQUEST, [SOURCE_ID])

    def test_remote_policy_denial_uses_canonical_error(self) -> None:
        with _server(lambda _handler, _body: _Reply(403)) as server:
            with self.assertRaises(PolicyAdmissionError):
                _client(server).context_plan(REQUEST, [SOURCE_ID])

    def test_oversized_and_trickled_responses_are_bounded(self) -> None:
        oversized = b"{" + b"x" * (1024 * 1024) + b"}"
        with _server(
            lambda _handler, _body: _Reply(
                200, {"Content-Type": "application/json"}, oversized
            )
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_plan(REQUEST, [SOURCE_ID])

        def trickle(_handler: _Handler, _body: bytes) -> _Reply:
            return _Reply(
                200,
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(1024 * 1024),
                },
                chunks=[(b"{", 0.05)] * 32,
            )

        with _server(trickle) as server:
            started = time.monotonic()
            with self.assertRaises(EngineTimeout):
                _client(server, timeout=0.2).context_plan(REQUEST, [SOURCE_ID])
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(server.calls, 1)

    def test_constructor_and_request_bounds_fail_before_network(self) -> None:
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient("http://localhost:443", "credential-test", TENANT_ID)
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient(
                "http://localhost:443",
                "credential-test",
                TENANT_ID,
                allow_loopback_http=True,
            )
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient(
                "https://user:pass@example.test", "credential-test", TENANT_ID
            )
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient(
                "https://example.test/path", "credential-test", TENANT_ID
            )
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient("https://example.test", "bad credential", TENANT_ID)
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient(
                "https://example.test", "credential-test", TENANT_ID, timeout=10**10000
            )
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient(
                "https://example.test\ud800", "credential-test", TENANT_ID
            )
        with self.assertRaises(ConfigurationError):
            EnterpriseEngineClient(
                "https://example.test", "credential-test", str(uuid.UUID(int=0))
            )

        with _server(lambda _handler, _body: _Reply(500)) as server:
            client = _client(server)
            with self.assertRaises(ValidationError):
                client.context_plan(REQUEST, [SOURCE_ID] * 2)
            with self.assertRaises(ValidationError):
                client.context_plan(
                    REQUEST, [str(uuid.UUID(int=index + 1)) for index in range(65)]
                )
            with self.assertRaises(ValidationError):
                client.context_plan(REQUEST, "not-a-sequence")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
