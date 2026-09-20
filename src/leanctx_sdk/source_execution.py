# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Strict SDK validation for the Enterprise source-execution envelope.

This module validates the shared Engine DTO joins at the authenticated SDK
boundary.  It does not execute a planner, authenticate sources, verify a
signer, or turn the canonical receipt projection into an acceptance claim.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, NoReturn, Sequence, Tuple, cast

from .engine import _parse_observation, _parse_view
from .errors import EngineProtocolError, ValidationError
from .planning import ENGINE_INTERFACE_VERSION, EnginePlanningRequest, parse_source_plan
from .protocol import (
    MAX_REF_BYTES,
    MAX_REFS,
    _plain,
    canonical_bytes,
    sha256_digest,
    strict_json_loads,
    validate_digest,
    validate_ref,
)


_SCHEMA_VERSION = 1
_TRANSPORT_VERSION = 1
_MAX_U64 = (1 << 64) - 1
_MAX_U32 = (1 << 32) - 1
_MAX_TASK_BYTES = 16 * 1024
_MAX_EXECUTION_VIEW_BYTES = 1024 * 1024
_MAX_LIST_ITEMS = 256
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_LOCAL_NATIVE = "local-native"
_CAPABILITY = "capability://leanctx/context-optimization"
_CAPABILITY_VERSION = "1.0.0"
_INPUT_REF_PREFIX = "input:source-materialization-sha256:"
_SOURCE_PLAN_EVIDENCE_PREFIX = "artifact://execution/evidence/"
_TASK_REF_PREFIX = "task:sha256:"
_PLAN_REF_PREFIX = "plan:sha256:"

_TASK_REQUIRED = {
    "schema_version",
    "task_id",
    "trace_id",
    "project_id",
    "session_id",
    "agent_id",
    "complexity",
    "created_at",
}
_TASK_OPTIONAL = {
    "parent_task_id",
    "tenant_id",
    "intent",
    "task_class",
    "risk_class",
    "quality_requirement_milli",
    "cost_budget_micros",
    "latency_budget_ms",
    "data_classification",
    "region_policy_ref",
    "model_policy_ref",
    "context_state_ref",
    "outcome_contract_ref",
}
_PLAN_REQUIRED = {
    "schema_version",
    "plan_id",
    "task_id",
    "context_budget_tokens",
    "context_strategy",
    "knowledge_refs",
    "capability_ids",
    "model",
    "provider",
    "reasoning_allocation_milli",
    "max_retries",
    "fallback_refs",
    "stop_condition",
    "expected_cost_micros",
    "expected_quality_milli",
    "expected_latency_ms",
}
_PLAN_OPTIONAL = {
    "context_budget_policy",
    "estimates",
    "policy_decision_ref",
    "scheduler_decision_ref",
    "executor_agent_id",
    "context_plan_id",
    "capability_bindings",
}
_EXECUTION_KEYS = {
    "schema_version",
    "transport_version",
    "engine_interface_version",
    "source_plan",
    "execution_plan",
    "view",
    "invocation",
    "observation",
    "canonical_receipt",
}


def _fail(message: str, *, protocol: bool) -> NoReturn:
    if protocol:
        raise EngineProtocolError(message)
    raise ValidationError(message)


def _mapping(value: Any, field: str, *, protocol: bool) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail(f"{field} must be an object with string keys", protocol=protocol)
    try:
        canonical_bytes(dict(value))
    except ValidationError:
        _fail(f"{field} contains non-canonical JSON data", protocol=protocol)
    return value


def _text(
    value: Any,
    field: str,
    *,
    maximum: int = MAX_REF_BYTES,
    protocol: bool,
    printable: bool = False,
) -> str:
    if not isinstance(value, str):
        _fail(f"{field} must be a string", protocol=protocol)
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail(f"{field} is not valid UTF-8", protocol=protocol)
    if not encoded or len(encoded) > maximum or "\x00" in value:
        _fail(f"{field} exceeds its byte bound", protocol=protocol)
    if any(ord(char) < 0x20 for char in value):
        _fail(f"{field} contains a control character", protocol=protocol)
    if printable and any(not 0x20 <= ord(char) <= 0x7E for char in value):
        _fail(f"{field} must be printable ASCII", protocol=protocol)
    return value


