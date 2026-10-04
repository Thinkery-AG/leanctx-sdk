# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Strict SDK validation for the governed Engine provider-execution response.

This adapter validates wire joins and provider-output integrity only. It does
not authorize a tenant, select a provider, verify a signer, charge an account,
publish a receipt, or admit an outcome; those remain host authorities.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping, NoReturn, cast

from .errors import EngineProtocolError, ValidationError
from .planning import (
    ENGINE_INTERFACE_VERSION,
    MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES,
    EnginePlanningRequest,
)
from .protocol import (
    MAX_REF_BYTES,
    sha256_digest,
    strict_json_loads,
    validate_digest,
    validate_ref,
)
from .source_execution import (
    _exact_version,
    _integer,
    _mapping,
    _validate_plan,
    _validate_task,
)


_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_MAX_PROVIDER_REQUEST_BYTES = MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES
_MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024
_MAX_PROVIDER_OUTPUT_BYTES = 256 * 1024
_MAX_PROVIDER_OUTPUT_TOKENS = 65_536
_PROVIDER_STATUSES = {
    "succeeded",
    "failed",
    "rejected",
    "timed_out",
    "dispatch_uncertain",
}
_FAILURE_CODES = {
    "policy_rejected",
    "source_unavailable",
    "source_integrity_mismatch",
    "resource_limit",
    "unsupported_operation",
    "internal",
}
_USAGE_FIELDS = {
    "state",
    "uncached_input_tokens",
    "cache_write_input_tokens",
    "cache_read_input_tokens",
    "total_input_tokens",
    "output_tokens",
}
_COST_BASIS = {"unavailable", "usage_priced_estimate", "observed_charge"}


def _fail(message: str, *, protocol: bool, cause: BaseException | None = None) -> NoReturn:
    error = EngineProtocolError(message) if protocol else ValidationError(message)
    if cause is None:
        raise error
    raise error from cause


def _bounded_text(
    value: Any,
    field: str,
    *,
    protocol: bool,
    maximum: int = MAX_REF_BYTES,
    nonempty: bool = True,
    controls: bool = True,
) -> str:
    if not isinstance(value, str):
        _fail(f"{field} must be a string", protocol=protocol)
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        _fail(f"{field} is not valid UTF-8", protocol=protocol, cause=exc)
    if (nonempty and not encoded) or len(encoded) > maximum:
        _fail(f"{field} exceeds its byte bound", protocol=protocol)
    if controls and any(unicodedata.category(character) == "Cc" for character in value):
        _fail(f"{field} contains a control character", protocol=protocol)
    return value


