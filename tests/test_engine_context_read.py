# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused loopback checks for the guarded Enterprise Engine context read."""

from __future__ import annotations

import json
import unittest
from typing import Any, Mapping, Optional

from test_enterprise_engine import _Reply, _server  # type: ignore[import-not-found]

from leanctx_sdk.enterprise_engine import (
    EngineContextClient,
    EngineContextReadResult,
)
from leanctx_sdk.errors import EngineProtocolError, ValidationError
from leanctx_sdk.protocol import MAX_PATH_BYTES, MAX_RESPONSE_BYTES, sha256_digest


def _client(server: Any, timeout: float = 30.0) -> EngineContextClient:
    return EngineContextClient(
        f"http://127.0.0.1:{server.server_port}/",
        "credential-test",
        allow_loopback_http=True,
        timeout=timeout,
    )


def _reply_factory(
    status: int,
    body: bytes = b"",
    headers: Optional[Mapping[str, str]] = None,
) -> Any:
    def factory(_handler: Any, _body: bytes) -> Any:
        return _Reply(status, headers, body)

    return factory


def _wire_response(
    *,
    receipt_overrides: Optional[Mapping[str, object]] = None,
    result_updates: Optional[Mapping[str, object]] = None,
) -> bytes:
    digest = sha256_digest(b"signed receipt")
    receipt: dict[str, object] = {
        "schema_version": 1,
        "receipt_id": digest,
        "receipt_ref": f"id:{digest}",
        "receipt_digest": digest,
        "outcome": "unknown",
        "delivery": "native_engine_view",
    }
    if receipt_overrides:
        receipt.update(receipt_overrides)
    result: dict[str, object] = {
        "content": [
            {
                "type": "text",
                "text": "guarded context text\n",
                "unknown_content_field": {"preserve": True},
            }
        ],
        "isError": False,
        "_meta": {
            "canonical_receipt": receipt,
            "unknown_meta_field": "preserve",
        },
        "unknown_result_field": [1, 2, 3],
    }
    if result_updates:
        result.update(result_updates)
    return json.dumps({"result": result}).encode("utf-8")


class EngineContextReadTests(unittest.TestCase):
    def test_valid_read_uses_guarded_shape_and_preserves_wire_response(self) -> None:
        body = _wire_response()
        with _server(
            _reply_factory(200, body, {"Content-Type": "application/json"})
        ) as server:
            result = _client(server).context_read("src/context.md")

        self.assertIsInstance(result, EngineContextReadResult)
        self.assertEqual(result.text, "guarded context text\n")
        self.assertEqual(result.canonical_receipt["outcome"], "unknown")
        digest = result.canonical_receipt["receipt_digest"]
        self.assertEqual(result.canonical_receipt["receipt_ref"], f"id:{digest}")
        self.assertEqual(result.raw_response, json.loads(body.decode("utf-8")))
        self.assertEqual(server.calls, 1)
        auth_header = server.auth_headers[0]
        assert auth_header is not None
        self.assertTrue(auth_header.startswith("Bearer "))
        request = json.loads(server.bodies[0].decode("utf-8"))
        self.assertEqual(
            request,
            {
                "name": "ctx_read",
                "arguments": {
                    "path": "src/context.md",
                    "mode": "aggressive",
                    "engine_interface": "v1",
                },
            },
        )

    def test_receipt_binding_and_current_delivery_contract_fail_closed(self) -> None:
        invalid_receipts: tuple[Mapping[str, object], ...] = (
            {"schema_version": True},
            {"receipt_digest": "sha256:" + "A" * 64},
            {"receipt_ref": "id:sha256:" + "0" * 64},
            {"outcome": "completed"},
            {"delivery": "other"},
        )
        for overrides in invalid_receipts:
            with self.subTest(overrides=overrides):
                with _server(
                    _reply_factory(
                        200,
                        _wire_response(receipt_overrides=overrides),
                        {"Content-Type": "application/json"},
                    )
                ) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_read("src/context.md")

    def test_result_shape_errors_fail_closed(self) -> None:
        invalid_results: tuple[Mapping[str, object], ...] = (
            {"isError": True},
            {"content": []},
            {"content": [{"type": "image", "data": "x"}]},
            {"_meta": {}},
        )
        for updates in invalid_results:
            with self.subTest(updates=updates):
                with _server(
                    _reply_factory(
                        200,
                        _wire_response(result_updates=updates),
                        {"Content-Type": "application/json"},
                    )
                ) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_read("src/context.md")

    def test_request_and_wire_limits_are_bounded_before_untrusted_work(self) -> None:
        with _server(_reply_factory(500)) as server:
            client = _client(server)
            for invalid_path in ("", "\x00", "\x7f", "\x80", "\ud800"):
                with self.subTest(invalid_path=repr(invalid_path)):
                    with self.assertRaises(ValidationError):
                        client.context_read(invalid_path)
            with self.assertRaises(ValidationError):
                client.context_read("x" * (MAX_PATH_BYTES + 1))
            self.assertEqual(server.calls, 0)

        with _server(
            _reply_factory(
                200,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(MAX_RESPONSE_BYTES + 1),
                },
            )
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_read("src/context.md")


if __name__ == "__main__":
    unittest.main()