def _integer(
    value: Any,
    field: str,
    *,
    maximum: int = _MAX_U64,
    protocol: bool,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _fail(f"{field} must be an unsigned bounded integer", protocol=protocol)
    return value


def _exact_version(value: Any, field: str, *, protocol: bool) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value != _SCHEMA_VERSION
    ):
        _fail(f"{field} is unsupported", protocol=protocol)


def _optional_text(
    value: Any, field: str, *, protocol: bool, maximum: int = MAX_REF_BYTES
) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum=maximum, protocol=protocol)


def _validate_string_list(value: Any, field: str, *, protocol: bool) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_LIST_ITEMS:
        _fail(f"{field} has an invalid shape", protocol=protocol)
    result = [_text(item, field, protocol=protocol) for item in value]
    if len(set(result)) != len(result):
        _fail(f"{field} contains duplicates", protocol=protocol)
    return result


def _validate_task(
    value: Any,
    request: EnginePlanningRequest,
    tenant_id: str,
    *,
    protocol: bool,
) -> dict[str, Any]:
    raw = _mapping(value, "task", protocol=protocol)
    if not _TASK_REQUIRED.issubset(raw):
        _fail("task is missing a required field", protocol=protocol)
    _exact_version(raw["schema_version"], "task.schema_version", protocol=protocol)
    task_id = _text(raw["task_id"], "task.task_id", protocol=protocol)
    if task_id != request.task_id:
        _fail("task.task_id does not bind the planning request", protocol=protocol)
    for field in ("trace_id", "project_id", "session_id", "agent_id"):
        _text(raw[field], f"task.{field}", protocol=protocol)
    complexity = raw["complexity"]
    if complexity not in {"unknown", "low", "medium", "high", "critical"}:
        _fail("task.complexity is unsupported", protocol=protocol)
    _text(raw["created_at"], "task.created_at", protocol=protocol)
    if "tenant_id" not in raw or raw["tenant_id"] != tenant_id:
        _fail("task.tenant_id does not bind the authenticated tenant", protocol=protocol)
    if "parent_task_id" in raw:
        parent = _optional_text(raw["parent_task_id"], "task.parent_task_id", protocol=protocol)
        if parent == task_id:
            _fail("task cannot be its own parent", protocol=protocol)
    for field in (
        "intent",
        "task_class",
        "region_policy_ref",
        "model_policy_ref",
        "context_state_ref",
        "outcome_contract_ref",
    ):
        if field in raw:
            _optional_text(raw[field], f"task.{field}", protocol=protocol)
    if "risk_class" in raw and raw["risk_class"] is not None:
        if raw["risk_class"] not in {"low", "medium", "high", "critical"}:
            _fail("task.risk_class is unsupported", protocol=protocol)
    if "quality_requirement_milli" in raw and raw["quality_requirement_milli"] is not None:
        _integer(raw["quality_requirement_milli"], "task.quality_requirement_milli", maximum=1000, protocol=protocol)
    for field in ("cost_budget_micros", "latency_budget_ms"):
        if field in raw and raw[field] is not None:
            _integer(raw[field], f"task.{field}", protocol=protocol)
    if "data_classification" in raw and raw["data_classification"] is not None:
        if raw["data_classification"] not in {"Public", "Internal", "Confidential", "Restricted"}:
            _fail("task.data_classification is unsupported", protocol=protocol)
    normalized = dict(raw)
    for field in _TASK_OPTIONAL:
        if normalized.get(field) is None:
            normalized.pop(field, None)
    if len(canonical_bytes(normalized)) > _MAX_TASK_BYTES:
        _fail("task exceeds its byte bound", protocol=protocol)
    return normalized


