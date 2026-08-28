"""Clean-room Product values and strict Engine Interface v1 records.

This module deliberately contains no subprocess or host integration logic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple, Union

from .errors import EngineExecutionError, ValidationError


SCHEMA_VERSION = 1
TRANSPORT_VERSION = 1
ENGINE_INTERFACE_VERSION = "1.0.0"

MAX_REQUEST_BYTES = 64 * 1024
MAX_PATH_BYTES = 4096
MAX_REF_BYTES = 512
MAX_TASK_BYTES = 16 * 1024
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_REFS = 32
MAX_MEASUREMENTS = 32

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OUTPUT_REF_RE = re.compile(r"^output:[0-9a-f]{64}$")
_PLAN_REF_RE = re.compile(r"^plan:sha256:[0-9a-f]{64}$")
_ASCII_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_PRINTABLE_ASCII_RE = re.compile(r"^[ -~]+$")


class FailureCode(str, Enum):
    POLICY_REJECTED = "policy_rejected"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_INTEGRITY_MISMATCH = "source_integrity_mismatch"
    RESOURCE_LIMIT = "resource_limit"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    INTERNAL = "internal"


class SessionState(str, Enum):
    CREATED = "created"
    PLANNED = "planned"
    EXECUTING = "executing"
    COMPLETED = "completed"
    ABORTED = "aborted"
    CLOSED = "closed"


class EngineStatus(str, Enum):
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    FAILED = "failed"


class HostOutcome(str, Enum):
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class Integrity(str, Enum):
    SEALED = "sealed"
    UNSEALED = "unsealed"


class Freshness(str, Enum):
    REUSE = "reuse"
    REFRESH = "refresh"


def _utf8(value: str, field_name: str) -> bytes:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    try:
        return value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ValidationError(f"{field_name} is not valid UTF-8") from exc


def _text(value: str, field_name: str, maximum: int, *, controls: bool = True) -> str:
    encoded = _utf8(value, field_name)
    if not encoded:
        raise ValidationError(f"{field_name} must not be empty")
    if len(encoded) > maximum:
        raise ValidationError(f"{field_name} exceeds {maximum} UTF-8 bytes")
    if "\x00" in value:
        raise ValidationError(f"{field_name} contains NUL")
    if controls and any(ord(char) < 0x20 for char in value):
        raise ValidationError(f"{field_name} contains a control character")
    return value


def _optional_text(value: Optional[str], field_name: str, maximum: int) -> Optional[str]:
    if value is None:
        return None
    return _text(value, field_name, maximum)


def validate_ref(value: str, field_name: str = "ref") -> str:
    encoded = _utf8(value, field_name)
    if not encoded or len(encoded) > MAX_REF_BYTES:
        raise ValidationError(f"{field_name} must be 1..{MAX_REF_BYTES} bytes")
    if not _PRINTABLE_ASCII_RE.fullmatch(value):
        raise ValidationError(f"{field_name} must be printable ASCII")
    return value


def validate_digest(value: str, field_name: str = "digest") -> str:
    validate_ref(value, field_name)
    if not _DIGEST_RE.fullmatch(value):
        raise ValidationError(f"{field_name} must be sha256:<64 lowercase hex>")
    return value


def validate_output_ref(value: str, field_name: str = "output_ref") -> str:
    validate_ref(value, field_name)
    if not _OUTPUT_REF_RE.fullmatch(value):
        raise ValidationError(f"{field_name} must be output:<64 lowercase hex>")
    return value


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used for Product hashes."""

    try:
        result = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("value is not canonical JSON data") from exc
    _utf8(result, "canonical JSON")
    return result


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def strict_json_loads(data: Union[bytes, str], *, label: str = "JSON") -> Any:
    """Decode JSON while rejecting duplicate keys, NaN, and trailing data."""

    def pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"invalid numeric constant: {value}")

    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid {label}") from exc
    if not isinstance(result, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return result


def exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValidationError(f"{label} fields do not match the v1 contract")
    if any(not isinstance(key, str) for key in value):
        raise ValidationError(f"{label} has a non-string field")


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict") and value.__class__.__module__ == __name__:
        return _plain(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _contained(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


@dataclass(frozen=True)
class ContextSource:
    path: str
    project_root: Optional[str] = None
    media_type: str = "text/plain"
    source_ref: Optional[str] = None
    source_digest: Optional[str] = None

    def __post_init__(self):
        supplied_path = _text(self.path, "path", MAX_PATH_BYTES)
        root_value = self.project_root if self.project_root is not None else os.getcwd()
        root = _text(root_value, "project_root", MAX_PATH_BYTES, controls=False)
        root = os.path.abspath(os.path.normpath(root))
        if len(_utf8(root, "project_root")) > MAX_PATH_BYTES:
            raise ValidationError("project_root exceeds the path bound")
        if os.path.isabs(supplied_path):
            absolute_path = os.path.abspath(os.path.normpath(supplied_path))
            stored_path = absolute_path
        else:
            stored_path = os.path.normpath(supplied_path).replace(os.sep, "/")
            absolute_path = os.path.abspath(os.path.normpath(os.path.join(root, supplied_path)))
        if not _contained(absolute_path, root):
            raise ValidationError("source path escapes project_root")
        if len(_utf8(absolute_path, "path")) > MAX_PATH_BYTES:
            raise ValidationError("path exceeds the path bound")
        media_type = _text(self.media_type, "media_type", MAX_REF_BYTES)
        source_ref = self.source_ref
        if source_ref is not None:
            source_ref = validate_ref(source_ref, "source_ref")
        source_digest = self.source_digest
        if source_digest is not None:
            source_digest = validate_digest(source_digest, "source_digest")
        object.__setattr__(self, "path", stored_path)
        object.__setattr__(self, "project_root", root)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "source_digest", source_digest)

    @property
    def relative_path(self) -> str:
        root = self.project_root
        if root is None:
            raise ValidationError("source project_root is unavailable")
        absolute_path = os.path.abspath(os.path.normpath(os.path.join(root, self.path)))
        if not _contained(absolute_path, root):
            raise ValidationError("source containment cannot be proven")
        relative = os.path.relpath(absolute_path, root).replace(os.sep, "/")
        if relative in ("", ".") or relative == ".." or relative.startswith("../"):
            raise ValidationError("source path must be a rooted relative file path")
        if "\x00" in relative or any(ord(char) < 0x20 for char in relative):
            raise ValidationError("relative source path contains a control character")
        return relative

    def descriptor(self) -> Mapping[str, object]:
        result = {
            "path": self.relative_path,
            "media_type": self.media_type,
        }
        if self.source_ref is not None:
            result["source_ref"] = self.source_ref
        if self.source_digest is not None:
            result["source_digest"] = self.source_digest
        return result

    def to_dict(self) -> Mapping[str, object]:
        result = dict(self.descriptor())
        result["project_root"] = self.project_root
        return result


@dataclass(frozen=True)
class ContextPlan:
    session_id: str
    task_id: str
    task: str
    source: ContextSource
    mode: str = "aggressive"
    freshness: str = "reuse"
    plan_id: str = field(init=False)

    def __post_init__(self):
        _text(self.session_id, "session_id", MAX_REF_BYTES)
        _text(self.task_id, "task_id", MAX_REF_BYTES)
        _text(self.task, "task", MAX_TASK_BYTES, controls=False)
        if not isinstance(self.source, ContextSource):
            raise ValidationError("source must be ContextSource")
        if self.mode != "aggressive":
            raise ValidationError("mode must be aggressive in Engine Interface v1")
        if self.freshness not in (Freshness.REUSE.value, Freshness.REFRESH.value):
            raise ValidationError("freshness must be reuse or refresh")
        intent = {
            "intent_version": 1,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "task": self.task,
            "source": _plain(self.source.descriptor()),
            "mode": self.mode,
            "freshness": self.freshness,
        }
        digest = hashlib.sha256(canonical_bytes(intent)).hexdigest()
        object.__setattr__(self, "plan_id", f"plan:sha256:{digest}")

    def to_intent(self) -> Mapping[str, object]:
        return {
            "intent_version": 1,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "task": self.task,
            "source": _plain(self.source.descriptor()),
            "mode": self.mode,
            "freshness": self.freshness,
        }

    def to_dict(self) -> Mapping[str, object]:
        result = dict(_plain(self.to_intent()))
        result["plan_id"] = self.plan_id
        return result


@dataclass(frozen=True)
class ContextMeasurement:
    name: str
    unit: str
    classification: str
    value: Optional[int]

    def __post_init__(self):
        if not isinstance(self.name, str) or not _ASCII_NAME_RE.fullmatch(self.name):
            raise ValidationError("measurement name must be lowercase ASCII")
        if not isinstance(self.unit, str) or not _ASCII_NAME_RE.fullmatch(self.unit):
            raise ValidationError("measurement unit must be lowercase ASCII")
        if self.classification not in {"measured", "estimated", "unavailable"}:
            raise ValidationError("invalid measurement classification")
        if self.classification == "unavailable":
            if self.value is not None:
                raise ValidationError("unavailable measurement value must be null")
        elif isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            raise ValidationError("measurement value must be a non-negative integer")

    def to_dict(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "unit": self.unit,
            "classification": self.classification,
            "value": self.value,
        }


@dataclass(frozen=True)
class ContextFailure:
    code: Union[FailureCode, str]
    retryable_by_host: bool
    recovery_ref: Optional[str]

    def __post_init__(self):
        code = self.code.value if isinstance(self.code, FailureCode) else self.code
        if code not in {item.value for item in FailureCode}:
            raise ValidationError("invalid failure code")
        if not isinstance(self.retryable_by_host, bool):
            raise ValidationError("retryable_by_host must be boolean")
        if self.recovery_ref is not None:
            validate_ref(self.recovery_ref, "recovery_ref")
        object.__setattr__(self, "code", FailureCode(code))

    def to_dict(self) -> Mapping[str, object]:
        code = self.code
        if not isinstance(code, FailureCode):
            raise ValidationError("failure code is not normalized")
        return {
            "code": code.value,
            "retryable_by_host": self.retryable_by_host,
            "recovery_ref": self.recovery_ref,
        }


@dataclass(frozen=True)
class ContextReceiptLink:
    schema_version: int
    receipt_id: str
    receipt_ref: str
    receipt_digest: str
    invocation_id: str

    def __post_init__(self):
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValidationError("receipt link schema_version must be 1")
        validate_ref(self.receipt_id, "receipt_id")
        validate_ref(self.receipt_ref, "receipt_ref")
        validate_digest(self.receipt_digest, "receipt_digest")
        _text(self.invocation_id, "invocation_id", MAX_REF_BYTES)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "receipt_ref": self.receipt_ref,
            "receipt_digest": self.receipt_digest,
            "invocation_id": self.invocation_id,
        }


@dataclass(frozen=True)
class RecoveredSource:
    text: str
    source_ref: str
    source_digest: str
    recovery_ref: str

    def __post_init__(self):
        _text(self.text, "recovered text", MAX_TEXT_BYTES, controls=False)
        validate_ref(self.source_ref, "source_ref")
        validate_digest(self.source_digest, "source_digest")
        validate_ref(self.recovery_ref, "recovery_ref")
        if sha256_digest(self.text.encode("utf-8")) != self.source_digest:
            raise ValidationError("recovered text digest does not match source_digest")

    def to_dict(self) -> Mapping[str, object]:
        return {
            "text": self.text,
            "source_ref": self.source_ref,
            "source_digest": self.source_digest,
            "recovery_ref": self.recovery_ref,
        }


@dataclass(frozen=True)
class ContextView:
    source: ContextSource
    text: Optional[str]
    output_ref: Optional[str]
    output_digest: Optional[str]
    source_ref: str
    source_digest: str
    recovery_ref: Optional[str]
    status: str
    measurements: Tuple[ContextMeasurement, ...] = ()
    failure: Optional[ContextFailure] = None
    receipt_link: Optional[ContextReceiptLink] = None
    invocation: Mapping[str, object] = field(default_factory=dict)
    observation: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    transport_version: int = TRANSPORT_VERSION
    engine_interface_version: str = ENGINE_INTERFACE_VERSION

    def __post_init__(self):
        if not isinstance(self.source, ContextSource):
            raise ValidationError("view source must be ContextSource")
        if self.text is not None:
            _utf8(self.text, "view text")
            if len(self.text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ValidationError("view text exceeds the bound")
        if self.output_ref is not None:
            validate_output_ref(self.output_ref)
        if self.output_digest is not None:
            validate_digest(self.output_digest, "output_digest")
        if (self.output_ref is None) != (self.output_digest is None):
            raise ValidationError("output_ref and output_digest must be paired")
        if self.output_digest is not None and self.text is not None:
            actual = sha256_digest(self.text.encode("utf-8"))
            if actual != self.output_digest:
                raise ValidationError("view output digest mismatch")
            if self.output_ref != "output:" + self.output_digest.removeprefix("sha256:"):
                raise ValidationError("view output reference mismatch")
        validate_ref(self.source_ref, "source_ref")
        validate_digest(self.source_digest, "source_digest")
        if self.recovery_ref is not None:
            validate_ref(self.recovery_ref, "recovery_ref")
        if self.status not in {item.value for item in EngineStatus}:
            raise ValidationError("invalid Engine observation status")
        if len(self.measurements) > MAX_MEASUREMENTS:
            raise ValidationError("too many measurements")
        object.__setattr__(self, "measurements", tuple(self.measurements))
        if any(not isinstance(item, ContextMeasurement) for item in self.measurements):
            raise ValidationError("measurements must be ContextMeasurement values")
        if self.failure is not None and not isinstance(self.failure, ContextFailure):
            raise ValidationError("failure must be ContextFailure")
        if not isinstance(self.invocation, Mapping) or not isinstance(self.observation, Mapping):
            raise ValidationError("invocation and observation must be mappings")
        object.__setattr__(self, "invocation", _freeze(dict(self.invocation)))
        object.__setattr__(self, "observation", _freeze(dict(self.observation)))
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValidationError("view schema_version must be 1")
        if (
            not isinstance(self.transport_version, int)
            or isinstance(self.transport_version, bool)
            or self.transport_version != TRANSPORT_VERSION
        ):
            raise ValidationError("view transport_version must be integer 1")
        if self.engine_interface_version != ENGINE_INTERFACE_VERSION:
            raise ValidationError("unsupported Engine Interface version")

    @property
    def integrity_status(self) -> Integrity:
        return Integrity.SEALED if self.verify() else Integrity.UNSEALED

    @property
    def input_ref(self) -> Optional[str]:
        value = self.invocation.get("input_ref")
        return value if isinstance(value, str) else None

    @property
    def invocation_id(self) -> Optional[str]:
        value = self.invocation.get("invocation_id")
        return value if isinstance(value, str) else None

    @property
    def engine_version(self) -> Optional[str]:
        engine = self.invocation.get("engine")
        value = engine.get("engine_version") if isinstance(engine, Mapping) else None
        return value if isinstance(value, str) else None

    @property
    def capability_version(self) -> Optional[str]:
        operation = self.invocation.get("operation")
        value = operation.get("capability_version") if isinstance(operation, Mapping) else None
        return value if isinstance(value, str) else None

    def require_text(self) -> str:
        if self.text is None:
            raise EngineExecutionError("Engine view has no text", view=self)
        return self.text

    def recovery_binding(self) -> Mapping[str, object]:
        if self.recovery_ref is None:
            raise ValidationError("view has no recovery binding")
        return {
            "recovery_ref": self.recovery_ref,
            "source_ref": self.source_ref,
            "source_digest": self.source_digest,
        }

    def verify(self) -> bool:
        try:
            if self.status not in {"succeeded", "degraded"}:
                return False
            if self.recovery_ref is None:
                return False
            source_refs = self.invocation.get("source_refs")
            if not isinstance(source_refs, (list, tuple)) or self.source_ref not in source_refs:
                return False
            if self.output_digest is None or self.output_ref is None or self.text is None:
                return False
            if self.observation.get("invocation_id") != self.invocation_id:
                return False
            if self.observation.get("output_digest") != self.output_digest:
                return False
            if self.observation.get("output_ref") != self.output_ref:
                return False
            if self.receipt_link is None:
                return False
            if self.receipt_link.invocation_id != self.invocation_id:
                return False
            return True
        except (AttributeError, TypeError, ValueError):
            return False

    def to_dict(self) -> Mapping[str, object]:
        result = {
            "schema_version": self.schema_version,
            "transport_version": self.transport_version,
            "engine_interface_version": self.engine_interface_version,
            "source": _plain(self.source.to_dict()),
            "text": self.text,
            "output_ref": self.output_ref,
            "output_digest": self.output_digest,
            "source_ref": self.source_ref,
            "source_digest": self.source_digest,
            "recovery_ref": self.recovery_ref,
            "status": self.status,
            "measurements": [_plain(item.to_dict()) for item in self.measurements],
            "failure": _plain(self.failure.to_dict()) if self.failure else None,
            "receipt_link": _plain(self.receipt_link.to_dict()) if self.receipt_link else None,
            "invocation": _plain(self.invocation),
            "observation": _plain(self.observation),
        }
        return result


__all__ = [
    "ContextFailure",
    "ContextMeasurement",
    "ContextPlan",
    "ContextReceiptLink",
    "ContextSource",
    "ContextView",
    "EngineStatus",
    "ENGINE_INTERFACE_VERSION",
    "FailureCode",
    "Freshness",
    "HostOutcome",
    "Integrity",
    "RecoveredSource",
    "SCHEMA_VERSION",
    "SessionState",
    "TRANSPORT_VERSION",
    "canonical_bytes",
    "canonical_json",
    "exact_keys",
    "sha256_digest",
    "strict_json_loads",
    "validate_digest",
    "validate_output_ref",
    "validate_ref",
]
