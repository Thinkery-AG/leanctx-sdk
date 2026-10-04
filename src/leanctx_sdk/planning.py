# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Strict DTOs and response validators for the canonical Engine planners.

This module owns wire-shape validation only.  It does not plan, execute, admit,
authenticate, or manufacture an execution receipt.
"""

from __future__ import annotations

import datetime as _datetime
import math
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, NoReturn, Optional, Sequence, cast

from .errors import ValidationError
from .protocol import (
    MAX_RESPONSE_BYTES,
    canonical_bytes,
    sha256_digest,
    strict_json_loads,
    validate_digest,
)


SCHEMA_VERSION = 1
TRANSPORT_VERSION = 1
ENGINE_INTERFACE_VERSION = "1.0.0"

MAX_ENGINE_CONTEXT_PLAN_REQUEST_BYTES = 64 * 1024
MAX_ENGINE_CONTEXT_PLAN_QUERY_BYTES = 16 * 1024
MAX_ENGINE_CONTEXT_PLAN_TOKENS = 1_048_576
MAX_ENGINE_CONTEXT_PLAN_CANDIDATES = 256
MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES = 1024 * 1024
MAX_ENGINE_SOURCE_PLAN_SOURCES = 64
MAX_ENGINE_SOURCE_CONTENT_BYTES = 64 * 1024

MAX_PROTOCOL_ITEMS = 256
MAX_IDENTIFIER_BYTES = 256
MAX_REFERENCE_BYTES = 1024
MAX_U64 = (1 << 64) - 1
MAX_EXTENSION_VALUE_BYTES = 64 * 1024
MAX_EXTENSION_DEPTH = 8

_SOURCE_TYPES = {
    "filesystem",
    "issue_tracker",
    "relational_database",
    "other",
}
_SOURCE_PERMISSIONS = {"permitted", "denied", "unknown"}
_CLASSIFICATIONS = {"Public", "Internal", "Confidential", "Restricted"}
_DISPOSITIONS = {"selected", "excluded", "deferred"}
_REASON_CODES = {
    "relevant",
    "required",
    "cache_hit",
    "budget_exceeded",
    "lower_utility",
    "policy_excluded",
    "deferred_for_later",
    "other",
}
_EVIDENCE_KINDS = {
    "ProviderReceipt",
    "RuntimeLog",
    "SignedBatch",
    "QualityMeasurement",
    "ExperimentOutcome",
}
_SIGNATURE_STATUSES = {"Verified", "Unverified", "NotSigned"}
_TIMESTAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$")

_PLAN_KEYS = {
    "schema_version",
    "context_plan_id",
    "task_id",
    "projection_digest",
    "budget_tokens",
    "selections",
    "provider_stats",
    "policy_decision_refs",
    "evidence",
}
_SELECTION_KEYS = {
    "source_ref",
    "provider",
    "disposition",
    "token_count",
    "sha256_digest",
    "reason_codes",
    "reason_detail",
}
_PROVIDER_STATS_KEYS = {
    "candidates_offered",
    "candidates_selected",
    "tokens_used",
}
_EVIDENCE_KEYS = {
    "schema_version",
    "kind",
    "uri",
    "digest",
    "signature_status",
    "media_type",
}
_DESCRIPTOR_KEYS = {
    "object_ref",
    "source_id",
    "source_type",
    "content_digest",
    "revision",
    "owner",
    "observed_at",
    "valid_until",
    "classification",
    "permission",
}


def _error(message: str) -> NoReturn:
    raise ValidationError(message)


def _is_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _text(
    value: Any,
    field_name: str,
    maximum: int,
    *,
    controls: bool = True,
    reject_nul: bool = True,
    nonblank: bool = True,
) -> str:
    if not isinstance(value, str):
        _error(f"{field_name} must be a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ValidationError(f"{field_name} is not valid UTF-8") from exc
    if not encoded:
        _error(f"{field_name} must not be empty")
    if len(encoded) > maximum:
        _error(f"{field_name} exceeds {maximum} UTF-8 bytes")
    if reject_nul and "\x00" in value:
        _error(f"{field_name} contains NUL")
    if controls and _is_control(value):
        _error(f"{field_name} contains a control character")
    if nonblank and not value.strip():
        _error(f"{field_name} must not be blank")
    return value


def _integer(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _error(f"{field_name} must be an integer")
    if not minimum <= value <= maximum:
        _error(f"{field_name} is outside its protocol bounds")
    return value


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _error(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in value):
        _error(f"{field_name} has a non-string field")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field_name: str) -> None:
    if set(value) != expected:
        _error(f"{field_name} fields do not match the v1 contract")


def _bounded_identifier(value: Any, field_name: str) -> str:
    return _text(value, field_name, MAX_IDENTIFIER_BYTES)


def _bounded_reference(value: Any, field_name: str) -> str:
    return _text(value, field_name, MAX_REFERENCE_BYTES)


def _optional_reference(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _bounded_reference(value, field_name)


def _enum(value: Any, field_name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _error(f"{field_name} has an unsupported value")
    return value


def _timestamp(value: Any, field_name: str) -> str:
    value = _text(value, field_name, MAX_IDENTIFIER_BYTES)
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        _error(f"{field_name} must use canonical UTC timestamp syntax")
    year, month, day, hour, minute, second = (
        int(component) for component in match.groups()
    )
    try:
        _datetime.datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise ValidationError(f"{field_name} contains an invalid date or time") from exc
    return value


def _optional_timestamp(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _timestamp(value, field_name)


def _projection_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        _error(f"{field_name} must be a string")
    candidate = value.removeprefix("sha256:")
    if len(candidate) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in candidate
    ):
        _error(f"{field_name} must contain a 64-digit hexadecimal digest")
    return value


def _extension_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_EXTENSION_DEPTH:
        _error("extension value exceeds its nesting bound")
    if isinstance(value, Mapping):
        if len(value) > MAX_PROTOCOL_ITEMS:
            _error("extension object exceeds its item bound")
        for key, nested in value.items():
            _bounded_identifier(key, "extension object key")
            _extension_value(nested, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_PROTOCOL_ITEMS:
            _error("extension array exceeds its item bound")
        for nested in value:
            _extension_value(nested, depth + 1)
    elif (
        isinstance(value, str)
        and len(value.encode("utf-8")) > MAX_EXTENSION_VALUE_BYTES
    ):
        _error("extension string exceeds its byte bound")
    elif isinstance(value, float) and not math.isfinite(value):
        _error("extension number is not finite")
    try:
        encoded = canonical_bytes(value)
    except ValidationError:
        raise
    if len(encoded) > MAX_EXTENSION_VALUE_BYTES:
        _error("extension value exceeds its serialized byte bound")


def _extensions(value: Mapping[str, Any], reserved: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, nested in value.items():
        if key in reserved:
            continue
        _bounded_identifier(key, "extension key")
        _extension_value(nested)
        result[key] = nested
    if len(result) > MAX_PROTOCOL_ITEMS:
        _error("extensions exceed their field bound")
    return result


def _descriptor(value: Any, field_name: str = "descriptor") -> dict[str, Any]:
    raw = _mapping(value, field_name)
    if set(raw) - _DESCRIPTOR_KEYS:
        _error(f"{field_name} fields do not match the v1 contract")
    required = {"object_ref", "source_id", "source_type", "content_digest"}
    if not required.issubset(raw):
        _error(f"{field_name} is missing a required field")
    result = {
        "object_ref": _bounded_reference(raw["object_ref"], f"{field_name}.object_ref"),
        "source_id": _bounded_identifier(raw["source_id"], f"{field_name}.source_id"),
        "source_type": _enum(
            raw["source_type"], f"{field_name}.source_type", _SOURCE_TYPES
        ),
        "content_digest": validate_digest(
            raw["content_digest"], f"{field_name}.content_digest"
        ),
        "revision": _optional_reference(raw.get("revision"), f"{field_name}.revision"),
        "owner": _optional_reference(raw.get("owner"), f"{field_name}.owner"),
        "observed_at": _optional_timestamp(
            raw.get("observed_at"), f"{field_name}.observed_at"
        ),
        "valid_until": _optional_timestamp(
            raw.get("valid_until"), f"{field_name}.valid_until"
        ),
        "classification": None,
        "permission": _enum(
            raw.get("permission", "unknown"),
            f"{field_name}.permission",
            _SOURCE_PERMISSIONS,
        ),
    }
    classification = raw.get("classification")
    if classification is not None:
        result["classification"] = _enum(
            classification, f"{field_name}.classification", _CLASSIFICATIONS
        )
    if (
        result["observed_at"] is not None
        and result["valid_until"] is not None
        and result["valid_until"] <= result["observed_at"]
    ):
        _error(f"{field_name} validity window is inverted")
    return result


@dataclass(frozen=True)
class EnginePlanningRequest:
    """Canonical Engine context-plan request, without execution evidence."""

    task_id: str
    query: str
    budget_tokens: int
    max_candidates: int = 64

    def __post_init__(self) -> None:
        _bounded_identifier(self.task_id, "task_id")
        _text(
            self.query,
            "query",
            MAX_ENGINE_CONTEXT_PLAN_QUERY_BYTES,
            controls=False,
        )
        _integer(self.budget_tokens, "budget_tokens", 1, MAX_ENGINE_CONTEXT_PLAN_TOKENS)
        _integer(
            self.max_candidates, "max_candidates", 1, MAX_ENGINE_CONTEXT_PLAN_CANDIDATES
        )
        if not self.query.strip():
            _error("query must not be blank")

    def to_dict(self) -> Mapping[str, object]:
        result = {
            "schema_version": SCHEMA_VERSION,
            "transport_version": TRANSPORT_VERSION,
            "engine_interface_version": ENGINE_INTERFACE_VERSION,
            "task_id": self.task_id,
            "query": self.query,
            "budget_tokens": self.budget_tokens,
            "max_candidates": self.max_candidates,
        }
        if len(canonical_bytes(result)) > MAX_ENGINE_CONTEXT_PLAN_REQUEST_BYTES:
            _error("Engine context-plan request exceeds its byte bound")
        return result


@dataclass(frozen=True)
class EngineSource:
    """Explicit source body plus its factual Engine binding descriptor."""

    descriptor: Mapping[str, object]
    content: str

    def __post_init__(self) -> None:
        normalized = _descriptor(self.descriptor)
        encoded = _text(
            self.content,
            "content",
            MAX_ENGINE_SOURCE_CONTENT_BYTES,
            controls=False,
            reject_nul=False,
        ).encode("utf-8")
        if not self.content.strip():
            _error("content must not be blank")
        if normalized["content_digest"] != sha256_digest(encoded):
            _error("descriptor.content_digest does not match content")
        object.__setattr__(self, "descriptor", MappingProxyType(normalized))

    def to_dict(self) -> Mapping[str, object]:
        return {"descriptor": dict(self.descriptor), "content": self.content}


def _decode(raw: bytes, label: str) -> Mapping[str, Any]:
    if not isinstance(raw, (bytes, bytearray)):
        _error(f"{label} must be bytes")
    if len(raw) > MAX_RESPONSE_BYTES:
        _error(f"{label} exceeds the response byte bound")
    return strict_json_loads(bytes(raw), label=label)


def _header(value: Mapping[str, Any], label: str) -> None:
    _integer(
        value.get("schema_version"),
        f"{label}.schema_version",
        SCHEMA_VERSION,
        SCHEMA_VERSION,
    )
    _integer(
        value.get("transport_version"),
        f"{label}.transport_version",
        TRANSPORT_VERSION,
        TRANSPORT_VERSION,
    )
    if value.get("engine_interface_version") != ENGINE_INTERFACE_VERSION:
        _error(f"{label}.engine_interface_version is unsupported")


def _evidence(value: Any, field_name: str) -> dict[str, Any]:
    raw = _mapping(value, field_name)
    if set(raw) - _EVIDENCE_KEYS:
        extensions = _extensions(raw, _EVIDENCE_KEYS)
    else:
        extensions = {}
    required = {"kind", "uri", "digest", "signature_status"}
    if not required.issubset(raw):
        _error(f"{field_name} is missing a required field")
    result: dict[str, Any] = {}
    if "schema_version" in raw and raw["schema_version"] is not None:
        _integer(raw["schema_version"], f"{field_name}.schema_version", 1, 1)
        result["schema_version"] = 1
    result["kind"] = _enum(raw["kind"], f"{field_name}.kind", _EVIDENCE_KINDS)
    result["uri"] = _bounded_identifier(raw["uri"], f"{field_name}.uri")
    result["digest"] = _bounded_identifier(raw["digest"], f"{field_name}.digest")
    if "schema_version" in result:
        digest = result["digest"].removeprefix("sha256:").removeprefix("blake3:")
        if len(digest) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in digest
        ):
            _error(f"{field_name}.digest is not a supported versioned digest")
    result["signature_status"] = _enum(
        raw["signature_status"],
        f"{field_name}.signature_status",
        _SIGNATURE_STATUSES,
    )
    media_type = raw.get("media_type")
    if media_type is not None:
        result["media_type"] = _bounded_identifier(
            media_type, f"{field_name}.media_type"
        )
    result.update(extensions)
    return result


def _selection(value: Any, field_name: str) -> dict[str, Any]:
    raw = _mapping(value, field_name)
    _exact_keys(
        raw,
        _SELECTION_KEYS - {"sha256_digest", "reason_detail"}
        | (set(raw) & {"sha256_digest", "reason_detail"}),
        field_name,
    )
    required = _SELECTION_KEYS - {"sha256_digest", "reason_detail"}
    if not required.issubset(raw):
        _error(f"{field_name} is missing a required field")
    reasons = raw["reason_codes"]
    if (
        not isinstance(reasons, list)
        or not reasons
        or len(reasons) > MAX_PROTOCOL_ITEMS
    ):
        _error(f"{field_name}.reason_codes has an invalid shape")
    normalized_reasons = [
        _enum(reason, f"{field_name}.reason_codes", _REASON_CODES) for reason in reasons
    ]
    if len(set(normalized_reasons)) != len(normalized_reasons):
        _error(f"{field_name}.reason_codes contains duplicates")
    result: dict[str, Any] = {
        "source_ref": _bounded_identifier(
            raw["source_ref"], f"{field_name}.source_ref"
        ),
        "provider": _bounded_identifier(raw["provider"], f"{field_name}.provider"),
        "disposition": _enum(
            raw["disposition"], f"{field_name}.disposition", _DISPOSITIONS
        ),
        "token_count": _integer(
            raw["token_count"], f"{field_name}.token_count", 0, MAX_U64
        ),
        "reason_codes": normalized_reasons,
    }
    if raw.get("sha256_digest") is not None:
        result["sha256_digest"] = _projection_digest(
            raw["sha256_digest"], f"{field_name}.sha256_digest"
        )
    if raw.get("reason_detail") is not None:
        result["reason_detail"] = _bounded_identifier(
            raw["reason_detail"], f"{field_name}.reason_detail"
        )
    return result


def _plan(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "plan")
    required = {
        "schema_version",
        "context_plan_id",
        "task_id",
        "budget_tokens",
        "selections",
    }
    if not required.issubset(raw):
        _error("plan is missing a required field")
    extensions = _extensions(raw, _PLAN_KEYS)
    _integer(
        raw["schema_version"], "plan.schema_version", SCHEMA_VERSION, SCHEMA_VERSION
    )
    selections = raw["selections"]
    if not isinstance(selections, list) or len(selections) > MAX_PROTOCOL_ITEMS:
        _error("plan.selections has an invalid shape")
    normalized_selections = [
        _selection(item, f"plan.selections[{index}]")
        for index, item in enumerate(selections)
    ]
    refs = [item["source_ref"] for item in normalized_selections]
    if len(set(refs)) != len(refs):
        _error("plan.selections contains duplicate source_ref values")
    budget = _integer(raw["budget_tokens"], "plan.budget_tokens", 0, MAX_U64)
    selected_tokens = sum(
        item["token_count"]
        for item in normalized_selections
        if item["disposition"] == "selected"
    )
    if selected_tokens > budget:
        _error("plan selected context exceeds budget_tokens")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "context_plan_id": _bounded_identifier(
            raw["context_plan_id"], "plan.context_plan_id"
        ),
        "task_id": _bounded_identifier(raw["task_id"], "plan.task_id"),
        "budget_tokens": budget,
        "selections": normalized_selections,
    }
    projection_digest = raw.get("projection_digest")
    if projection_digest is not None:
        result["projection_digest"] = validate_digest(
            projection_digest, "plan.projection_digest"
        )
    provider_stats = raw.get("provider_stats")
    if "provider_stats" in raw:
        provider_stats = _mapping(provider_stats, "plan.provider_stats")
        if len(provider_stats) > MAX_PROTOCOL_ITEMS:
            _error("plan.provider_stats exceeds its item bound")
        normalized_stats: dict[str, Any] = {}
        for provider, stats in provider_stats.items():
            provider_name = _bounded_identifier(provider, "plan.provider_stats key")
            stats_map = _mapping(stats, f"plan.provider_stats[{provider_name!r}]")
            _exact_keys(stats_map, _PROVIDER_STATS_KEYS, "plan.provider_stats entry")
            offered = _integer(
                stats_map["candidates_offered"],
                "plan.provider_stats.candidates_offered",
                0,
                MAX_U64,
            )
            selected = _integer(
                stats_map["candidates_selected"],
                "plan.provider_stats.candidates_selected",
                0,
                MAX_U64,
            )
            if selected > offered:
                _error("plan.provider_stats selected exceeds offered")
            normalized_stats[provider_name] = {
                "candidates_offered": offered,
                "candidates_selected": selected,
                "tokens_used": _integer(
                    stats_map["tokens_used"],
                    "plan.provider_stats.tokens_used",
                    0,
                    MAX_U64,
                ),
            }
        if normalized_stats:
            result["provider_stats"] = normalized_stats
    policy_refs = raw.get("policy_decision_refs")
    if "policy_decision_refs" in raw:
        if not isinstance(policy_refs, list) or len(policy_refs) > MAX_PROTOCOL_ITEMS:
            _error("plan.policy_decision_refs has an invalid shape")
        normalized_refs = [
            _bounded_identifier(ref, "plan.policy_decision_refs") for ref in policy_refs
        ]
        if len(set(normalized_refs)) != len(normalized_refs):
            _error("plan.policy_decision_refs contains duplicates")
        if normalized_refs:
            result["policy_decision_refs"] = normalized_refs
    evidence = raw.get("evidence")
    if "evidence" in raw:
        if not isinstance(evidence, list) or len(evidence) > MAX_PROTOCOL_ITEMS:
            _error("plan.evidence has an invalid shape")
        normalized_evidence = [
            _evidence(item, f"plan.evidence[{index}]")
            for index, item in enumerate(evidence)
        ]
        if normalized_evidence:
            result["evidence"] = normalized_evidence
    result.update(extensions)
    if "projection_digest" in result:
        unsigned = dict(result)
        unsigned.pop("projection_digest")
        expected = sha256_digest(canonical_bytes(unsigned))
        if result["projection_digest"] != expected:
            _error("plan.projection_digest does not match canonical projection content")
    return result


def _response(raw: bytes, request: EnginePlanningRequest) -> dict[str, Any]:
    value = _decode(raw, "Engine context-plan response")
    _exact_keys(
        value,
        {"schema_version", "transport_version", "engine_interface_version", "plan"},
        "Engine context-plan response",
    )
    _header(value, "Engine context-plan response")
    plan = _plan(value["plan"])
    if plan["task_id"] != request.task_id:
        _error("Engine context-plan response task_id does not bind the request")
    if plan["budget_tokens"] > request.budget_tokens:
        _error("Engine context-plan response budget exceeds the request")
    return {
        "schema_version": SCHEMA_VERSION,
        "transport_version": TRANSPORT_VERSION,
        "engine_interface_version": ENGINE_INTERFACE_VERSION,
        "plan": plan,
    }


def parse_context_plan(
    raw: bytes, request: EnginePlanningRequest
) -> Mapping[str, object]:
    """Validate and detach a canonical plan; no execution proof is inferred."""

    if not isinstance(request, EnginePlanningRequest):
        _error("request must be EnginePlanningRequest")
    request.to_dict()
    return _response(raw, request)


def parse_source_plan(
    raw: bytes,
    request: EnginePlanningRequest,
    sources: Optional[Sequence[EngineSource]] = None,
) -> Mapping[str, object]:
    """Validate a source-plan envelope and its digest-bound selected bindings.

    ``sources=None`` intentionally validates only response self-consistency;
    an authenticated caller must additionally constrain object references.
    """

    if not isinstance(request, EnginePlanningRequest):
        _error("request must be EnginePlanningRequest")
    request.to_dict()
    value = _decode(raw, "Engine source-plan response")
    _exact_keys(
        value,
        {"result", "source_bindings", "binding_digest"},
        "Engine source-plan response",
    )
    result = _response(canonical_bytes(value["result"]), request)
    if result["plan"].get("projection_digest") is None:
        _error("Engine source-plan response requires projection_digest")
    bindings_raw = value["source_bindings"]
    if not isinstance(bindings_raw, list) or len(bindings_raw) > MAX_PROTOCOL_ITEMS:
        _error("source_bindings has an invalid shape")
    bindings = [
        _descriptor(item, f"source_bindings[{index}]")
        for index, item in enumerate(bindings_raw)
    ]
    if any(
        left["object_ref"] >= right["object_ref"]
        for left, right in zip(bindings, bindings[1:])
    ):
        _error("source_bindings must be strictly sorted by object_ref")
    selected = [
        item
        for item in result["plan"]["selections"]
        if item["disposition"] == "selected"
    ]
    if len(selected) != len(bindings):
        _error("source_bindings do not match selected plan entries")
    for binding in bindings:
        if not any(
            item["source_ref"] == binding["object_ref"]
            and item["provider"] == binding["source_id"]
            and item.get("sha256_digest") == binding["content_digest"]
            for item in selected
        ):
            _error("source binding does not match a selected plan entry")
    supplied_digest = validate_digest(value["binding_digest"], "binding_digest")
    expected_digest = sha256_digest(canonical_bytes([result, bindings]))
    if supplied_digest != expected_digest:
        _error("binding_digest does not match canonical source bindings")
    if sources is not None:
        if (
            isinstance(sources, (str, bytes))
            or len(sources) > MAX_ENGINE_SOURCE_PLAN_SOURCES
        ):
            _error("sources has an invalid shape")
        requested: dict[str, Mapping[str, object]] = {}
        for source in sources:
            if not isinstance(source, EngineSource):
                _error("sources must contain EngineSource values")
            object_ref = cast(str, source.descriptor["object_ref"])
            if object_ref in requested:
                _error("sources contains duplicate object_ref values")
            requested[object_ref] = source.descriptor
        for binding in bindings:
            expected = requested.get(binding["object_ref"])
            if expected is None or dict(expected) != binding:
                _error("source binding is not present in the requested sources")
    return {
        "result": result,
        "source_bindings": bindings,
        "binding_digest": supplied_digest,
    }


__all__ = [
    "ENGINE_INTERFACE_VERSION",
    "MAX_ENGINE_CONTEXT_PLAN_CANDIDATES",
    "MAX_ENGINE_CONTEXT_PLAN_QUERY_BYTES",
    "MAX_ENGINE_CONTEXT_PLAN_REQUEST_BYTES",
    "MAX_ENGINE_CONTEXT_PLAN_TOKENS",
    "MAX_ENGINE_SOURCE_CONTENT_BYTES",
    "MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES",
    "MAX_ENGINE_SOURCE_PLAN_SOURCES",
    "EnginePlanningRequest",
    "EngineSource",
    "parse_context_plan",
    "parse_source_plan",
]