def _validate_plan(
    value: Any,
    task_id: str,
    *,
    protocol: bool,
    allow_context_plan_id: bool,
) -> dict[str, Any]:
    raw = _mapping(value, "plan", protocol=protocol)
    if not _PLAN_REQUIRED.issubset(raw):
        _fail("plan is missing a required field", protocol=protocol)
    _exact_version(raw["schema_version"], "plan.schema_version", protocol=protocol)
    plan_task_id = _text(raw["task_id"], "plan.task_id", protocol=protocol)
    if plan_task_id != task_id:
        _fail("plan.task_id does not bind task.task_id", protocol=protocol)
    _text(raw["plan_id"], "plan.plan_id", protocol=protocol)
    budget = _integer(raw["context_budget_tokens"], "plan.context_budget_tokens", protocol=protocol)
    if raw["context_strategy"] not in {"minimal", "balanced", "comprehensive", "cached_first"}:
        _fail("plan.context_strategy is unsupported", protocol=protocol)
    _validate_string_list(raw["knowledge_refs"], "plan.knowledge_refs", protocol=protocol)
    capabilities = _validate_string_list(raw["capability_ids"], "plan.capability_ids", protocol=protocol)
    if not capabilities:
        _fail("plan.capability_ids must not be empty", protocol=protocol)
    _text(raw["model"], "plan.model", protocol=protocol)
    _text(raw["provider"], "plan.provider", protocol=protocol)
    _integer(raw["reasoning_allocation_milli"], "plan.reasoning_allocation_milli", maximum=1000, protocol=protocol)
    _integer(raw["max_retries"], "plan.max_retries", maximum=_MAX_U32, protocol=protocol)
    _validate_string_list(raw["fallback_refs"], "plan.fallback_refs", protocol=protocol)
    if raw["stop_condition"] not in {"on_completion", "on_acceptance", "on_budget_exhaustion", "on_error", "manual"}:
        _fail("plan.stop_condition is unsupported", protocol=protocol)
    for field in ("expected_cost_micros", "expected_latency_ms"):
        _integer(raw[field], f"plan.{field}", protocol=protocol)
    _integer(raw["expected_quality_milli"], "plan.expected_quality_milli", maximum=1000, protocol=protocol)
    context_plan_id = raw.get("context_plan_id")
    if context_plan_id is not None:
        if not allow_context_plan_id:
            _fail("source execution request plan must not contain context_plan_id", protocol=protocol)
        _text(context_plan_id, "plan.context_plan_id", protocol=protocol)
    budget_policy = raw.get("context_budget_policy")
    if budget_policy is not None:
        policy = _mapping(budget_policy, "plan.context_budget_policy", protocol=protocol)
        kind = policy.get("kind")
        if kind == "token_limit":
            if set(policy) != {"kind", "tokens"}:
                _fail("plan.context_budget_policy fields are invalid", protocol=protocol)
            if _integer(policy["tokens"], "plan.context_budget_policy.tokens", protocol=protocol) != budget:
                _fail("plan.context_budget_policy disagrees with context_budget_tokens", protocol=protocol)
        elif kind == "no_token_limit":
            if set(policy) != {"kind"} or budget != 0:
                _fail("plan.context_budget_policy disagrees with context_budget_tokens", protocol=protocol)
        else:
            _fail("plan.context_budget_policy.kind is unsupported", protocol=protocol)
    estimates = raw.get("estimates")
    if estimates is not None:
        estimates_map = _mapping(estimates, "plan.estimates", protocol=protocol)
        if set(estimates_map) != {"cost_micros", "quality_milli", "latency_ms"}:
            _fail("plan.estimates fields are invalid", protocol=protocol)
        for field in ("cost_micros", "quality_milli", "latency_ms"):
            maximum = 1000 if field == "quality_milli" else _MAX_U64
            estimate = estimates_map[field]
            if estimate is not None:
                _integer(estimate, f"plan.estimates.{field}", maximum=maximum, protocol=protocol)
        if estimates_map["cost_micros"] not in (None, raw["expected_cost_micros"]):
            _fail("plan.estimates.cost_micros disagrees with legacy scalar", protocol=protocol)
        if estimates_map["quality_milli"] not in (None, raw["expected_quality_milli"]):
            _fail("plan.estimates.quality_milli disagrees with legacy scalar", protocol=protocol)
        if estimates_map["latency_ms"] not in (None, raw["expected_latency_ms"]):
            _fail("plan.estimates.latency_ms disagrees with legacy scalar", protocol=protocol)
    for field in ("policy_decision_ref", "scheduler_decision_ref", "executor_agent_id"):
        if field in raw and raw[field] is not None:
            _text(raw[field], f"plan.{field}", protocol=protocol)
    bindings = raw.get("capability_bindings")
    if bindings is not None:
        if not isinstance(bindings, list) or len(bindings) != len(capabilities):
            _fail("plan.capability_bindings has an invalid shape", protocol=protocol)
        seen: set[str] = set()
        for index, binding in enumerate(bindings):
            item = _mapping(binding, f"plan.capability_bindings[{index}]", protocol=protocol)
            if set(item) != {"capability_id", "version"}:
                _fail("plan.capability_bindings entry is invalid", protocol=protocol)
            capability_id = _text(item["capability_id"], "plan.capability_bindings.capability_id", protocol=protocol)
            version = _text(item["version"], "plan.capability_bindings.version", protocol=protocol)
            if capability_id not in capabilities or capability_id in seen:
                _fail("plan.capability_bindings does not match capability_ids", protocol=protocol)
            seen.add(capability_id)
            if not _SEMVER_RE.fullmatch(version):
                _fail("plan.capability_bindings.version is invalid", protocol=protocol)
    normalized = dict(raw)
    for field in _PLAN_OPTIONAL:
        if normalized.get(field) is None:
            normalized.pop(field, None)
    if not allow_context_plan_id:
        normalized.pop("context_plan_id", None)
    # Preserve all additive extension fields, but force them through the same
    # canonical JSON gate used by the wire protocol.
    try:
        canonical_bytes(normalized)
    except ValidationError:
        _fail("plan contains non-canonical JSON data", protocol=protocol)
    return normalized


