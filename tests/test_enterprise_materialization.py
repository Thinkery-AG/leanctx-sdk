# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused loopback checks for Enterprise source materialization."""

from __future__ import annotations

import json
import pickle
import unittest
from typing import Any, Mapping, cast

from leanctx_sdk.errors import (
    InstanceProtocolError,
    PolicyAdmissionError,
    UnsupportedInstanceError,
    ValidationError,
)
from leanctx_sdk.presenter import PRESENTER_CAPABILITY_HEADER, PresenterCapability
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
                    with self.assertRaises(InstanceProtocolError):
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
                    with self.assertRaises(InstanceProtocolError):
                        _client(server).context_materialize(
                            REQUEST,
                            [SOURCE_ID],
                            EXPECTED_GOVERNANCE_REVISION,
                            binding_digest,
                        )

    def test_v2_keeps_the_presenter_capability_out_of_bodies_and_renderings(self) -> None:
        binding_digest = cast(str, _source_plan()["binding_digest"])
        # Assembled at run time: the source holds no capability-shaped text.
        secret = "lcp1_" + "ab" * 32
        reference = "presenter:" + "0123456789abcdef" * 2
        seen: list[tuple[str, str | None]] = []

        def reply(handler: Any, _body: bytes) -> _Reply:
            shown = handler.headers.get(PRESENTER_CAPABILITY_HEADER)
            seen.append((handler.path, shown))
            grant: dict[str, object] = {
                "presenter_ref": reference,
                "expires_at_unix": 1_900_000_000,
            }
            if shown is None:
                grant["capability"] = secret
            v1 = json.loads(_materialization_response().decode("utf-8"))
            v1["schema_version"] = 2
            v1["materialization"] = {
                "schema_version": 2,
                "materialization": v1["materialization"],
                "presenter": grant,
            }
            return _Reply(200, body=json.dumps(v1).encode("utf-8"))

        with _server(reply) as server:
            client = _client(server)
            issued = client.context_materialize_v2(
                REQUEST, [SOURCE_ID], EXPECTED_GOVERNANCE_REVISION, binding_digest, 600
            )
            grant = cast(
                Mapping[str, Any],
                cast(Mapping[str, Any], issued["materialization"])["presenter"],
            )
            capability = grant["capability"]
            self.assertIsInstance(capability, PresenterCapability)
            renewed = client.context_materialize_v2(
                REQUEST,
                [SOURCE_ID],
                EXPECTED_GOVERNANCE_REVISION,
                binding_digest,
                capability,
            )
            for invalid in (0, True, 24 * 60 * 60 + 1, secret):
                with self.assertRaises(ValidationError):
                    client.context_materialize_v2(
                        REQUEST,
                        [SOURCE_ID],
                        EXPECTED_GOVERNANCE_REVISION,
                        binding_digest,
                        cast(Any, invalid),
                    )
            bodies = b"".join(server.bodies).decode("utf-8")

        self.assertEqual(
            seen,
            [
                ("/v1/engine/context-materialize-v2", None),
                ("/v1/engine/context-materialize-v2", secret),
            ],
        )
        self.assertNotIn(secret, bodies)
        self.assertEqual(json.loads(server.bodies[0])["presenter"], {"mode": "issue", "ttl_seconds": 600})
        self.assertEqual(json.loads(server.bodies[1])["presenter"], {"mode": "renew"})
        renewed_grant = cast(Mapping[str, Any], renewed["materialization"])["presenter"]
        self.assertIsNone(renewed_grant["capability"])
        self.assertEqual(renewed_grant["presenter_ref"], reference)
        for rendering in (repr(capability), str(capability), f"{capability}", repr(issued)):
            self.assertNotIn(secret, rendering)
        with self.assertRaises(TypeError):
            pickle.dumps(capability)

        # A suite without the route, or an Engine without the operation.
        for status in (404, 501):
            with self.subTest(status=status):
                with _server(lambda _handler, _body, status=status: _Reply(status)) as server:
                    with self.assertRaises(UnsupportedInstanceError):
                        _client(server).context_materialize_v2(
                            REQUEST,
                            [SOURCE_ID],
                            EXPECTED_GOVERNANCE_REVISION,
                            binding_digest,
                            600,
                        )

    def test_a_held_capability_is_used_and_revoked_in_the_header_only(self) -> None:
        # Assembled at run time: the source holds no capability-shaped text.
        capability = PresenterCapability("lcp1_" + "ab" * 32)
        secret = capability.reveal()
        reference = "presenter:" + "0123456789abcdef" * 2
        recovery_id = "5e" * 32
        seen: list[tuple[str, str | None]] = []

        def reply(handler: Any, body: bytes) -> _Reply:
            seen.append((handler.path, handler.headers.get(PRESENTER_CAPABILITY_HEADER)))
            if handler.path == "/v1/engine/presenter-revoke":
                answer: dict[str, object] = {
                    "schema_version": 1,
                    "tenant_id": TENANT_ID,
                    "revocation": {
                        "schema_version": 1,
                        "presenter_ref": reference,
                        "revoked": True,
                    },
                }
                return _Reply(200, body=json.dumps(answer).encode("utf-8"))
            asked = json.loads(body.decode("utf-8"))["use"]
            use: dict[str, object] = {
                "schema_version": 1,
                "presenter_ref": reference,
                "usable": True,
                "source_ids": [SOURCE_ID],
            }
            if asked["mode"] == "retain":
                use["recovery_id"] = recovery_id
            if asked["mode"] == "expand":
                use["recovery_id"] = asked["recovery_id"]
                use["content"] = "ledger"
            answer = {
                "schema_version": 1,
                "tenant_id": TENANT_ID,
                "governance_revision": EXPECTED_GOVERNANCE_REVISION,
                "use": use,
            }
            return _Reply(200, body=json.dumps(answer).encode("utf-8"))

        with _server(reply) as server:
            client = _client(server)
            scope = ([SOURCE_ID], EXPECTED_GOVERNANCE_REVISION)
            kept = cast(
                Mapping[str, Any],
                client.presented_use(capability, *scope, "retain", "ledger")["use"],
            )
            expanded = cast(
                Mapping[str, Any],
                client.presented_use(capability, *scope, "expand", kept["recovery_id"])["use"],
            )
            revoked = cast(
                Mapping[str, Any], client.presenter_revoke(capability)["revocation"]
            )
            for mode, value in (("expand", "../x"), ("check", ""), ("other", "ledger")):
                with self.assertRaises(ValidationError):
                    client.presented_use(capability, *scope, mode, value)
            bodies = b"".join(server.bodies).decode("utf-8")

        self.assertEqual(
            (kept["recovery_id"], expanded["content"], revoked["revoked"]),
            (recovery_id, "ledger", True),
        )
        self.assertEqual(
            seen,
            [
                ("/v1/engine/presented-use", secret),
                ("/v1/engine/presented-use", secret),
                ("/v1/engine/presenter-revoke", secret),
            ],
        )
        self.assertNotIn(secret, bodies)

        # The one capability refusal, a suite without the routes, and an
        # answer that hands the capability back inside the text.
        leaking = {
            "schema_version": 1,
            "tenant_id": TENANT_ID,
            "governance_revision": EXPECTED_GOVERNANCE_REVISION,
            "use": {
                "schema_version": 1,
                "presenter_ref": reference,
                "usable": True,
                "source_ids": [SOURCE_ID],
                "recovery_id": recovery_id,
                "content": "ledger " + secret,
            },
        }
        for answer_to, expected in (
            (_Reply(403), PolicyAdmissionError),
            (_Reply(404), UnsupportedInstanceError),
            (_Reply(200, body=json.dumps(leaking).encode("utf-8")), InstanceProtocolError),
        ):
            with self.subTest(expected=expected.__name__):
                with _server(lambda _handler, _body, answer_to=answer_to: answer_to) as server:
                    with self.assertRaises(expected):
                        _client(server).presented_use(
                            capability, *scope, "expand", recovery_id
                        )

        # Two sources asked, one named twice: a repeated id must not stand in
        # for the source the answer leaves out.
        other = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
        repeated = {
            "schema_version": 1,
            "tenant_id": TENANT_ID,
            "governance_revision": EXPECTED_GOVERNANCE_REVISION,
            "use": {
                "schema_version": 1,
                "presenter_ref": reference,
                "usable": True,
                "source_ids": [SOURCE_ID, SOURCE_ID],
            },
        }
        body = json.dumps(repeated).encode("utf-8")
        with _server(lambda _handler, _body: _Reply(200, body=body)) as server:
            with self.assertRaises(InstanceProtocolError):
                _client(server).presented_use(
                    capability,
                    [SOURCE_ID, other],
                    EXPECTED_GOVERNANCE_REVISION,
                    "check",
                    "ledger",
                )

    def test_presenter_response_schema_versions_require_integer_wire_values(self) -> None:
        capability = PresenterCapability("lcp1_" + "ab" * 32)
        secret = capability.reveal()
        reference = "presenter:" + "0123456789abcdef" * 2
        binding_digest = cast(str, _source_plan()["binding_digest"])
        marker = "__schema_version_token__"
        wire = b""
        seen: list[tuple[str, str | None]] = []

        def reply(handler: Any, _body: bytes) -> _Reply:
            seen.append((handler.path, handler.headers.get(PRESENTER_CAPABILITY_HEADER)))
            return _Reply(200, body=wire)

        with _server(reply) as server:
            client = _client(server)

            def invoke(operation: str) -> Mapping[str, object]:
                if operation == "materialize":
                    return client.context_materialize_v2(
                        REQUEST,
                        [SOURCE_ID],
                        EXPECTED_GOVERNANCE_REVISION,
                        binding_digest,
                        capability,
                    )
                if operation == "use":
                    return client.presented_use(
                        capability,
                        [SOURCE_ID],
                        EXPECTED_GOVERNANCE_REVISION,
                        "check",
                        "ledger",
                    )
                return client.presenter_revoke(capability)

            for label, operation, member, version, error in (
                ("v2-outer", "materialize", None, 2, "materialization response fields"),
                ("v2-inner", "materialize", "materialization", 2, "materialization fields"),
                ("use-outer", "use", None, 1, "Enterprise presenter response fields"),
                ("revoke-outer", "revoke", None, 1, "Enterprise presenter response fields"),
                ("use-inner", "use", "use", 1, "presented use fields"),
                ("revoke-inner", "revoke", "revocation", 1, "presenter revocation fields"),
            ):
                answer: dict[str, Any]
                if operation == "materialize":
                    answer = json.loads(_materialization_response().decode("utf-8"))
                    answer["schema_version"] = 2
                    answer["materialization"] = {
                        "schema_version": 2,
                        "materialization": answer["materialization"],
                        "presenter": {
                            "presenter_ref": reference,
                            "expires_at_unix": 1_900_000_000,
                        },
                    }
                elif operation == "use":
                    answer = {
                        "schema_version": 1,
                        "tenant_id": TENANT_ID,
                        "governance_revision": EXPECTED_GOVERNANCE_REVISION,
                        "use": {
                            "schema_version": 1,
                            "presenter_ref": reference,
                            "usable": True,
                            "source_ids": [SOURCE_ID],
                        },
                    }
                else:
                    answer = {
                        "schema_version": 1,
                        "tenant_id": TENANT_ID,
                        "revocation": {
                            "schema_version": 1,
                            "presenter_ref": reference,
                            "revoked": True,
                        },
                    }
                selected = (
                    answer if member is None else cast(dict[str, Any], answer[member])
                )
                selected["schema_version"] = marker
                encoded = json.dumps(answer, separators=(",", ":")).encode("utf-8")
                placeholder = json.dumps(marker).encode("utf-8")
                self.assertEqual(encoded.count(placeholder), 1)
                # Substitute raw lexemes: decoding and re-encoding a float would
                # lose the exponent spelling or the rounded decimal under test.
                for token in (
                    str(version).encode("ascii"),
                    f"{version}.0".encode("ascii"),
                    f"{version}e0".encode("ascii"),
                    f"{version}.0000000000000001".encode("ascii"),
                    b"true",
                ):
                    wire = encoded.replace(placeholder, token)
                    with self.subTest(guard=label, token=token.decode("ascii")):
                        if token == str(version).encode("ascii"):
                            result = invoke(operation)
                            self.assertEqual(result["schema_version"], version)
                            if operation == "materialize":
                                materialization = cast(
                                    Mapping[str, Any], result["materialization"]
                                )
                                self.assertEqual(
                                    materialization["materialization"]["content"],
                                    "materialized enterprise context",
                                )
                            elif operation == "use":
                                use = cast(Mapping[str, Any], result["use"])
                                self.assertIs(use["usable"], True)
                                self.assertEqual(use["source_ids"], (SOURCE_ID,))
                            else:
                                revocation = cast(Mapping[str, Any], result["revocation"])
                                self.assertIs(revocation["revoked"], True)
                                self.assertEqual(revocation["presenter_ref"], reference)
                        else:
                            with self.assertRaisesRegex(
                                InstanceProtocolError, error
                            ) as caught:
                                invoke(operation)
                            self.assertNotIn(secret, str(caught.exception))
            self.assertEqual(server.calls, len(seen))
            self.assertTrue(seen)
            self.assertTrue(all(shown == secret for _path, shown in seen))
            self.assertNotIn(secret, b"".join(server.bodies).decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
