# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0

import unittest

from leanctx_sdk.errors import ValidationError
from leanctx_sdk.planning import (
    EnginePlanningRequest,
    EngineSource,
    parse_context_plan,
    parse_source_plan,
)
from leanctx_sdk.protocol import canonical_bytes, sha256_digest


def _request():
    return EnginePlanningRequest("task-1", "invoice ledger", 512, 64)


def _source(object_ref="object-1", content="invoice payload"):
    return EngineSource(
        {
            "object_ref": object_ref,
            "source_id": "source-1",
            "source_type": "filesystem",
            "content_digest": sha256_digest(content.encode("utf-8")),
        },
        content,
    )


def _plan(request, source):
    unsigned = {
        "schema_version": 1,
        "context_plan_id": "plan-1",
        "task_id": request.task_id,
        "budget_tokens": 256,
        "selections": [
            {
                "source_ref": source.descriptor["object_ref"],
                "provider": source.descriptor["source_id"],
                "disposition": "selected",
                "token_count": 4,
                "sha256_digest": source.descriptor["content_digest"],
                "reason_codes": ["relevant"],
            }
        ],
    }
    plan = dict(unsigned)
    plan["projection_digest"] = sha256_digest(canonical_bytes(unsigned))
    return plan


def _source_response(request, source):
    result = {
        "schema_version": 1,
        "transport_version": 1,
        "engine_interface_version": "1.0.0",
        "plan": _plan(request, source),
    }
    return {
        "result": result,
        "source_bindings": [dict(source.descriptor)],
        "binding_digest": sha256_digest(
            canonical_bytes([result, [dict(source.descriptor)]])
        ),
    }


class EnginePlanningTests(unittest.TestCase):
    def test_request_and_source_are_frozen_and_strict(self):
        request = _request()
        self.assertEqual(request.to_dict()["max_candidates"], 64)
        source = _source()
        self.assertEqual(source.to_dict()["descriptor"]["permission"], "unknown")
        with self.assertRaises(ValidationError):
            EnginePlanningRequest("task-1", "query", True)
        with self.assertRaises(ValidationError):
            EngineSource(
                {
                    "object_ref": "object-1",
                    "source_id": "source-1",
                    "source_type": "filesystem",
                    "content_digest": "sha256:" + "0" * 64,
                },
                "payload",
            )

    def test_context_plan_binds_task_and_never_exceeds_requested_budget(self):
        request = _request()
        source = _source()
        response = {
            "schema_version": 1,
            "transport_version": 1,
            "engine_interface_version": "1.0.0",
            "plan": _plan(request, source),
        }
        parsed = parse_context_plan(canonical_bytes(response), request)
        self.assertEqual(parsed["plan"]["task_id"], request.task_id)
        self.assertEqual(parsed["plan"]["budget_tokens"], 256)
        wrong_task = dict(response)
        wrong_task["plan"] = dict(response["plan"], task_id="other-task")
        with self.assertRaises(ValidationError):
            parse_context_plan(canonical_bytes(wrong_task), request)
        over_budget = dict(response)
        over_budget["plan"] = dict(response["plan"], budget_tokens=513)
        with self.assertRaises(ValidationError):
            parse_context_plan(canonical_bytes(over_budget), request)

    def test_source_plan_self_binding_and_requested_source_binding(self):
        request = _request()
        source = _source()
        response = _source_response(request, source)
        parsed = parse_source_plan(canonical_bytes(response), request, [source])
        self.assertEqual(
            parsed["source_bindings"][0]["object_ref"], source.descriptor["object_ref"]
        )
        remote_parsed = parse_source_plan(canonical_bytes(response), request)
        self.assertEqual(remote_parsed["binding_digest"], response["binding_digest"])
        unknown_source = _source("object-2")
        unknown_response = _source_response(request, unknown_source)
        with self.assertRaises(ValidationError):
            parse_source_plan(canonical_bytes(unknown_response), request, [source])

    def test_source_plan_rejects_binding_tampering_and_selection_mismatch(self):
        request = _request()
        source = _source()
        response = _source_response(request, source)
        tampered = dict(response)
        tampered["binding_digest"] = sha256_digest(b"tampered")
        with self.assertRaises(ValidationError):
            parse_source_plan(canonical_bytes(tampered), request, [source])
        mismatch = _source_response(request, source)
        mismatch["result"] = dict(mismatch["result"])
        mismatch["result"]["plan"] = dict(mismatch["result"]["plan"])
        mismatch["result"]["plan"]["selections"] = []
        mismatch["result"]["plan"].pop("projection_digest")
        mismatch["result"]["plan"]["projection_digest"] = sha256_digest(
            canonical_bytes(mismatch["result"]["plan"])
        )
        mismatch["binding_digest"] = sha256_digest(
            canonical_bytes([mismatch["result"], mismatch["source_bindings"]])
        )
        with self.assertRaises(ValidationError):
            parse_source_plan(canonical_bytes(mismatch), request, [source])

    def test_source_plan_requires_the_projection_digest(self):
        request = _request()
        source = _source()
        response = _source_response(request, source)
        response["result"] = dict(response["result"])
        response["result"]["plan"] = dict(response["result"]["plan"])
        response["result"]["plan"].pop("projection_digest")
        response["binding_digest"] = sha256_digest(
            canonical_bytes([response["result"], response["source_bindings"]])
        )
        with self.assertRaises(ValidationError):
            parse_source_plan(canonical_bytes(response), request, [source])

    def test_projection_rejects_duplicate_fields_unknown_headers_and_bool_numbers(self):
        request = _request()
        source = _source()
        response = {
            "schema_version": 1,
            "transport_version": 1,
            "engine_interface_version": "1.0.0",
            "plan": _plan(request, source),
        }
        unknown = dict(response)
        unknown["future"] = True
        with self.assertRaises(ValidationError):
            parse_context_plan(canonical_bytes(unknown), request)
        boolean_budget = dict(response)
        boolean_budget["plan"] = dict(response["plan"], budget_tokens=True)
        with self.assertRaises(ValidationError):
            parse_context_plan(canonical_bytes(boolean_budget), request)
        duplicate = canonical_bytes(response).replace(
            b'"schema_version":1,', b'"schema_version":1,"schema_version":1,', 1
        )
        with self.assertRaises(ValidationError):
            parse_context_plan(duplicate, request)


if __name__ == "__main__":
    unittest.main()