def _validate_source_scope(plan: Mapping[str, Any], source_ids: Tuple[str, ...]) -> None:
    requested = set(source_ids)
    result = cast(Mapping[str, Any], plan["result"])
    result_plan = cast(Mapping[str, Any], result["plan"])
    for selection in cast(Sequence[Mapping[str, Any]], result_plan["selections"]):
        if selection["source_ref"] not in requested or selection["provider"] not in requested:
            raise EngineProtocolError("source execution selection is outside requested sources")
    for binding in cast(Sequence[Mapping[str, Any]], plan["source_bindings"]):
        if binding["object_ref"] not in requested or binding["source_id"] not in requested:
            raise EngineProtocolError("source execution binding is outside requested sources")
        if binding["permission"] != "permitted":
            raise EngineProtocolError("source execution selected source is not permitted")


def _digest_suffix(value: str, prefix: str, field: str) -> str:
    if not value.startswith(prefix):
        raise EngineProtocolError(f"{field} has an unsupported reference prefix")
    try:
        return validate_digest("sha256:" + value[len(prefix) :], field)
    except ValidationError as exc:
        raise EngineProtocolError(f"{field} is not digest-bound") from exc


def _parse_invocation(value: Any) -> Mapping[str, object]:
    raw = _mapping(value, "execution.invocation", protocol=True)
    expected = {"schema_version", "invocation_id", "engine", "operation", "input_ref", "input_digest", "source_refs", "policy_admission"}
    if set(raw) != expected:
        raise EngineProtocolError("execution.invocation fields do not match the v1 contract")
    _exact_version(raw["schema_version"], "execution.invocation.schema_version", protocol=True)
    invocation_id = _text(raw["invocation_id"], "execution.invocation.invocation_id", protocol=True)
    engine = _mapping(raw["engine"], "execution.invocation.engine", protocol=True)
    if set(engine) != {"engine_id", "engine_version"}:
        raise EngineProtocolError("execution.invocation.engine fields are invalid")
    engine_id = _text(engine["engine_id"], "execution.invocation.engine.engine_id", protocol=True)
    engine_version = _text(engine["engine_version"], "execution.invocation.engine.engine_version", protocol=True)
    if not _SEMVER_RE.fullmatch(engine_version):
        raise EngineProtocolError("execution.invocation.engine.engine_version is invalid")
    operation = _mapping(raw["operation"], "execution.invocation.operation", protocol=True)
    if set(operation) != {"capability_id", "capability_version"}:
        raise EngineProtocolError("execution.invocation.operation fields are invalid")
    if operation["capability_id"] != _CAPABILITY or operation["capability_version"] != _CAPABILITY_VERSION:
        raise EngineProtocolError("execution.invocation is not bound to local-native capability")
    policy = _mapping(raw["policy_admission"], "execution.invocation.policy_admission", protocol=True)
    if set(policy) != {"policy_ref", "decision"} or policy["decision"] != "admitted":
        raise EngineProtocolError("execution.invocation is not policy-admitted")
    policy_ref = _text(policy["policy_ref"], "execution.invocation.policy_admission.policy_ref", protocol=True, printable=True)
    try:
        input_ref = validate_ref(raw["input_ref"], "execution.invocation.input_ref")
        input_digest = validate_digest(
            raw["input_digest"], "execution.invocation.input_digest"
        )
    except ValidationError as exc:
        raise EngineProtocolError("execution invocation input binding is invalid") from exc
    source_refs = raw["source_refs"]
    if not isinstance(source_refs, list) or not 0 < len(source_refs) <= MAX_REFS:
        raise EngineProtocolError("execution.invocation.source_refs exceeds its bound")
    try:
        normalized_refs = [
            validate_ref(item, "execution.invocation.source_refs") for item in source_refs
        ]
    except ValidationError as exc:
        raise EngineProtocolError("execution invocation source refs are invalid") from exc
    if len(set(normalized_refs)) != len(normalized_refs) or input_ref not in normalized_refs:
        raise EngineProtocolError("execution.invocation source refs are not unique or omit input_ref")
    return {
        "schema_version": _SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "engine": {"engine_id": engine_id, "engine_version": engine_version},
        "operation": {"capability_id": _CAPABILITY, "capability_version": _CAPABILITY_VERSION},
        "input_ref": input_ref,
        "input_digest": input_digest,
        "source_refs": tuple(normalized_refs),
        "policy_admission": {"policy_ref": policy_ref, "decision": "admitted"},
    }


