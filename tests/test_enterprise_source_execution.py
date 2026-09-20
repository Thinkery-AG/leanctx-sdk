# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused loopback checks for Enterprise source execution consumption."""

from __future__ import annotations

import json
import unittest
from typing import Any, Mapping, cast

from leanctx_sdk.errors import EngineProtocolError, ValidationError
from leanctx_sdk.protocol import canonical_bytes, sha256_digest

from tests.test_enterprise_engine import (
    OTHER_SOURCE_ID,
    REQUEST,
    SOURCE_ID,
    TENANT_ID,
    _Reply,
    _client,
    _plan_response,
    _server,
)
from tests.test_enterprise_materialization import _source_plan


TASK: Mapping[str, object] = {
    "schema_version": 1,
    "task_id": REQUEST.task_id,
    "trace_id": "trace-enterprise-execution",
    "project_id": "project-enterprise",
    "session_id": "session-enterprise",
    "agent_id": "agent-enterprise",
    "complexity": "unknown",
    "created_at": "2026-09-20T12:34:56Z",
    "tenant_id": TENANT_ID,
}

PLAN: Mapping[str, object] = {
    "schema_version": 1,
    "plan_id": "execution-plan-request",
    "task_id": REQUEST.task_id,
    "context_budget_tokens": REQUEST.budget_tokens,
    "context_strategy": "minimal",
    "knowledge_refs": [],
    "capability_ids": ["capability://leanctx/context-optimization"],
    "model": "local-native",
    "provider": "local-native",
    "reasoning_allocation_milli": 0,
    "max_retries": 0,
    "fallback_refs": [],
    "stop_condition": "on_completion",
    "expected_cost_micros": 0,
    "expected_quality_milli": 0,
    "expected_latency_ms": 30000,
    "capability_bindings": [
        {
            "capability_id": "capability://leanctx/context-optimization",
            "version": "1.0.0",
        }
    ],
}


def _execution_response(
    *,
    tenant_id: str = TENANT_ID,
    source_plan: Mapping[str, object] | None = None,
    source_id: str = SOURCE_ID,
    outcome: str = "unknown",
    output_text: str = "executed source context",
    mutate_lineage: bool = False,
    mutate_plan: bool = False,
) -> bytes:
    source_plan_value = dict(source_plan or _source_plan())
    execution_plan: dict[str, object] = {
        "schema_version": 1,
        "plan_id": "execution-plan-request",
        "task_id": REQUEST.task_id,
        "context_budget_tokens": REQUEST.budget_tokens,
        "context_strategy": "minimal",
        "knowledge_refs": [],
        "capability_ids": ["capability://leanctx/context-optimization"],
        "model": "local-native",
        "provider": "local-native",
        "reasoning_allocation_milli": 0,
        "max_retries": 0,
        "fallback_refs": [],
        "stop_condition": "on_completion",
        "expected_cost_micros": 0,
        "expected_quality_milli": 0,
        "expected_latency_ms": 30000,
        "context_plan_id": "context-plan-1",
        "context_autopilot_decision_ref": "decision:context-autopilot-1",
        "capability_bindings": [
            {
                "capability_id": "capability://leanctx/context-optimization",
                "version": "1.0.0",
            }
        ],
    }
    if mutate_plan:
        execution_plan["max_retries"] = 1
    output_digest = sha256_digest(output_text.encode("utf-8"))
    input_digest = sha256_digest(b"materialized source input")
    task_digest = sha256_digest(canonical_bytes(TASK))
    source_plan_digest = sha256_digest(canonical_bytes(source_plan_value))
    execution_plan_digest = sha256_digest(canonical_bytes(execution_plan))
    refs = [
        "input:source-materialization-sha256:" + input_digest.removeprefix("sha256:"),
        "artifact://execution/evidence/" + source_plan_digest.removeprefix("sha256:"),
        "task:sha256:" + task_digest.removeprefix("sha256:"),
        "plan:sha256:" + execution_plan_digest.removeprefix("sha256:"),
    ]
    if mutate_lineage:
        refs[-1] = "plan:sha256:" + sha256_digest(b"wrong").removeprefix("sha256:")
    invocation_id = "invocation-enterprise-1"
    receipt_digest = sha256_digest(b"engine receipt")
    observation = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "status": "succeeded",
        "output_ref": "output:" + output_digest.removeprefix("sha256:"),
        "output_digest": output_digest,
        "source_lineage": refs,
        "measurements": [],
        "failure": None,
        "receipt_link": {
            "schema_version": 1,
            "receipt_id": "engine-receipt-id",
            "receipt_ref": "receipt:" + receipt_digest,
            "receipt_digest": receipt_digest,
            "invocation_id": invocation_id,
        },
    }
    execution = {
        "schema_version": 1,
        "transport_version": 1,
        "engine_interface_version": "1.0.0",
        "source_plan": source_plan_value,
        "execution_plan": execution_plan,
        "view": {
            "text": output_text,
            "output_ref": "output:" + output_digest.removeprefix("sha256:"),
            "output_digest": output_digest,
        },
        "invocation": {
            "schema_version": 1,
            "invocation_id": invocation_id,
            "engine": {"engine_id": "lean-ctx-local", "engine_version": "1.0.0"},
            "operation": {
                "capability_id": "capability://leanctx/context-optimization",
                "capability_version": "1.0.0",
            },
            "input_ref": refs[0],
            "input_digest": input_digest,
            "source_refs": refs,
            "policy_admission": {
                "policy_ref": "policy:engine-transport-v1:admitted",
                "decision": "admitted",
            },
        },
        "observation": observation,
        "canonical_receipt": {
            "receipt_id": "host-receipt-id",
            "receipt_ref": "id:" + sha256_digest(b"host receipt"),
            "receipt_digest": sha256_digest(b"host receipt"),
            "outcome": outcome,
        },
    }
    # `source_id` is used only to make an intentionally out-of-scope fixture;
    # callers supply a fully self-consistent source plan from the helper.
    _ = source_id
    return json.dumps(
        {
            "schema_version": 1,
            "tenant_id": tenant_id,
            "governance_revision": 7,
            "execution": execution,
        }
    ).encode("utf-8")


