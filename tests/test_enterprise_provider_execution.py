# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused loopback checks for the governed provider-execution SDK adapter."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping, cast
import unittest

from leanctx_sdk.enterprise_engine import EnterpriseEngineClient
from leanctx_sdk.errors import EngineProtocolError, ValidationError
from leanctx_sdk.protocol import sha256_digest

from tests.test_enterprise_engine import (
    REQUEST as PLAN_REQUEST,
    SOURCE_ID,
    TENANT_ID,
    _Reply,
    _client,
    _server,
)


TASK: Mapping[str, object] = {
    "schema_version": 1,
    "task_id": PLAN_REQUEST.task_id,
    "trace_id": "trace-provider-execution",
    "project_id": "project-provider",
    "session_id": "session-provider",
    "agent_id": "agent-provider",
    "complexity": "unknown",
    "created_at": "2026-09-20T12:34:56Z",
    "tenant_id": TENANT_ID,
}

PLAN: Mapping[str, object] = {
    "schema_version": 1,
    "plan_id": "provider-execution-plan",
    "task_id": PLAN_REQUEST.task_id,
    "context_plan_id": "provider-context-plan",
    "context_budget_tokens": PLAN_REQUEST.budget_tokens,
    "context_strategy": "minimal",
    "knowledge_refs": [],
    "capability_ids": ["capability://leanctx/context-optimization"],
    "model": "gpt-4o",
    "provider": "openai",
    "reasoning_allocation_milli": 0,
    "max_retries": 0,
    "fallback_refs": [],
    "stop_condition": "on_completion",
    "expected_cost_micros": 0,
    "expected_quality_milli": 0,
    "expected_latency_ms": 30000,
}


def _response(**changes: Any) -> bytes:
    output_text = "provider output"
    response: dict[str, Any] = {
        "schema_version": 1,
        "transport_version": 1,
        "engine_interface_version": "1.0.0",
        "attempt_id": "provider-attempt-1",
        "task_id": PLAN_REQUEST.task_id,
        "plan_id": "provider-execution-plan",
        "context_digest": sha256_digest(b"materialized context"),
        "request_digest": sha256_digest(b"host-owned request"),
        "provider": "openai",
        "model": "gpt-4o",
        "status": "succeeded",
        "acceptance": "unknown",
        "output": {
            "content": output_text,
            "sha256_digest": sha256_digest(output_text.encode("utf-8")),
        },
        "usage": {
            "state": "measured",
            "uncached_input_tokens": 100,
            "cache_write_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_input_tokens": 100,
            "output_tokens": 20,
        },
        "cost": {"basis": "unavailable"},
    }
    response.update(changes)
    return json.dumps(response).encode("utf-8")


def _call(*, response: bytes) -> Mapping[str, object]:
    with _server(lambda _handler, _body: _Reply(200, body=response)) as server:
        client = _client(server)
        return client.provider_execute(
            TASK,
            PLAN,
            PLAN_REQUEST,
            [SOURCE_ID],
            7,
            sha256_digest(b"source binding"),
            max_output_tokens=64,
            planning_evaluation_time="2026-09-20T12:34:56Z",
        )