def _validate_lineage(
    invocation: Mapping[str, object],
    source_plan_digest: str,
    execution_plan_digest: str,
    task_digest: str,
) -> None:
    refs = cast(Sequence[str], invocation["source_refs"])
    if len(refs) != 4:
        raise EngineProtocolError("execution invocation must contain four lineage references")
    input_ref = cast(str, invocation["input_ref"])
    input_digest = cast(str, invocation["input_digest"])
    if _digest_suffix(input_ref, _INPUT_REF_PREFIX, "execution.invocation.input_ref") != input_digest:
        raise EngineProtocolError("execution input_ref does not bind input_digest")
    kinds = {"input": 0, "evidence": 0, "task": 0, "plan": 0}
    for ref in refs:
        if ref.startswith(_INPUT_REF_PREFIX):
            kinds["input"] += 1
            if _digest_suffix(ref, _INPUT_REF_PREFIX, "execution.invocation.source_refs") != input_digest:
                raise EngineProtocolError("execution input lineage does not bind input_digest")
        elif ref.startswith(_SOURCE_PLAN_EVIDENCE_PREFIX):
            kinds["evidence"] += 1
            if _digest_suffix(ref, _SOURCE_PLAN_EVIDENCE_PREFIX, "execution.invocation.source_refs") != source_plan_digest:
                raise EngineProtocolError("execution source-plan evidence is not digest-bound")
        elif ref.startswith(_TASK_REF_PREFIX):
            kinds["task"] += 1
            if _digest_suffix(ref, _TASK_REF_PREFIX, "execution.invocation.source_refs") != task_digest:
                raise EngineProtocolError("execution task evidence is not digest-bound")
        elif ref.startswith(_PLAN_REF_PREFIX):
            kinds["plan"] += 1
            if _digest_suffix(ref, _PLAN_REF_PREFIX, "execution.invocation.source_refs") != execution_plan_digest:
                raise EngineProtocolError("execution plan evidence is not digest-bound")
        else:
            raise EngineProtocolError("execution invocation contains unknown lineage reference")
    if kinds != {"input": 1, "evidence": 1, "task": 1, "plan": 1}:
        raise EngineProtocolError("execution invocation lineage is incomplete")


def _parse_receipt(value: Any) -> Mapping[str, object]:
    raw = _mapping(value, "execution.canonical_receipt", protocol=True)
    if set(raw) != {"receipt_id", "receipt_ref", "receipt_digest", "outcome"}:
        raise EngineProtocolError("execution canonical receipt fields are invalid")
    receipt_id = _text(raw["receipt_id"], "execution.canonical_receipt.receipt_id", protocol=True, printable=True)
    try:
        receipt_digest = validate_digest(
            raw["receipt_digest"], "execution.canonical_receipt.receipt_digest"
        )
        receipt_ref = validate_ref(
            raw["receipt_ref"], "execution.canonical_receipt.receipt_ref"
        )
    except ValidationError as exc:
        raise EngineProtocolError("execution canonical receipt identity is invalid") from exc
    if receipt_ref != "id:" + receipt_digest:
        raise EngineProtocolError("execution canonical receipt reference is not digest-bound")
    if raw["outcome"] != "unknown":
        raise EngineProtocolError("execution canonical receipt outcome must remain unknown")
    return {
        "receipt_id": receipt_id,
        "receipt_ref": receipt_ref,
        "receipt_digest": receipt_digest,
        "outcome": "unknown",
    }