def _execution_v2_response(
    *,
    receipt_document_json: str = '{"receipt":"signed-v2-document"}',
    **kwargs: Any,
) -> bytes:
    response = cast(
        dict[str, Any], json.loads(_execution_response(**kwargs).decode("utf-8"))
    )
    document_digest = sha256_digest(receipt_document_json.encode("utf-8"))
    response["schema_version"] = 2
    response["execution"]["canonical_receipt"]["receipt_digest"] = document_digest
    response["execution"]["canonical_receipt"]["receipt_ref"] = "id:" + document_digest
    response["execution"] = {
        "schema_version": 2,
        "execution": response["execution"],
        "receipt_document_json": receipt_document_json,
    }
    return json.dumps(response, ensure_ascii=False).encode("utf-8")


class EnterpriseSourceExecutionTests(unittest.TestCase):
    def test_rejects_unicode_controls_before_network(self) -> None:
        binding_digest = cast(str, _source_plan()["binding_digest"])
        controls = (*range(0x20), *range(0x7F, 0xA0))
        with _server(lambda _handler, _body: _Reply(500)) as server:
            client = _client(server)
            for execute in (client.context_execute, client.context_execute_v2):
                for codepoint in controls:
                    with self.subTest(operation=execute.__name__, codepoint=codepoint):
                        with self.assertRaises(ValidationError):
                            execute(
                                {**TASK, "trace_id": "trace-" + chr(codepoint)},
                                PLAN,
                                REQUEST,
                                [SOURCE_ID],
                                7,
                                binding_digest,
                            )
            self.assertEqual(server.calls, 0)

    def test_rejects_response_control_ids_and_preserves_unicode(self) -> None:
        binding_digest = cast(str, _source_plan()["binding_digest"])
        for engine_id in (
            "engine-\x7f",
            "engine-\x80",
            "engine-\x9f",
            "Zürich-日本-🚀",
        ):
            with self.subTest(engine_id=ascii(engine_id)):
                response = json.loads(_execution_response())
                response["execution"]["invocation"]["engine"]["engine_id"] = engine_id
                payload = canonical_bytes(response)
                with _server(
                    lambda _handler, _body, payload=payload: _Reply(200, body=payload)
                ) as server:
                    if engine_id.startswith("engine-"):
                        with self.assertRaisesRegex(
                            EngineProtocolError, "control character"
                        ):
                            _client(server).context_execute(
                                TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
                            )
                    else:
                        result = _client(server).context_execute(
                            TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
                        )
                        execution = cast(Mapping[str, Any], result["execution"])
                        self.assertEqual(
                            execution["invocation"]["engine"]["engine_id"], engine_id
                        )

    def test_executes_and_returns_digest_bound_unknown_receipt_projection(self) -> None:
        source_plan = _source_plan()
        binding_digest = cast(str, source_plan["binding_digest"])
        with _server(
            lambda _handler, _body: _Reply(200, body=_execution_response())
        ) as server:
            result = _client(server).context_execute(
                TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
            )
        execution = cast(Mapping[str, Any], result["execution"])
        self.assertEqual(execution["execution_plan"]["task_id"], REQUEST.task_id)
        self.assertEqual(execution["view"]["output_digest"], sha256_digest(b"executed source context"))
        self.assertEqual(execution["canonical_receipt"]["outcome"], "unknown")
        self.assertEqual(
            execution["invocation"]["source_refs"], execution["observation"]["source_lineage"]
        )
        body = cast(dict[str, Any], json.loads(server.bodies[0]))
        self.assertEqual(set(body), {"task", "plan", "materialization"})
        self.assertNotIn("content", body["materialization"])
        self.assertEqual(len(server.auth_headers), 1)
        self.assertTrue(cast(str, server.auth_headers[0]).startswith("Bearer "))

    def test_rejects_caller_input_before_network(self) -> None:
        source_plan = _source_plan()
        binding_digest = cast(str, source_plan["binding_digest"])
        with _server(lambda _handler, _body: _Reply(500)) as server:
            client = _client(server)
            with self.assertRaises(ValidationError):
                client.context_execute({**TASK, "tenant_id": OTHER_SOURCE_ID}, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest)
            with self.assertRaises(ValidationError):
                client.context_execute(TASK, {**PLAN, "provider": "remote"}, REQUEST, [SOURCE_ID], 7, binding_digest)
            with self.assertRaises(ValidationError):
                client.context_execute(TASK, PLAN, REQUEST, [SOURCE_ID], True, binding_digest)
            self.assertEqual(server.calls, 0)

    def test_rejects_wrong_tenant_lineage_and_receipt_claim(self) -> None:
        source_plan = _source_plan()
        binding_digest = cast(str, source_plan["binding_digest"])
        cases = (
            ("tenant", _execution_response(tenant_id=OTHER_SOURCE_ID)),
            ("lineage", _execution_response(mutate_lineage=True)),
            ("plan", _execution_response(mutate_plan=True)),
            ("receipt", _execution_response(outcome="accepted")),
        )
        for label, body in cases:
            with self.subTest(label=label):
                with _server(cast(Any, lambda _handler, _body, body=body: _Reply(200, body=body))) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_execute(
                            TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
                        )

    def test_rejects_unrequested_source_and_wrong_output_digest(self) -> None:
        source_plan = cast(
            dict[str, Any], json.loads(_plan_response(source_id=OTHER_SOURCE_ID).decode("utf-8"))["plan"]
        )
        binding_digest = cast(str, source_plan["binding_digest"])
        with _server(
            lambda _handler, _body: _Reply(
                200, body=_execution_response(source_plan=source_plan)
            )
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_execute(
                    TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
                )

        malformed = json.loads(_execution_response().decode("utf-8"))
        malformed["execution"]["view"]["output_digest"] = sha256_digest(b"different")
        with _server(
            lambda _handler, _body: _Reply(200, body=json.dumps(malformed).encode("utf-8"))
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_execute(
                    TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
                )

    def test_v2_preserves_exact_receipt_document_and_v1_projection(self) -> None:
        source_plan = _source_plan()
        binding_digest = cast(str, source_plan["binding_digest"])
        document = '{"issued_at":"2026-09-20T12:34:56Z","note":"✓"}'
        with _server(
            lambda _handler, _body: _Reply(
                200, body=_execution_v2_response(receipt_document_json=document)
            )
        ) as server:
            result = _client(server).context_execute_v2(
                TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
            )
        self.assertEqual(
            set(result), {"schema_version", "tenant_id", "governance_revision", "execution"}
        )
        execution = cast(Mapping[str, Any], result["execution"])
        self.assertEqual(set(execution), {"schema_version", "execution", "receipt_document_json"})
        self.assertEqual(execution["schema_version"], 2)
        self.assertEqual(execution["receipt_document_json"].encode("utf-8"), document.encode("utf-8"))
        nested = cast(Mapping[str, Any], execution["execution"])
        self.assertEqual(nested["schema_version"], 1)
        self.assertEqual(
            nested["canonical_receipt"]["receipt_digest"],
            sha256_digest(document.encode("utf-8")),
        )
        self.assertEqual(server.calls, 1)

    def test_v2_rejects_schema_and_receipt_digest_mutations(self) -> None:
        source_plan = _source_plan()
        binding_digest = cast(str, source_plan["binding_digest"])
        valid = json.loads(_execution_v2_response().decode("utf-8"))
        cases: list[tuple[str, dict[str, Any]]] = []
        wrong_outer = json.loads(json.dumps(valid))
        wrong_outer["schema_version"] = 1
        cases.append(("outer schema", wrong_outer))
        wrong_nested = json.loads(json.dumps(valid))
        wrong_nested["execution"]["schema_version"] = 1
        cases.append(("nested schema", wrong_nested))
        wrong_digest = json.loads(json.dumps(valid))
        wrong_digest["execution"]["receipt_document_json"] = '{"changed":true}'
        cases.append(("receipt digest", wrong_digest))
        for label, value in cases:
            with self.subTest(label=label):
                with _server(
                    lambda _handler, _body, value=value: _Reply(
                        200, body=json.dumps(value, ensure_ascii=False).encode("utf-8")
                    )
                ) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_execute_v2(
                            TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
                        )

    def test_v2_rejects_oversized_receipt_document(self) -> None:
        source_plan = _source_plan()
        binding_digest = cast(str, source_plan["binding_digest"])
        oversized = "x" * (1024 * 1024 + 1)
        with _server(
            lambda _handler, _body: _Reply(
                200, body=_execution_v2_response(receipt_document_json=oversized)
            )
        ) as server:
            with self.assertRaises(EngineProtocolError):
                _client(server).context_execute_v2(
                    TASK, PLAN, REQUEST, [SOURCE_ID], 7, binding_digest
                )


if __name__ == "__main__":
    unittest.main()