class EnterpriseProviderExecutionTests(unittest.TestCase):
    def test_valid_response_and_request_wire_are_bound(self) -> None:
        with _server(lambda _handler, _body: _Reply(200, body=_response())) as server:
            result = _client(server).provider_execute(
                TASK,
                PLAN,
                PLAN_REQUEST,
                [SOURCE_ID],
                7,
                sha256_digest(b"source binding"),
                max_output_tokens=64,
                planning_evaluation_time="2026-09-20T12:34:56Z",
            )
            self.assertEqual(server.calls, 1)
            body = cast(dict[str, Any], json.loads(server.bodies[0]))
            self.assertEqual(
                set(body),
                {"schema_version", "task", "plan", "materialization", "max_output_tokens"},
            )
            self.assertEqual(body["schema_version"], 1)
            self.assertEqual(body["task"], dict(TASK))
            self.assertEqual(body["plan"], dict(PLAN))
            self.assertEqual(body["max_output_tokens"], 64)
            self.assertEqual(
                body["materialization"]["planning"],
                dict(PLAN_REQUEST.to_dict()),
            )
            self.assertEqual(
                body["materialization"]["planning_evaluation_time"],
                "2026-09-20T12:34:56Z",
            )
            self.assertEqual(result["acceptance"], "unknown")
            self.assertEqual(result["usage"]["total_input_tokens"], 100)

    def test_missing_usage_remains_unavailable_and_cost_estimate_is_rejected(self) -> None:
        unavailable = {
            "state": "unavailable",
            "uncached_input_tokens": None,
            "cache_write_input_tokens": None,
            "cache_read_input_tokens": None,
            "total_input_tokens": None,
            "output_tokens": None,
        }
        with self.assertRaises(EngineProtocolError):
            _call(
                response=_response(
                    usage=unavailable,
                    cost={"basis": "usage_priced_estimate", "micros": 1},
                ),
            )

    def test_output_digest_and_unknown_acceptance_are_strict(self) -> None:
        with self.assertRaises(EngineProtocolError):
            _call(
                response=_response(
                    output={"content": "tampered", "sha256_digest": sha256_digest(b"provider output")}
                ),
            )
        with self.assertRaises(EngineProtocolError):
            _call(
                response=_response(acceptance="accepted")
            )

    def test_non_success_requires_sanitized_failure_and_no_output(self) -> None:
        failure = {
            "code": "resource_limit",
            "retryable_by_host": False,
        }
        response = json.loads(_response().decode("utf-8"))
        response["status"] = "timed_out"
        response["output"] = None
        response["failure"] = failure
        result = _call(
            response=json.dumps(response).encode("utf-8")
        )
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["failure"], failure)

    def test_transport_and_failure_retry_types_are_strict(self) -> None:
        for non_object in (b"null", b"[]"):
            with self.assertRaises(EngineProtocolError):
                _call(response=non_object)
        for transport_version in (True, 1.0):
            with self.assertRaises(EngineProtocolError):
                _call(response=_response(transport_version=transport_version))
        response = json.loads(_response().decode("utf-8"))
        response["status"] = "failed"
        response["output"] = None
        response["failure"] = {
            "code": "internal",
            "retryable_by_host": True,
        }
        with self.assertRaises(EngineProtocolError):
            _call(response=json.dumps(response).encode("utf-8"))

    def test_context_plan_binding_and_budget_are_required_before_http(self) -> None:
        missing_context_plan = deepcopy(dict(PLAN))
        missing_context_plan.pop("context_plan_id")
        null_context_plan = deepcopy(dict(PLAN))
        null_context_plan["context_plan_id"] = None
        no_token_limit = deepcopy(dict(PLAN))
        no_token_limit["context_budget_tokens"] = 0
        no_token_limit["context_budget_policy"] = {"kind": "no_token_limit"}
        excessive_budget = deepcopy(dict(PLAN))
        excessive_budget["context_budget_tokens"] = PLAN_REQUEST.budget_tokens + 1
        with _server(lambda _handler, _body: _Reply(200, body=_response())) as server:
            client = _client(server)
            for invalid_plan in (
                missing_context_plan,
                null_context_plan,
                no_token_limit,
                excessive_budget,
            ):
                with self.assertRaises(ValidationError):
                    client.provider_execute(
                        TASK,
                        invalid_plan,
                        PLAN_REQUEST,
                        [SOURCE_ID],
                        7,
                        sha256_digest(b"source binding"),
                        max_output_tokens=64,
                    )
            self.assertEqual(server.calls, 0)

    def test_local_native_retry_and_output_bounds_are_rejected_before_http(self) -> None:
        local_plan = deepcopy(dict(PLAN))
        local_plan["provider"] = "local-native"
        local_plan["model"] = "local-native"
        with self.assertRaises(ValidationError):
            EnterpriseEngineClient(
                "http://127.0.0.1:1/",
                "credential-test",
                TENANT_ID,
                allow_loopback_http=True,
            ).provider_execute(
                TASK,
                local_plan,
                PLAN_REQUEST,
                [SOURCE_ID],
                7,
                sha256_digest(b"source binding"),
                max_output_tokens=0,
            )
        retry_plan = deepcopy(dict(PLAN))
        retry_plan["max_retries"] = 1
        with self.assertRaises(ValidationError):
            EnterpriseEngineClient(
                "http://127.0.0.1:1/",
                "credential-test",
                TENANT_ID,
                allow_loopback_http=True,
            ).provider_execute(
                TASK,
                retry_plan,
                PLAN_REQUEST,
                [SOURCE_ID],
                7,
                sha256_digest(b"source binding"),
                max_output_tokens=64,
            )