def validate_execution_request(
    task: Any,
    plan: Any,
    request: EnginePlanningRequest,
    tenant_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and canonicalize caller-owned task/plan input."""
    if not isinstance(request, EnginePlanningRequest):
        raise ValidationError("context_execute requires EnginePlanningRequest")
    normalized_task = _validate_task(task, request, tenant_id, protocol=False)
    normalized_plan = _validate_plan(
        plan,
        cast(str, normalized_task["task_id"]),
        protocol=False,
        allow_context_plan_id=False,
    )
    if normalized_plan["provider"] != _LOCAL_NATIVE or normalized_plan["model"] != _LOCAL_NATIVE:
        raise ValidationError("context_execute requires a local-native plan")
    if normalized_plan["capability_ids"] != [_CAPABILITY]:
        raise ValidationError("context_execute requires the local-native capability")
    bindings = normalized_plan.get("capability_bindings")
    if bindings is None or len(cast(Sequence[Any], bindings)) != 1 or cast(Sequence[Any], bindings)[0] != {"capability_id": _CAPABILITY, "version": _CAPABILITY_VERSION}:
        raise ValidationError("context_execute requires a bound local-native capability")
    return normalized_task, normalized_plan


def parse_source_execution_response(
    raw: bytes,
    request: EnginePlanningRequest,
    source_ids: Tuple[str, ...],
    task: Mapping[str, Any],
    plan: Mapping[str, Any],
    tenant_id: str,
    expected_governance_revision: int,
    expected_binding_digest: str,
    planning_evaluation_time: str | None = None,
) -> Mapping[str, object]:
    """Parse and cross-bind an Enterprise source execution response."""
    try:
        value = strict_json_loads(raw, label="Enterprise Engine source execution response")
    except ValidationError as exc:
        raise EngineProtocolError("Enterprise Engine source execution response is not valid JSON") from exc
    if set(value) != {"schema_version", "tenant_id", "governance_revision", "execution"}:
        raise EngineProtocolError("Enterprise source execution response wrapper is invalid")
    _exact_version(value["schema_version"], "response.schema_version", protocol=True)
    if value["tenant_id"] != tenant_id:
        raise EngineProtocolError("Enterprise source execution response tenant binding does not match")
    revision = _integer(value["governance_revision"], "response.governance_revision", protocol=True)
    if revision != expected_governance_revision:
        raise EngineProtocolError("Enterprise source execution governance revision does not match")
    execution_raw = _mapping(value["execution"], "execution", protocol=True)
    if set(execution_raw) != _EXECUTION_KEYS:
        raise EngineProtocolError("Enterprise source execution fields do not match the v1 contract")
    _exact_version(execution_raw["schema_version"], "execution.schema_version", protocol=True)
    _exact_version(execution_raw["transport_version"], "execution.transport_version", protocol=True)
    if execution_raw["engine_interface_version"] != ENGINE_INTERFACE_VERSION:
        raise EngineProtocolError("Enterprise source execution Engine interface is unsupported")
    source_plan_raw = execution_raw["source_plan"]
    try:
        source_plan = parse_source_plan(canonical_bytes(source_plan_raw), request)
    except ValidationError as exc:
        raise EngineProtocolError("Enterprise source execution source plan is invalid") from exc
    _validate_source_scope(cast(Mapping[str, Any], source_plan), source_ids)
    binding_digest = validate_digest(expected_binding_digest, "expected_binding_digest")
    if source_plan["binding_digest"] != binding_digest:
        raise EngineProtocolError("Enterprise source execution binding digest does not match")
    execution_plan_raw = execution_raw["execution_plan"]
    execution_plan = _validate_plan(
        execution_plan_raw,
        request.task_id,
        protocol=True,
        allow_context_plan_id=True,
    )
    source_result = cast(Mapping[str, Any], source_plan["result"])
    source_result_plan = cast(Mapping[str, Any], source_result["plan"])
    if execution_plan.get("context_plan_id") != source_result_plan["context_plan_id"]:
        raise EngineProtocolError("Enterprise source execution plan does not bind source plan")
    if execution_plan["context_budget_tokens"] != source_result_plan["budget_tokens"]:
        raise EngineProtocolError("Enterprise source execution budget does not bind source plan")
    if execution_plan["provider"] != _LOCAL_NATIVE or execution_plan["model"] != _LOCAL_NATIVE or execution_plan["capability_ids"] != [_CAPABILITY]:
        raise EngineProtocolError("Enterprise source execution plan is not local-native")
    execution_bindings = execution_plan.get("capability_bindings")
    if execution_bindings != [
        {"capability_id": _CAPABILITY, "version": _CAPABILITY_VERSION}
    ]:
        raise EngineProtocolError("Enterprise source execution capability binding is invalid")
    expected_plan = dict(plan)
    expected_plan["context_plan_id"] = execution_plan["context_plan_id"]
    decision_ref = "context_autopilot_decision_ref"
    if decision_ref not in expected_plan:
        decision_value = execution_plan.get(decision_ref)
        if not isinstance(decision_value, str) or not decision_value:
            raise EngineProtocolError("Enterprise source execution decision reference is missing")
        expected_plan[decision_ref] = decision_value
    if expected_plan != execution_plan:
        raise EngineProtocolError("Enterprise source execution changed the declared plan")
    if planning_evaluation_time is not None:
        marker = source_result_plan.get("context_plan_evaluation_v1")
        actual_epoch = (
            marker.get("evaluation_time")
            if isinstance(marker, Mapping)
            else None
        )
        if actual_epoch != planning_evaluation_time:
            raise EngineProtocolError("Enterprise source execution changed the evaluation time")
    view = _parse_view(execution_raw["view"])
    if not view["output_digest"] or not view["output_ref"]:
        raise EngineProtocolError("Enterprise source execution view is missing output binding")
    if len(cast(str, view["text"]).encode("utf-8")) > _MAX_EXECUTION_VIEW_BYTES:
        raise EngineProtocolError("Enterprise source execution view exceeds its byte bound")
    invocation = _parse_invocation(execution_raw["invocation"])
    invocation_value = cast(Mapping[str, Any], invocation)
    observation = _parse_observation(
        execution_raw["observation"], cast(str, invocation_value["invocation_id"])
    )
    observation_value = cast(Mapping[str, Any], observation)
    if cast(Mapping[str, Any], invocation_value["policy_admission"])["decision"] != "admitted":
        raise EngineProtocolError("Enterprise source execution invocation is not admitted")
    if observation_value["status"] != "succeeded":
        raise EngineProtocolError("Enterprise source execution observation did not succeed")
    if tuple(cast(Sequence[Any], observation_value["source_lineage"])) != tuple(
        cast(Sequence[Any], invocation_value["source_refs"])
    ):
        raise EngineProtocolError("Enterprise source execution lineage does not bind invocation")
    if observation_value["output_ref"] != view["output_ref"] or observation_value["output_digest"] != view["output_digest"]:
        raise EngineProtocolError("Enterprise source execution view does not bind observation")
    source_plan_digest = sha256_digest(canonical_bytes(source_plan_raw))
    execution_plan_digest = sha256_digest(canonical_bytes(execution_plan_raw))
    task_digest = sha256_digest(canonical_bytes(task))
    _validate_lineage(invocation_value, source_plan_digest, execution_plan_digest, task_digest)
    receipt = _parse_receipt(execution_raw["canonical_receipt"])
    normalized_invocation = cast(Mapping[str, object], _plain(invocation_value))
    normalized_observation = cast(Mapping[str, object], _plain(observation_value))
    return {
        "schema_version": _SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "governance_revision": revision,
        "execution": {
            "schema_version": _SCHEMA_VERSION,
            "transport_version": _TRANSPORT_VERSION,
            "engine_interface_version": ENGINE_INTERFACE_VERSION,
            "source_plan": source_plan,
            "execution_plan": execution_plan,
            "view": dict(view),
            "invocation": dict(normalized_invocation),
            "observation": dict(normalized_observation),
            "canonical_receipt": receipt,
        },
    }


__all__ = ["parse_source_execution_response", "validate_execution_request"]
