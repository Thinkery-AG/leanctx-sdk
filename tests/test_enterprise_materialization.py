# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused loopback checks for Enterprise source materialization."""

from __future__ import annotations

import json
import unittest
from typing import Any, Mapping, cast

from leanctx_sdk.errors import EngineProtocolError, ValidationError
from leanctx_sdk.protocol import sha256_digest

from tests.test_enterprise_engine import (
    REQUEST,
    SOURCE_ID,
    TENANT_ID,
    _Reply,
    _client,
    _plan_response,
    _server,
)


EXPECTED_GOVERNANCE_REVISION = 7


def _source_plan() -> Mapping[str, object]:
    response = cast(dict[str, Any], json.loads(_plan_response().decode("utf-8")))
    return cast(Mapping[str, object], response["plan"])


def _materialization_response(
    *,
    content: str = "materialized enterprise context",
    tenant_id: str = TENANT_ID,
    governance_revision: int = EXPECTED_GOVERNANCE_REVISION,
    materialized_token_count: int = 5,
    materialized_digest: str | None = None,
    source_plan: Mapping[str, object] | None = None,
) -> bytes:
    plan = dict(source_plan or _source_plan())
    encoded = content.encode("utf-8")
    digest = materialized_digest or sha256_digest(encoded)
    response = {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "governance_revision": governance_revision,
        "materialization": {
            "schema_version": 1,
            "transport_version": 1,
            "engine_interface_version": "1.0.0",
            "plan": plan,
            "materialized_digest": digest,
            "materialized_token_count": materialized_token_count,
            "content": content,
        },
    }
    return json.dumps(response).encode("utf-8")


class EnterpriseMaterializationTests(unittest.TestCase):
    def test_materializes_bound_plan_and_forwards_optional_evaluation_time(self) -> None:
        binding_digest = cast(str, _source_plan()["binding_digest"])

        with _server(
            lambda _handler, _body: _Reply(200, body=_materialization_response())
        ) as server:
            result = _client(server).context_materialize(
                REQUEST,
                [SOURCE_ID],
                EXPECTED_GOVERNANCE_REVISION,
                binding_digest,
                planning_evaluation_time="2026-09-20T12:34:56Z",
            )

        materialization = cast(Mapping[str, Any], result["materialization"])
        self.assertEqual(materialization["content"], "materialized enterprise context")
        self.assertEqual(materialization["materialized_token_count"], 5)
        self.assertEqual(
            materialization["materialized_digest"],
            sha256_digest(b"materialized enterprise context"),
        )
        request_body = cast(dict[str, Any], json.loads(server.bodies[0]))
        self.assertEqual(request_body["source_ids"], [SOURCE_ID])
        self.assertEqual(request_body["expected_governance_revision"], 7)
        self.assertEqual(request_body["expected_binding_digest"], binding_digest)
        self.assertEqual(
            request_body["planning_evaluation_time"], "2026-09-20T12:34:56Z"
        )
        self.assertEqual(server.auth_headers, ["Bearer credential-test"])

    def test_rejects_input_before_network(self) -> None:
        binding_digest = cast(str, _source_plan()["binding_digest"])

        with _server(lambda _handler, _body: _Reply(500)) as server:
            client = _client(server)
            with self.assertRaises(ValidationError):
                client.context_materialize(REQUEST, [SOURCE_ID], True, binding_digest)
            with self.assertRaises(ValidationError):
                client.context_materialize(REQUEST, [SOURCE_ID], 7, "bad-digest")
            with self.assertRaises(ValidationError):
                client.context_materialize(
                    REQUEST,
                    [SOURCE_ID],
                    7,
                    binding_digest,
                    planning_evaluation_time="not-a-timestamp",
                )
            self.assertEqual(server.calls, 0)

    def test_rejects_governance_binding_and_content_integrity(self) -> None:
        binding_digest = cast(str, _source_plan()["binding_digest"])
        cases = (
            ("governance", _materialization_response(governance_revision=8)),
            (
                "binding",
                _materialization_response(
                    source_plan={**_source_plan(), "binding_digest": sha256_digest(b"other")}
                ),
            ),
            (
                "content",
                _materialization_response(materialized_digest=sha256_digest(b"other")),
            ),
        )
        for _label, body in cases:
            with self.subTest(_label=_label):
                with _server(lambda _handler, _body, body=body: _Reply(200, body=body)) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_materialize(
                            REQUEST,
                            [SOURCE_ID],
                            EXPECTED_GOVERNANCE_REVISION,
                            binding_digest,
                        )

    def test_rejects_unbounded_or_malformed_materialization_fields(self) -> None:
        binding_digest = cast(str, _source_plan()["binding_digest"])
        for label, body in (
            ("token", _materialization_response(materialized_token_count=True)),
            ("token_budget", _materialization_response(materialized_token_count=513)),
            ("content", _materialization_response(content="x" * (1024 * 1024 + 1))),
        ):
            with self.subTest(label=label):
                with _server(lambda _handler, _body, body=body: _Reply(200, body=body)) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_materialize(
                            REQUEST,
                            [SOURCE_ID],
                            EXPECTED_GOVERNANCE_REVISION,
                            binding_digest,
                        )


if __name__ == "__main__":
    unittest.main()