def validate_provider_request(
    task: Any,
    plan: Any,
    request: EnginePlanningRequest,
    tenant_id: str,
    max_output_tokens: Any,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Validate caller input without making it an authorization or dispatch claim."""
    if not isinstance(request, EnginePlanningRequest):
        raise ValidationError("provider_execute requires EnginePlanningRequest")
    normalized_task = _validate_task(task, request, tenant_id, protocol=False)
    normalized_plan = _validate_plan(
        plan,
        cast(str, normalized_task["task_id"]),
        protocol=False,
        allow_context_plan_id=True,
    )
    context_plan_id = normalized_plan.get("context_plan_id")
    if not isinstance(context_plan_id, str) or not context_plan_id:
        raise ValidationError("provider_execute requires a concrete context_plan_id")
    _integer(
        normalized_plan["context_budget_tokens"],
        "plan.context_budget_tokens",
        maximum=request.budget_tokens,
        protocol=False,
    )
    if normalized_plan["context_budget_tokens"] < 1:
        raise ValidationError("provider_execute requires a bounded positive context budget")
    provider = cast(str, normalized_plan["provider"])
    model = cast(str, normalized_plan["model"])
    if provider in {"local-native", "auto"} or model in {"local-native", "auto"}:
        raise ValidationError("provider_execute requires a concrete non-local provider plan")
    if normalized_plan["max_retries"] != 0 or normalized_plan["fallback_refs"]:
        raise ValidationError("provider_execute does not permit host retries or fallbacks")
    _bounded_text(provider, "plan.provider", protocol=False)
    _bounded_text(model, "plan.model", protocol=False)
    output_tokens = _integer(
        max_output_tokens,
        "max_output_tokens",
        maximum=_MAX_PROVIDER_OUTPUT_TOKENS,
        protocol=False,
    )
    if output_tokens < 1:
        raise ValidationError("max_output_tokens must be positive")
    return normalized_task, normalized_plan, output_tokens


def _parse_usage(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "provider usage", protocol=True)
    if set(raw) != _USAGE_FIELDS:
        raise EngineProtocolError("provider usage fields do not match the v1 contract")
    state = raw["state"]
    if not isinstance(state, str) or state not in {"measured", "estimated", "unavailable"}:
        raise EngineProtocolError("provider usage state is unsupported")
    counters: dict[str, int | None] = {}
    for field in sorted(_USAGE_FIELDS - {"state"}):
        counter = raw[field]
        if counter is not None:
            counter = _integer(
                counter,
                f"provider usage.{field}",
                maximum=_MAX_SAFE_INTEGER,
                protocol=True,
            )
        counters[field] = counter
    if state == "unavailable":
        if any(counter is not None for counter in counters.values()):
            raise EngineProtocolError("unavailable provider usage must omit every counter")
    else:
        if any(counter is None for counter in counters.values()):
            raise EngineProtocolError(
                "measured or estimated provider usage requires every counter"
            )
        expected = (
            cast(int, counters["uncached_input_tokens"])
            + cast(int, counters["cache_write_input_tokens"])
            + cast(int, counters["cache_read_input_tokens"])
        )
        if counters["total_input_tokens"] != expected:
            raise EngineProtocolError("provider usage total_input_tokens does not match components")
    return dict(raw)


def _parse_cost(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "provider cost", protocol=True)
    basis = raw.get("basis")
    if basis == "unavailable":
        if set(raw) != {"basis"}:
            raise EngineProtocolError("unavailable provider cost has unexpected fields")
        return dict(raw)
    if not isinstance(basis, str) or basis not in _COST_BASIS - {"unavailable"}:
        raise EngineProtocolError("provider cost basis is unsupported")
    if set(raw) != {"basis", "micros"}:
        raise EngineProtocolError("provider cost fields do not match the v1 contract")
    micros = _integer(
        raw["micros"], "provider cost.micros", maximum=_MAX_SAFE_INTEGER, protocol=True
    )
    return {"basis": basis, "micros": micros}


def _parse_failure(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "provider failure", protocol=True)
    allowed = {"code", "retryable_by_host", "recovery_ref"}
    required = {"code", "retryable_by_host"}
    if not required.issubset(raw) or set(raw) - allowed:
        raise EngineProtocolError("provider failure fields do not match the v1 contract")
    code = raw["code"]
    if not isinstance(code, str) or code not in _FAILURE_CODES:
        raise EngineProtocolError("provider failure code is unsupported")
    retryable = raw["retryable_by_host"]
    if not isinstance(retryable, bool):
        raise EngineProtocolError("provider failure retryable_by_host must be boolean")
    recovery = raw.get("recovery_ref")
    if recovery is not None:
        try:
            recovery = validate_ref(recovery, "provider failure recovery_ref")
        except ValidationError as exc:
            raise EngineProtocolError("provider failure recovery_ref is invalid") from exc
    if retryable:
        raise EngineProtocolError("provider failures must not request host retry")
    if code == "policy_rejected" and recovery is not None:
        raise EngineProtocolError("policy rejection cannot request recovery")
    if code in {"source_unavailable", "source_integrity_mismatch"} and recovery is None:
        raise EngineProtocolError("source failure requires a recovery_ref")
    normalized = dict(raw)
    if recovery is not None:
        normalized["recovery_ref"] = recovery
    return normalized


def _parse_output(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "provider output", protocol=True)
    if set(raw) != {"content", "sha256_digest"}:
        raise EngineProtocolError("provider output fields do not match the v1 contract")
    content = _bounded_text(
        raw["content"],
        "provider output.content",
        protocol=True,
        maximum=_MAX_PROVIDER_OUTPUT_BYTES,
        nonempty=False,
        controls=False,
    )
    try:
        digest = validate_digest(raw["sha256_digest"], "provider output.sha256_digest")
    except ValidationError as exc:
        raise EngineProtocolError("provider output digest is invalid") from exc
    if digest != sha256_digest(content.encode("utf-8")):
        raise EngineProtocolError("provider output digest does not match content")
    return {"content": content, "sha256_digest": digest}


def parse_provider_execution_response(
    raw: bytes,
    task: Mapping[str, Any],
    plan: Mapping[str, Any],
    tenant_id: str,
) -> Mapping[str, object]:
    """Validate a direct EngineProviderExecutionResponseV1 projection.

    The SDK cannot recompute request_digest or context_digest because the host
    owns materialized bytes and the assigned attempt ID; both are syntax-checked
    only and never treated as proof.
    """
    if len(raw) > _MAX_PROVIDER_RESPONSE_BYTES:
        raise EngineProtocolError("provider execution response exceeds its byte bound")
    try:
        value = strict_json_loads(raw, label="Enterprise Engine provider execution response")
    except ValidationError as exc:
        raise EngineProtocolError(
            "Enterprise Engine provider execution response is not valid JSON"
        ) from exc
    if not isinstance(value, Mapping):
        raise EngineProtocolError("provider execution response must be an object")
    required = {
        "schema_version",
        "transport_version",
        "engine_interface_version",
        "attempt_id",
        "task_id",
        "plan_id",
        "context_digest",
        "request_digest",
        "provider",
        "model",
        "status",
        "acceptance",
        "usage",
        "cost",
    }
    allowed = required | {"output", "failure"}
    if set(value) - allowed or not required.issubset(value):
        raise EngineProtocolError("provider execution response fields do not match the v1 contract")
    _exact_version(value["schema_version"], "response.schema_version", protocol=True)
    _exact_version(value["transport_version"], "response.transport_version", protocol=True)
    if value["engine_interface_version"] != ENGINE_INTERFACE_VERSION:
        raise EngineProtocolError("provider execution Engine interface is unsupported")
    if task.get("tenant_id") != tenant_id:
        raise EngineProtocolError("provider execution task tenant does not bind the client")
    attempt_id = _bounded_text(value["attempt_id"], "response.attempt_id", protocol=True)
    task_id = _bounded_text(value["task_id"], "response.task_id", protocol=True)
    plan_id = _bounded_text(value["plan_id"], "response.plan_id", protocol=True)
    if task_id != task["task_id"] or plan_id != plan["plan_id"]:
        raise EngineProtocolError("provider execution response task or plan does not bind request")
    try:
        context_digest = validate_digest(value["context_digest"], "response.context_digest")
        request_digest = validate_digest(value["request_digest"], "response.request_digest")
    except ValidationError as exc:
        raise EngineProtocolError("provider execution response digest is invalid") from exc
    provider = _bounded_text(value["provider"], "response.provider", protocol=True)
    model = _bounded_text(value["model"], "response.model", protocol=True)
    if provider != plan["provider"] or model != plan["model"]:
        raise EngineProtocolError("provider execution response provider or model does not bind plan")
    status = value["status"]
    if not isinstance(status, str) or status not in _PROVIDER_STATUSES:
        raise EngineProtocolError("provider execution response status is unsupported")
    if value["acceptance"] != "unknown":
        raise EngineProtocolError("provider execution response acceptance must remain unknown")
    usage = _parse_usage(value["usage"])
    cost = _parse_cost(value["cost"])
    if cost["basis"] == "usage_priced_estimate" and usage["state"] == "unavailable":
        raise EngineProtocolError("provider cost cannot price unavailable usage")
    output = None
    if "output" in value and value["output"] is not None:
        output = _parse_output(value["output"])
    failure = None
    if "failure" in value and value["failure"] is not None:
        failure = _parse_failure(value["failure"])
    if status == "succeeded":
        if output is None or failure is not None:
            raise EngineProtocolError("succeeded provider response requires output only")
    elif output is not None or failure is None:
        raise EngineProtocolError("non-success provider response requires failure only")
    normalized = dict(value)
    normalized["attempt_id"] = attempt_id
    normalized["task_id"] = task_id
    normalized["plan_id"] = plan_id
    normalized["context_digest"] = context_digest
    normalized["request_digest"] = request_digest
    normalized["provider"] = provider
    normalized["model"] = model
    normalized["usage"] = usage
    normalized["cost"] = cost
    if output is not None:
        normalized["output"] = output
    if failure is not None:
        normalized["failure"] = failure
    return cast(Mapping[str, object], normalized)


__all__ = ["parse_provider_execution_response", "validate_provider_request"]
