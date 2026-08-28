"""Durable, provider-free Product workspace contract.

The workspace journal is deliberately independent from the Engine protocol.
Only immutable identifiers and explicitly committed Product values cross this
module's persistence boundary.
"""

from __future__ import annotations

import contextlib
import datetime as _datetime
import errno
import hashlib
import os
import re
import stat
import tempfile
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field
from types import MappingProxyType, ModuleType
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .errors import (
    WorkspaceAlreadyExistsError,
    WorkspaceConflictError,
    WorkspaceCorruptError,
    WorkspaceError,
    WorkspaceIOError,
    WorkspaceIncompatibleError,
    WorkspaceLifecycleError,
    WorkspaceLockError,
    WorkspaceNotFoundError,
    WorkspacePolicyError,
    WorkspaceSensitiveDataError,
    WorkspaceValidationError,
)
from .protocol import ContextReceiptLink, ContextSource, canonical_bytes, strict_json_loads
from .receipt import ContextReceipt
from .session import ContextSession

fcntl: Optional[ModuleType]
try:
    import fcntl
except ImportError:  # pragma: no cover - the supported CI is POSIX
    fcntl = None


_IDENTITY_SCHEMA = "leanctx.workspace-identity/v1"
_ANCHOR_SCHEMA = "leanctx.source-anchor/v1"
_ENTRY_SCHEMA = "leanctx.project-context-entry/v1"
_CONTEXT_SCHEMA = "leanctx.project-context/v1"
_POLICY_SCHEMA = "leanctx.workspace-policy/v1"
_STATUS_SCHEMA = "leanctx.workspace-status/v1"
_RECEIPT_SCHEMA = "leanctx.workspace-receipt/v1"
_EVENT_SCHEMA = "leanctx.workspace-event/v1"
_CHECKPOINT_SCHEMA = "leanctx.context-checkpoint/v2"
_PACKAGE_PIN_SCHEMA = "leanctx.package-pin/v1"
_LOGICAL_STATE_SCHEMA = "leanctx.workspace.state/v1"
_SDK_P6_CONTRACT = "leanctx-product-sdk-research/p6"
_ZERO_DIGEST = "0" * 64
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)
_EVENT_FILE_RE = re.compile(r"^([0-9]{16})-([0-9a-f]{64})\.json$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_P4_RECEIPT_REF_RE = re.compile(r"^receipt:sha256:[0-9a-f]{64}$")
_KINDS = frozenset(("filesystem", "git", "archive", "api", "custom"))
_CATEGORIES = frozenset(
    ("facts", "decisions", "constraints", "unresolved_questions", "source_refs")
)
_FRESHNESS = frozenset(("current", "stale", "unknown"))
_TRUST = frozenset(("unverified", "local", "verified"))
_LIFECYCLES = frozenset(("active", "completed", "aborted"))
_HEALTH = frozenset(("healthy", "corrupt", "incompatible"))
_EVENT_KINDS = frozenset(
    (
        "workspace_created",
        "source_attached",
        "source_updated",
        "policy_tightened",
        "session_attached",
        "context_committed",
        "checkpoint_created",
        "workspace_restored",
        "workspace_seeded",
        "workspace_sealed",
        "package_pinned",
        "workspace_forked",
        "handoff_applied",
        "workspace_completed",
        "workspace_aborted",
    )
)
_LOCK_TIMEOUT = 1.0
_MAX_EVENT_BYTES = 16 * 1024 * 1024
_MAX_SOURCE_IDS = 16
_MAX_ENTRY_REFS = 16
_MAX_RECEIPT_IDS = 4096
_MAX_RECEIPT_REFS = 4096
_MAX_VALUE_BYTES = 4096
_MAX_CONTEXT_BYTES = 262144
_MAX_EVENTS = 4096
_MAX_SOURCES = 128
_MAX_SESSIONS = 256
_MAX_CHECKPOINTS = 512
_MAX_PACKAGE_PINS = 128
_MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024
_TRUST_CONSTRUCTION_TOKEN = object()


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return dict(value)


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical(value: Any) -> bytes:
    try:
        return canonical_bytes(_plain(value))
    except Exception as exc:
        raise WorkspaceValidationError("workspace value is not canonical JSON") from exc


def _domain_digest(domain: str, value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        domain.encode("utf-8") + b"\n" + _canonical(value)
    ).hexdigest()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: Any, field_name: str, maximum: int, *, controls: bool = True) -> str:
    if not isinstance(value, str):
        raise WorkspaceValidationError(f"{field_name} must be a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise WorkspaceValidationError(f"{field_name} is not valid UTF-8") from exc
    if not encoded:
        raise WorkspaceValidationError(f"{field_name} must not be empty")
    if len(encoded) > maximum:
        raise WorkspaceValidationError(f"{field_name} exceeds its UTF-8 bound")
    if "\x00" in value or (controls and any(ord(char) < 0x20 for char in value)):
        raise WorkspaceValidationError(f"{field_name} contains a control character")
    return value


def _optional_text(value: Any, field_name: str, maximum: int) -> Optional[str]:
    if value is None:
        return None
    return _text(value, field_name, maximum)


def _uuid(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value) or value.lower() != value:
        raise WorkspaceValidationError(f"{field_name} must be a canonical lowercase UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise WorkspaceValidationError(f"{field_name} must be a canonical lowercase UUID") from exc
    return value


def _digest_value(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise WorkspaceValidationError(f"{field_name} must be a sha256 digest")
    return value


def _ref(value: Any, field_name: str, maximum: int = 512) -> str:
    value = _text(value, field_name, maximum)
    if any(ord(char) < 0x20 or ord(char) > 0x7e for char in value):
        raise WorkspaceValidationError(f"{field_name} must be printable")
    return value


def _refs(values: Any, field_name: str, maximum: int) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise WorkspaceValidationError(f"{field_name} must be a list")
    if len(values) > maximum:
        raise WorkspaceValidationError(f"{field_name} exceeds its bound")
    result = tuple(sorted({_ref(item, field_name) for item in values}))
    if len(result) != len(values):
        raise WorkspaceValidationError(f"{field_name} must contain unique values")
    return result


def _ids(values: Any, field_name: str, maximum: int) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise WorkspaceValidationError(f"{field_name} must be a list")
    if len(values) > maximum:
        raise WorkspaceValidationError(f"{field_name} exceeds its bound")
    result = tuple(sorted({_text(item, field_name, 128) for item in values}))
    if len(result) != len(values):
        raise WorkspaceValidationError(f"{field_name} must contain unique values")
    return result


def _parse_timestamp(value: Any, field_name: str) -> _datetime.datetime:
    value = _text(value, field_name, 32)
    if not _TIMESTAMP_RE.fullmatch(value):
        raise WorkspaceValidationError(f"{field_name} must be an RFC3339 UTC timestamp")
    try:
        if "." in value:
            parsed = _datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
        else:
            parsed = _datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise WorkspaceValidationError(f"{field_name} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _timestamp(value: Any, field_name: str) -> str:
    parsed = _parse_timestamp(value, field_name)
    suffix = f".{parsed.microsecond:06d}" if parsed.microsecond else ""
    return parsed.strftime("%Y-%m-%dT%H:%M:%S") + suffix + "Z"


def _now_timestamp() -> str:
    current = _datetime.datetime.now(_datetime.timezone.utc).replace(tzinfo=None)
    suffix = f".{current.microsecond:06d}" if current.microsecond else ""
    return current.strftime("%Y-%m-%dT%H:%M:%S") + suffix + "Z"


def _exact(value: Any, keys: Iterable[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise WorkspaceIncompatibleError(f"{label} fields do not match the v1 contract")
    if any(not isinstance(key, str) for key in value):
        raise WorkspaceIncompatibleError(f"{label} has a non-string field")


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise WorkspaceValidationError(f"{field_name} must be boolean")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkspaceValidationError(f"{field_name} must be a positive integer")
    return value


def _sensitive_walk(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key in value:
            yield from _sensitive_walk(key)
        for item in value.values():
            yield from _sensitive_walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _sensitive_walk(item)


_PEM_RE = re.compile(r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----")
_AWS_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_OPENAI_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]+")
_GITHUB_RE = re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|[\s\"'/?&#;])"
    r"(api[-_ ]?key|apikey|secret|token|password|private[-_ ]?key)"
    r"\s*(?:[:=]|%3a|%3d)\s*[^\s,;}\"'&]+"
)


def _reject_sensitive(value: Any, field_name: str) -> None:
    for text in _sensitive_walk(value):
        if (
            _PEM_RE.search(text)
            or _AWS_RE.search(text)
            or _OPENAI_RE.search(text)
            or _GITHUB_RE.search(text)
            or _BEARER_RE.search(text)
            or _ASSIGNMENT_RE.search(text)
        ):
            raise WorkspaceSensitiveDataError(field_name)


@dataclass(frozen=True)
class SourceRevision:
    kind: str
    value: str

    def __post_init__(self) -> None:
        _text(self.kind, "revision.kind", 64)
        _text(self.value, "revision.value", 2048)

    def to_dict(self) -> Mapping[str, object]:
        return _mapping({"kind": self.kind, "value": self.value})

    @classmethod
    def from_dict(cls, value: Any) -> "SourceRevision":
        _exact(value, {"kind", "value"}, "revision")
        try:
            return cls(value["kind"], value["value"])
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceIncompatibleError("invalid revision") from exc


@dataclass(frozen=True)
class SourceFreshness:
    observed_at: str
    status: str
    valid_until: Optional[str] = None

    def __post_init__(self) -> None:
        observed_dt = _parse_timestamp(self.observed_at, "freshness.observed_at")
        observed = _timestamp(self.observed_at, "freshness.observed_at")
        if self.status not in _FRESHNESS:
            raise WorkspaceValidationError("freshness.status is invalid")
        valid_dt = None if self.valid_until is None else _parse_timestamp(
            self.valid_until, "freshness.valid_until"
        )
        valid = None if valid_dt is None else _timestamp(self.valid_until, "freshness.valid_until")
        if valid_dt is not None and valid_dt < observed_dt:
            raise WorkspaceValidationError("freshness.valid_until precedes observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "valid_until", valid)

    def to_dict(self) -> Mapping[str, object]:
        result = {"observed_at": self.observed_at, "status": self.status}
        if self.valid_until is not None:
            result["valid_until"] = self.valid_until
        return _mapping(result)

    @classmethod
    def from_dict(cls, value: Any) -> "SourceFreshness":
        if not isinstance(value, Mapping):
            raise WorkspaceIncompatibleError("freshness is not an object")
        keys = {"observed_at", "status"}
        if "valid_until" in value:
            keys.add("valid_until")
        _exact(value, keys, "freshness")
        try:
            return cls(value["observed_at"], value["status"], value.get("valid_until"))
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceIncompatibleError("invalid freshness") from exc


@dataclass(frozen=True)
class SourceRecovery:
    kind: str
    immutable_ref: str
    digest: Optional[str] = None

    def __post_init__(self) -> None:
        _text(self.kind, "recovery.kind", 64)
        _ref(self.immutable_ref, "recovery.immutable_ref", 2048)
        if self.digest is not None:
            _digest_value(self.digest, "recovery.digest")

    def to_dict(self) -> Mapping[str, object]:
        result = {"kind": self.kind, "immutable_ref": self.immutable_ref}
        if self.digest is not None:
            result["digest"] = self.digest
        return _mapping(result)

    @classmethod
    def from_dict(cls, value: Any) -> "SourceRecovery":
        if not isinstance(value, Mapping):
            raise WorkspaceIncompatibleError("recovery is not an object")
        keys = {"kind", "immutable_ref"}
        if "digest" in value:
            keys.add("digest")
        _exact(value, keys, "recovery")
        try:
            return cls(value["kind"], value["immutable_ref"], value.get("digest"))
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceIncompatibleError("invalid recovery") from exc


@dataclass(frozen=True)
class SourceTrust:
    level: str
    evidence_refs: Tuple[str, ...] = ()
    _construction_token: object = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.level not in _TRUST:
            raise WorkspaceValidationError("trust.level is invalid")
        refs = _refs(self.evidence_refs, "trust.evidence_refs", 32)
        if self.level == "verified":
            if self._construction_token is not _TRUST_CONSTRUCTION_TOKEN or not refs or any(
                not _P4_RECEIPT_REF_RE.fullmatch(ref) for ref in refs
            ):
                raise WorkspaceValidationError("verified trust requires P4-bound evidence")
        object.__setattr__(self, "evidence_refs", refs)

    def to_dict(self) -> Mapping[str, object]:
        return _mapping({"level": self.level, "evidence_refs": list(self.evidence_refs)})

    @classmethod
    def from_dict(cls, value: Any) -> "SourceTrust":
        return _source_trust_from_dict(value, allow_verified=False)


def _verified_source_trust(refs: Sequence[str]) -> SourceTrust:
    value = object.__new__(SourceTrust)
    object.__setattr__(value, "level", "verified")
    object.__setattr__(value, "evidence_refs", tuple(refs))
    object.__setattr__(value, "_construction_token", _TRUST_CONSTRUCTION_TOKEN)
    SourceTrust.__post_init__(value)
    return value


def _source_trust_from_dict(value: Any, *, allow_verified: bool) -> SourceTrust:
    _exact(value, {"level", "evidence_refs"}, "trust")
    if not isinstance(value.get("evidence_refs"), list):
        raise WorkspaceIncompatibleError("trust.evidence_refs must be a JSON array")
    try:
        if value["level"] == "verified":
            if not allow_verified:
                raise WorkspaceIncompatibleError("verified trust requires SDK evidence")
            return _verified_source_trust(value["evidence_refs"])
        return SourceTrust(value["level"], value["evidence_refs"])
    except WorkspaceError:
        raise
    except Exception as exc:
        raise WorkspaceIncompatibleError("invalid trust") from exc


@dataclass(frozen=True)
class SourceScope:
    kind: str
    value: str

    def __post_init__(self) -> None:
        _text(self.kind, "scope.kind", 64)
        _text(self.value, "scope.value", 2048)

    def to_dict(self) -> Mapping[str, object]:
        return _mapping({"kind": self.kind, "value": self.value})

    @classmethod
    def from_dict(cls, value: Any) -> "SourceScope":
        _exact(value, {"kind", "value"}, "scope")
        try:
            return cls(value["kind"], value["value"])
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceIncompatibleError("invalid scope") from exc


@dataclass(frozen=True)
class SourceAnchor:
    source_id: str
    kind: str
    canonical_id: str
    revision: Optional[SourceRevision] = None
    freshness: Optional[SourceFreshness] = None
    recovery: Optional[SourceRecovery] = None
    trust: Optional[SourceTrust] = None
    scope: Optional[SourceScope] = None
    engine_binding: Optional[Mapping[str, object]] = None

    def __post_init__(self) -> None:
        source_id = _text(self.source_id, "source_id", 128)
        if self.kind not in _KINDS:
            raise WorkspaceValidationError("source kind is invalid")
        canonical_id = _text(self.canonical_id, "canonical_id", 2048)
        revision = self.revision
        freshness = self.freshness
        recovery = self.recovery
        trust = self.trust
        scope = self.scope
        try:
            if isinstance(revision, Mapping):
                revision = SourceRevision.from_dict(revision)
            if isinstance(freshness, Mapping):
                freshness = SourceFreshness.from_dict(freshness)
            if isinstance(recovery, Mapping):
                recovery = SourceRecovery.from_dict(recovery)
            if isinstance(trust, Mapping):
                trust = SourceTrust.from_dict(trust)
            if isinstance(scope, Mapping):
                scope = SourceScope.from_dict(scope)
        except WorkspaceError as exc:
            raise WorkspaceValidationError("source anchor contains an invalid value") from exc
        if self.revision is not None:
            if not isinstance(revision, SourceRevision):
                raise WorkspaceValidationError("revision must be SourceRevision")
            if revision.kind != self.kind:
                raise WorkspaceValidationError("revision kind does not match source kind")
        if not isinstance(freshness, SourceFreshness):
            raise WorkspaceValidationError("freshness must be SourceFreshness")
        if recovery is not None and not isinstance(recovery, SourceRecovery):
            raise WorkspaceValidationError("recovery must be SourceRecovery")
        if not isinstance(trust, SourceTrust):
            raise WorkspaceValidationError("trust must be SourceTrust")
        if not isinstance(scope, SourceScope):
            raise WorkspaceValidationError("scope must be SourceScope")
        binding = self.engine_binding
        if isinstance(binding, ContextSource):
            binding = binding.to_dict()
        if binding is not None:
            if self.kind != "filesystem":
                raise WorkspaceValidationError("engine_binding requires a filesystem source")
            if not isinstance(binding, Mapping):
                raise WorkspaceValidationError("engine_binding must be a mapping")
            allowed = {"path", "project_root", "media_type", "source_ref", "source_digest"}
            if set(binding) - allowed or "path" not in binding or "project_root" not in binding or "media_type" not in binding:
                raise WorkspaceValidationError("engine_binding fields do not match ContextSource")
            try:
                source = _source_from_binding(binding)
            except Exception as exc:
                raise WorkspaceValidationError("engine_binding is not a valid ContextSource") from exc
            normalized = _plain(source.to_dict())
            if _plain(binding) != normalized:
                raise WorkspaceValidationError("engine_binding is not canonical")
            binding = normalized
        elif self.kind != "filesystem":
            binding = None
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "canonical_id", canonical_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "freshness", freshness)
        object.__setattr__(self, "recovery", recovery)
        object.__setattr__(self, "trust", trust)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "engine_binding", _freeze(binding) if binding is not None else None)

    @property
    def schema_version(self) -> str:
        return _ANCHOR_SCHEMA

    def to_dict(self) -> Mapping[str, object]:
        assert self.freshness is not None
        assert self.trust is not None
        assert self.scope is not None
        return _mapping(
            {
                "schema_version": _ANCHOR_SCHEMA,
                "source_id": self.source_id,
                "kind": self.kind,
                "canonical_id": self.canonical_id,
                "revision": _plain(self.revision.to_dict()) if self.revision else None,
                "freshness": _plain(self.freshness.to_dict()),
                "recovery": _plain(self.recovery.to_dict()) if self.recovery else None,
                "trust": _plain(self.trust.to_dict()),
                "scope": _plain(self.scope.to_dict()),
                "engine_binding": _plain(self.engine_binding) if self.engine_binding else None,
            }
        )

    @classmethod
    def from_dict(cls, value: Any) -> "SourceAnchor":
        return _source_anchor_from_dict(value, allow_verified=False)


def _source_anchor_from_dict(value: Any, *, allow_verified: bool) -> SourceAnchor:
    _exact(
            value,
            {
                "schema_version",
                "source_id",
                "kind",
                "canonical_id",
                "revision",
                "freshness",
                "recovery",
                "trust",
                "scope",
                "engine_binding",
            },
            "source anchor",
    )
    if value["schema_version"] != _ANCHOR_SCHEMA:
        raise WorkspaceIncompatibleError("unknown source anchor schema")
    try:
        return SourceAnchor(
            value["source_id"],
            value["kind"],
            value["canonical_id"],
            SourceRevision.from_dict(value["revision"]) if value["revision"] is not None else None,
            SourceFreshness.from_dict(value["freshness"]),
            SourceRecovery.from_dict(value["recovery"]) if value["recovery"] is not None else None,
            _source_trust_from_dict(value["trust"], allow_verified=allow_verified),
            SourceScope.from_dict(value["scope"]),
            value["engine_binding"],
        )
    except WorkspaceIncompatibleError:
        raise
    except WorkspaceError:
        raise WorkspaceIncompatibleError("invalid source anchor")
    except Exception as exc:
        raise WorkspaceIncompatibleError("invalid source anchor") from exc


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_id: str
    name: str
    created_at: str
    state_id: str = field(init=False)

    def __post_init__(self) -> None:
        workspace_id = _uuid(self.workspace_id, "workspace_id")
        name = _text(self.name, "name", 128)
        created_at = _timestamp(self.created_at, "created_at")
        base = {
            "schema_version": _IDENTITY_SCHEMA,
            "workspace_id": workspace_id,
            "name": name,
            "created_at": created_at,
        }
        state_id = _digest(base)
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "state_id", state_id)

    @property
    def schema_version(self) -> str:
        return _IDENTITY_SCHEMA

    def to_dict(self) -> Mapping[str, object]:
        return _mapping(
            {
                "schema_version": _IDENTITY_SCHEMA,
                "workspace_id": self.workspace_id,
                "name": self.name,
                "created_at": self.created_at,
                "state_id": self.state_id,
            }
        )

    @classmethod
    def from_dict(cls, value: Any) -> "WorkspaceIdentity":
        _exact(value, {"schema_version", "workspace_id", "name", "created_at", "state_id"}, "identity")
        if value["schema_version"] != _IDENTITY_SCHEMA:
            raise WorkspaceIncompatibleError("unknown workspace identity schema")
        try:
            result = cls(value["workspace_id"], value["name"], value["created_at"])
        except WorkspaceError:
            raise WorkspaceIncompatibleError("invalid workspace identity")
        if result.state_id != value["state_id"]:
            raise WorkspaceCorruptError()
        return result


@dataclass(frozen=True)
class ProjectContextEntry:
    entry_id: str
    category: str
    value: str
    source_ids: Tuple[str, ...] = ()
    session_id: Optional[str] = None
    receipt_refs: Tuple[str, ...] = ()
    recovery_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        entry_id = _uuid(self.entry_id, "entry_id")
        if self.category not in _CATEGORIES:
            raise WorkspaceValidationError("entry category is invalid")
        value = _text(self.value, "value", _MAX_VALUE_BYTES)
        source_ids = _ids(self.source_ids, "source_ids", _MAX_SOURCE_IDS)
        session_id = _optional_text(self.session_id, "session_id", 512)
        receipt_refs = _refs(self.receipt_refs, "receipt_refs", _MAX_ENTRY_REFS)
        recovery_refs = _refs(self.recovery_refs, "recovery_refs", _MAX_ENTRY_REFS)
        object.__setattr__(self, "entry_id", entry_id)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "source_ids", source_ids)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "receipt_refs", receipt_refs)
        object.__setattr__(self, "recovery_refs", recovery_refs)

    @property
    def schema_version(self) -> str:
        return _ENTRY_SCHEMA

    def to_dict(self) -> Mapping[str, object]:
        return _mapping(
            {
                "schema_version": _ENTRY_SCHEMA,
                "entry_id": self.entry_id,
                "category": self.category,
                "value": self.value,
                "source_ids": list(self.source_ids),
                "session_id": self.session_id,
                "receipt_refs": list(self.receipt_refs),
                "recovery_refs": list(self.recovery_refs),
            }
        )

    @classmethod
    def from_dict(cls, value: Any) -> "ProjectContextEntry":
        _exact(
            value,
            {
                "schema_version",
                "entry_id",
                "category",
                "value",
                "source_ids",
                "session_id",
                "receipt_refs",
                "recovery_refs",
            },
            "project context entry",
        )
        if value["schema_version"] != _ENTRY_SCHEMA:
            raise WorkspaceIncompatibleError("unknown project context entry schema")
        for field_name in ("source_ids", "receipt_refs", "recovery_refs"):
            if not isinstance(value.get(field_name), list):
                raise WorkspaceIncompatibleError(f"{field_name} must be a JSON array")
        try:
            return cls(
                value["entry_id"],
                value["category"],
                value["value"],
                tuple(value["source_ids"]),
                value["session_id"],
                tuple(value["receipt_refs"]),
                tuple(value["recovery_refs"]),
            )
        except WorkspaceError:
            raise WorkspaceIncompatibleError("invalid project context entry")
        except Exception as exc:
            raise WorkspaceIncompatibleError("invalid project context entry") from exc


@dataclass(frozen=True)
class ProjectContext:
    workspace_id: str
    state_digest: str
    entries: Tuple[ProjectContextEntry, ...]
    filtered_count: int
    omitted_by_bounds: int

    def __post_init__(self) -> None:
        _uuid(self.workspace_id, "workspace_id")
        _digest_value(self.state_digest, "state_digest")
        entries = tuple(self.entries)
        if any(not isinstance(entry, ProjectContextEntry) for entry in entries):
            raise WorkspaceValidationError("entries must be ProjectContextEntry values")
        for field_name in ("filtered_count", "omitted_by_bounds"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WorkspaceValidationError(f"{field_name} must be a non-negative integer")
        object.__setattr__(self, "entries", entries)

    @property
    def schema_version(self) -> str:
        return _CONTEXT_SCHEMA

    @property
    def context_bytes(self) -> int:
        return len(_canonical([entry.to_dict() for entry in self.entries]))

    def to_dict(self) -> Mapping[str, object]:
        return _mapping(
            {
                "schema_version": _CONTEXT_SCHEMA,
                "workspace_id": self.workspace_id,
                "state_digest": self.state_digest,
                "entries": [_plain(entry.to_dict()) for entry in self.entries],
                "filtered_count": self.filtered_count,
                "omitted_by_bounds": self.omitted_by_bounds,
            }
        )


@dataclass(frozen=True)
class WorkspacePolicy:
    allowed_categories: Tuple[str, ...] = tuple(sorted(_CATEGORIES))
    max_events: int = _MAX_EVENTS
    max_context_entries: int = 256
    max_entry_bytes: int = _MAX_VALUE_BYTES
    max_context_bytes: int = _MAX_CONTEXT_BYTES
    max_sources: int = _MAX_SOURCES
    max_sessions: int = _MAX_SESSIONS
    allow_external_sources: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.allowed_categories, str):
            raise WorkspaceValidationError("allowed_categories must be a collection")
        try:
            categories = tuple(sorted(set(self.allowed_categories)))
        except (TypeError, ValueError) as exc:
            raise WorkspaceValidationError("allowed_categories must be a collection") from exc
        if any(category not in _CATEGORIES for category in categories):
            raise WorkspaceValidationError("allowed_categories contains an invalid category")
        for field_name in (
            "max_events",
            "max_context_entries",
            "max_entry_bytes",
            "max_context_bytes",
            "max_sources",
            "max_sessions",
        ):
            _positive_int(getattr(self, field_name), field_name)
        external = _bool(self.allow_external_sources, "allow_external_sources")
        object.__setattr__(self, "allowed_categories", categories)
        object.__setattr__(self, "allow_external_sources", external)

    @property
    def schema_version(self) -> str:
        return _POLICY_SCHEMA

    def to_dict(self) -> Mapping[str, object]:
        return _mapping(
            {
                "schema_version": _POLICY_SCHEMA,
                "allowed_categories": list(self.allowed_categories),
                "max_events": self.max_events,
                "max_context_entries": self.max_context_entries,
                "max_entry_bytes": self.max_entry_bytes,
                "max_context_bytes": self.max_context_bytes,
                "max_sources": self.max_sources,
                "max_sessions": self.max_sessions,
                "allow_external_sources": self.allow_external_sources,
            }
        )

    @classmethod
    def from_dict(cls, value: Any) -> "WorkspacePolicy":
        _exact(
            value,
            {
                "schema_version",
                "allowed_categories",
                "max_events",
                "max_context_entries",
                "max_entry_bytes",
                "max_context_bytes",
                "max_sources",
                "max_sessions",
                "allow_external_sources",
            },
            "workspace policy",
        )
        if value["schema_version"] != _POLICY_SCHEMA:
            raise WorkspaceIncompatibleError("unknown workspace policy schema")
        if not isinstance(value.get("allowed_categories"), list):
            raise WorkspaceIncompatibleError("allowed_categories must be a JSON array")
        try:
            return cls(
                value["allowed_categories"],
                value["max_events"],
                value["max_context_entries"],
                value["max_entry_bytes"],
                value["max_context_bytes"],
                value["max_sources"],
                value["max_sessions"],
                value["allow_external_sources"],
            )
        except WorkspaceError:
            raise WorkspaceIncompatibleError("invalid workspace policy")
        except Exception as exc:
            raise WorkspaceIncompatibleError("invalid workspace policy") from exc

    def is_tightening(self, previous: "WorkspacePolicy") -> bool:
        return (
            set(self.allowed_categories).issubset(previous.allowed_categories)
            and self.max_events <= previous.max_events
            and self.max_context_entries <= previous.max_context_entries
            and self.max_entry_bytes <= previous.max_entry_bytes
            and self.max_context_bytes <= previous.max_context_bytes
            and self.max_sources <= previous.max_sources
            and self.max_sessions <= previous.max_sessions
            and (not self.allow_external_sources or previous.allow_external_sources)
        )


@dataclass(frozen=True)
class WorkspaceReceipt:
    workspace_id: str
    state_id: str
    sequence: int
    event_id: str
    event_kind: str
    event_digest: str
    state_digest: str
    source_ids: Tuple[str, ...] = ()
    session_id: Optional[str] = None
    engine_receipt_refs: Tuple[str, ...] = ()
    recovery_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _uuid(self.workspace_id, "workspace_id")
        _digest_value(self.state_id, "state_id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence <= 0:
            raise WorkspaceValidationError("sequence must be positive")
        _uuid(self.event_id, "event_id")
        if self.event_kind not in _EVENT_KINDS:
            raise WorkspaceValidationError("event_kind is invalid")
        _digest_value(self.event_digest, "event_digest")
        _digest_value(self.state_digest, "state_digest")
        source_ids = _ids(self.source_ids, "source_ids", _MAX_RECEIPT_IDS)
        session_id = _optional_text(self.session_id, "session_id", 512)
        engine_refs = _refs(self.engine_receipt_refs, "engine_receipt_refs", _MAX_RECEIPT_REFS)
        recovery_refs = _refs(self.recovery_refs, "recovery_refs", _MAX_RECEIPT_REFS)
        object.__setattr__(self, "source_ids", source_ids)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "engine_receipt_refs", engine_refs)
        object.__setattr__(self, "recovery_refs", recovery_refs)

    @property
    def schema_version(self) -> str:
        return _RECEIPT_SCHEMA

    def to_dict(self) -> Mapping[str, object]:
        return _mapping(
            {
                "schema_version": _RECEIPT_SCHEMA,
                "workspace_id": self.workspace_id,
                "state_id": self.state_id,
                "sequence": self.sequence,
                "event_id": self.event_id,
                "event_kind": self.event_kind,
                "event_digest": self.event_digest,
                "state_digest": self.state_digest,
                "source_ids": list(self.source_ids),
                "session_id": self.session_id,
                "engine_receipt_refs": list(self.engine_receipt_refs),
                "recovery_refs": list(self.recovery_refs),
            }
        )


@dataclass(frozen=True)
class WorkspaceStatus:
    identity: Optional[WorkspaceIdentity]
    lifecycle: str
    health: str
    event_count: int
    source_count: int
    session_count: int
    context_entry_count: int
    policy: Optional[WorkspacePolicy]
    state_digest: Optional[str]

    def __post_init__(self) -> None:
        if self.lifecycle not in _LIFECYCLES:
            raise WorkspaceValidationError("lifecycle is invalid")
        if self.health not in _HEALTH:
            raise WorkspaceValidationError("health is invalid")
        for field_name in ("event_count", "source_count", "session_count", "context_entry_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WorkspaceValidationError(f"{field_name} must be non-negative")
        if self.state_digest is not None:
            _digest_value(self.state_digest, "state_digest")

    @property
    def schema_version(self) -> str:
        return _STATUS_SCHEMA

    def to_dict(self) -> Mapping[str, object]:
        return _mapping(
            {
                "schema_version": _STATUS_SCHEMA,
                "identity": _plain(self.identity.to_dict()) if self.identity else None,
                "lifecycle": self.lifecycle,
                "health": self.health,
                "event_count": self.event_count,
                "source_count": self.source_count,
                "session_count": self.session_count,
                "context_entry_count": self.context_entry_count,
                "policy": _plain(self.policy.to_dict()) if self.policy else None,
                "state_digest": self.state_digest,
            }
        )


@dataclass(frozen=True)
class WorkspaceSessionAttachment:
    session: ContextSession
    receipt: WorkspaceReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.session, ContextSession):
            raise WorkspaceValidationError("session must be ContextSession")
        if not isinstance(self.receipt, WorkspaceReceipt):
            raise WorkspaceValidationError("receipt must be WorkspaceReceipt")


@dataclass(frozen=True)
class PackagePin:
    name: str
    version: str
    artifact_digest: str
    manifest_digest: str
    content_hash: str
    signature_state: str
    signer_public_key: Optional[str]
    trust_state: str
    policy_decision: str = "admitted"
    schema_version: str = _PACKAGE_PIN_SCHEMA

    def __post_init__(self) -> None:
        _text(self.name, "package pin name", 128)
        _text(self.version, "package pin version", 64)
        for name in ("artifact_digest", "manifest_digest", "content_hash"):
            _digest_value(getattr(self, name), f"package pin {name}")
        if self.signature_state not in {"signed_valid", "unsigned"}:
            raise WorkspaceValidationError("package pin signature state is invalid")
        if self.trust_state not in {"trusted", "untrusted", "unknown"}:
            raise WorkspaceValidationError("package pin trust state is invalid")
        if self.policy_decision != "admitted":
            raise WorkspacePolicyError()
        if self.schema_version != _PACKAGE_PIN_SCHEMA:
            raise WorkspaceIncompatibleError("package pin schema is unsupported")
        if self.signature_state == "signed_valid":
            if (
                not isinstance(self.signer_public_key, str)
                or len(self.signer_public_key) != 64
                or not all(char in "0123456789abcdef" for char in self.signer_public_key)
            ):
                raise WorkspaceValidationError("package pin signer key is invalid")
        elif self.signer_public_key is not None:
            raise WorkspaceValidationError("unsigned package pin cannot name a signer")

    def to_dict(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "artifact_digest": self.artifact_digest,
            "manifest_digest": self.manifest_digest,
            "content_hash": self.content_hash,
            "signature_state": self.signature_state,
            "signer_public_key": self.signer_public_key,
            "trust_state": self.trust_state,
            "policy_decision": self.policy_decision,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PackagePin":
        _exact(
            value,
            {
                "schema_version",
                "name",
                "version",
                "artifact_digest",
                "manifest_digest",
                "content_hash",
                "signature_state",
                "signer_public_key",
                "trust_state",
                "policy_decision",
            },
            "package pin",
        )
        try:
            return cls(
                value["name"],
                value["version"],
                value["artifact_digest"],
                value["manifest_digest"],
                value["content_hash"],
                value["signature_state"],
                value["signer_public_key"],
                value["trust_state"],
                value["policy_decision"],
                value["schema_version"],
            )
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceIncompatibleError("package pin is invalid") from exc


@dataclass(frozen=True)
class ContextCheckpointV2:
    checkpoint_id: str
    workspace_id: str
    state_digest: str
    workspace_state_ref: str
    logical_state: Mapping[str, object]
    source_anchors: Tuple[Mapping[str, object], ...]
    recovery_refs: Tuple[str, ...]
    package_pins: Tuple[Mapping[str, object], ...]
    package_lock_digest: Optional[str]
    policy_digest: str
    project_context_digest: str
    lineage: Mapping[str, object]
    engine_identity: Mapping[str, object]
    envelope_digest: str
    schema_version: str = _CHECKPOINT_SCHEMA
    state_schema_version: str = _LOGICAL_STATE_SCHEMA
    sdk_contract: str = _SDK_P6_CONTRACT

    def __post_init__(self) -> None:
        _uuid(self.checkpoint_id, "checkpoint_id")
        _uuid(self.workspace_id, "workspace_id")
        for name in (
            "state_digest",
            "policy_digest",
            "project_context_digest",
            "envelope_digest",
        ):
            _digest_value(getattr(self, name), name)
        if self.package_lock_digest is not None:
            _digest_value(self.package_lock_digest, "package_lock_digest")
        if self.schema_version != _CHECKPOINT_SCHEMA:
            raise WorkspaceIncompatibleError("checkpoint schema is unsupported")
        if self.state_schema_version != _LOGICAL_STATE_SCHEMA:
            raise WorkspaceIncompatibleError("logical state schema is unsupported")
        if self.sdk_contract != _SDK_P6_CONTRACT:
            raise WorkspaceIncompatibleError("SDK checkpoint contract is unsupported")
        _ref(self.workspace_state_ref, "workspace_state_ref")
        if not self.workspace_state_ref.startswith("event:sha256:"):
            raise WorkspaceValidationError("workspace_state_ref must bind an event digest")
        logical_state = _plain(self.logical_state)
        anchors = [_plain(anchor) for anchor in self.source_anchors]
        package_pins = [_plain(pin) for pin in self.package_pins]
        lineage = _plain(self.lineage)
        engine_identity = _plain(self.engine_identity)
        if len(anchors) > _MAX_SOURCES:
            raise WorkspacePolicyError()
        if len(package_pins) > _MAX_PACKAGE_PINS:
            raise WorkspacePolicyError()
        if len(self.recovery_refs) > _MAX_RECEIPT_REFS:
            raise WorkspacePolicyError()
        _refs(self.recovery_refs, "recovery_refs", _MAX_RECEIPT_REFS)
        if len(_canonical(logical_state)) > _MAX_CHECKPOINT_BYTES:
            raise WorkspacePolicyError()
        _exact(
            lineage,
            {"kind", "workspace_id", "state_id"},
            "checkpoint lineage",
        )
        if (
            lineage["kind"] != "workspace"
            or _uuid(lineage["workspace_id"], "lineage.workspace_id") != self.workspace_id
        ):
            raise WorkspaceConflictError()
        _ref(lineage["state_id"], "lineage.state_id")
        _exact(
            engine_identity,
            {"interface_version", "schema_version", "transport_version"},
            "engine_identity",
        )
        if engine_identity != {
            "interface_version": "1.0.0",
            "schema_version": 1,
            "transport_version": 1,
        }:
            raise WorkspaceIncompatibleError("Engine checkpoint identity is unsupported")
        _reject_sensitive(logical_state, "logical_state")
        _reject_sensitive(package_pins, "package_pins")
        object.__setattr__(self, "logical_state", _freeze(logical_state))
        object.__setattr__(self, "source_anchors", tuple(_freeze(anchor) for anchor in anchors))
        object.__setattr__(self, "recovery_refs", tuple(self.recovery_refs))
        object.__setattr__(self, "package_pins", tuple(_freeze(pin) for pin in package_pins))
        object.__setattr__(self, "lineage", _freeze(lineage))
        object.__setattr__(self, "engine_identity", _freeze(engine_identity))
        expected_state = _domain_digest("leanctx.workspace.state.v1", logical_state)
        if self.state_digest != expected_state:
            raise WorkspaceCorruptError()
        _validate_checkpoint_projections(self)
        expected_envelope = _checkpoint_envelope_digest(self._unsigned_dict())
        if self.envelope_digest != expected_envelope:
            raise WorkspaceCorruptError()

    def _unsigned_dict(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "workspace_id": self.workspace_id,
            "state_digest": self.state_digest,
            "state_schema_version": self.state_schema_version,
            "workspace_state_ref": self.workspace_state_ref,
            "logical_state": _plain(self.logical_state),
            "source_anchors": [_plain(anchor) for anchor in self.source_anchors],
            "recovery_refs": list(self.recovery_refs),
            "package_pins": [_plain(pin) for pin in self.package_pins],
            "package_lock_digest": self.package_lock_digest,
            "policy_digest": self.policy_digest,
            "project_context_digest": self.project_context_digest,
            "lineage": _plain(self.lineage),
            "engine_identity": _plain(self.engine_identity),
            "sdk_contract": self.sdk_contract,
        }

    def to_dict(self) -> Mapping[str, object]:
        return _mapping(dict(self._unsigned_dict(), envelope_digest=self.envelope_digest))

    @classmethod
    def from_dict(cls, value: Any) -> "ContextCheckpointV2":
        _exact(
            value,
            {
                "schema_version",
                "checkpoint_id",
                "workspace_id",
                "state_digest",
                "state_schema_version",
                "workspace_state_ref",
                "logical_state",
                "source_anchors",
                "recovery_refs",
                "package_pins",
                "package_lock_digest",
                "policy_digest",
                "project_context_digest",
                "lineage",
                "engine_identity",
                "sdk_contract",
                "envelope_digest",
            },
            "checkpoint",
        )
        if not all(
            isinstance(value[name], list)
            for name in ("source_anchors", "recovery_refs", "package_pins")
        ):
            raise WorkspaceIncompatibleError("checkpoint arrays are required")
        if not all(
            isinstance(value[name], Mapping)
            for name in ("logical_state", "lineage", "engine_identity")
        ):
            raise WorkspaceIncompatibleError("checkpoint mappings are required")
        return cls(
            checkpoint_id=value["checkpoint_id"],
            workspace_id=value["workspace_id"],
            state_digest=value["state_digest"],
            workspace_state_ref=value["workspace_state_ref"],
            logical_state=value["logical_state"],
            source_anchors=tuple(value["source_anchors"]),
            recovery_refs=tuple(value["recovery_refs"]),
            package_pins=tuple(value["package_pins"]),
            package_lock_digest=value["package_lock_digest"],
            policy_digest=value["policy_digest"],
            project_context_digest=value["project_context_digest"],
            lineage=value["lineage"],
            engine_identity=value["engine_identity"],
            envelope_digest=value["envelope_digest"],
            schema_version=value["schema_version"],
            state_schema_version=value["state_schema_version"],
            sdk_contract=value["sdk_contract"],
        )


def _checkpoint_envelope_digest(value: Mapping[str, object]) -> str:
    return _domain_digest("leanctx.checkpoint.envelope.v2", value)


class _ProjectionFailure(Exception):
    def __init__(self, kind: str, state: Optional["_WorkspaceState"] = None):
        super().__init__(kind)
        self.kind = kind
        self.state = state


class _WorkspaceState:
    def __init__(self) -> None:
        self.identity: Optional[WorkspaceIdentity] = None
        self.policy: Optional[WorkspacePolicy] = None
        self.lifecycle = "active"
        self.sources: Dict[str, SourceAnchor] = {}
        self.sessions: List[Mapping[str, object]] = []
        self.entries: List[ProjectContextEntry] = []
        self.checkpoints: Dict[str, ContextCheckpointV2] = {}
        self.package_pins: Dict[Tuple[str, str], PackagePin] = {}
        self.package_lock_digest: Optional[str] = None
        self.fork_lineage: Optional[Mapping[str, object]] = None
        self.applied_handoffs: Dict[str, Mapping[str, str]] = {}
        self.events: List[Mapping[str, object]] = []
        self.receipts: Dict[str, WorkspaceReceipt] = {}
        self.last_event_digest = _ZERO_DIGEST

    def clone(self) -> "_WorkspaceState":
        result = _WorkspaceState()
        result.identity = self.identity
        result.policy = self.policy
        result.lifecycle = self.lifecycle
        result.sources = dict(self.sources)
        result.sessions = [dict(session) for session in self.sessions]
        result.entries = list(self.entries)
        result.checkpoints = dict(self.checkpoints)
        result.package_pins = dict(self.package_pins)
        result.package_lock_digest = self.package_lock_digest
        result.fork_lineage = _plain(self.fork_lineage) if self.fork_lineage else None
        result.applied_handoffs = {
            key: dict(value) for key, value in self.applied_handoffs.items()
        }
        result.events = list(self.events)
        result.receipts = dict(self.receipts)
        result.last_event_digest = self.last_event_digest
        return result

    def logical_state(self) -> Mapping[str, object]:
        if self.identity is None or self.policy is None:
            raise WorkspaceCorruptError()
        return _mapping(
            {
                "schema_version": _LOGICAL_STATE_SCHEMA,
                "workspace_id": self.identity.workspace_id,
                "policy": _plain(self.policy.to_dict()),
                "sources": [
                    _plain(self.sources[source_id].to_dict())
                    for source_id in sorted(self.sources)
                ],
                "entries": [_plain(entry.to_dict()) for entry in self.entries],
                "package_pins": [
                    _plain(self.package_pins[key].to_dict())
                    for key in sorted(self.package_pins)
                ],
                "package_lock_digest": self.package_lock_digest,
            }
        )

    def logical_digest(self) -> str:
        return _domain_digest("leanctx.workspace.state.v1", self.logical_state())

    def digest(self) -> str:
        if self.identity is None or self.policy is None:
            raise WorkspaceCorruptError()
        return _digest(
            {
                "identity": _plain(self.identity.to_dict()),
                "latest_sources_sorted_by_id": [
                    _plain(self.sources[source_id].to_dict()) for source_id in sorted(self.sources)
                ],
                "policy": _plain(self.policy.to_dict()),
                "sessions_in_event_order": [_plain(session) for session in self.sessions],
                "entries_in_event_order": [_plain(entry.to_dict()) for entry in self.entries],
                "fork_lineage": _plain(self.fork_lineage),
                "applied_handoffs": [
                    _plain(self.applied_handoffs[key])
                    for key in sorted(self.applied_handoffs)
                ],
                "lifecycle": self.lifecycle,
                "last_event_digest": self.last_event_digest,
            }
        )

    def status(self, health: str = "healthy") -> WorkspaceStatus:
        return WorkspaceStatus(
            self.identity,
            self.lifecycle,
            health,
            len(self.events),
            len(self.sources),
            len(self.sessions),
            len(self.entries),
            self.policy,
            self.digest() if self.identity is not None and self.policy is not None else None,
        )


def _state_policy(state: _WorkspaceState) -> WorkspacePolicy:
    if state.policy is None:
        raise WorkspaceCorruptError()
    return state.policy


def _state_identity(state: _WorkspaceState) -> WorkspaceIdentity:
    if state.identity is None:
        raise WorkspaceCorruptError()
    return state.identity


def _state_context_bytes(entries: Sequence[ProjectContextEntry]) -> int:
    return len(_canonical([entry.to_dict() for entry in entries]))


def _anchor_digest(anchor: SourceAnchor) -> str:
    return _digest(anchor.to_dict())


def _entry_from_input(value: Any) -> ProjectContextEntry:
    if isinstance(value, ProjectContextEntry):
        return value
    if isinstance(value, Mapping):
        return ProjectContextEntry.from_dict(value)
    raise WorkspaceValidationError("entries must contain ProjectContextEntry values")


def _package_lock(pins: Sequence[PackagePin]) -> Optional[str]:
    if not pins:
        return None
    return _domain_digest(
        "leanctx.package.lock.v1",
        [pin.to_dict() for pin in sorted(pins, key=lambda item: (item.name, item.version))],
    )


def _logical_state_components(
    value: Any,
    workspace_id: str,
) -> Tuple[
    WorkspacePolicy,
    Dict[str, SourceAnchor],
    List[ProjectContextEntry],
    Dict[Tuple[str, str], PackagePin],
    Optional[str],
]:
    value = _plain(value)
    _exact(
        value,
        {
            "schema_version",
            "workspace_id",
            "policy",
            "sources",
            "entries",
            "package_pins",
            "package_lock_digest",
        },
        "logical_state",
    )
    if value["schema_version"] != _LOGICAL_STATE_SCHEMA:
        raise WorkspaceIncompatibleError("logical state schema is unsupported")
    if value["workspace_id"] != workspace_id:
        raise WorkspaceConflictError()
    if not all(isinstance(value[name], list) for name in ("sources", "entries", "package_pins")):
        raise WorkspaceIncompatibleError("logical state arrays are required")
    if (
        len(value["sources"]) > _MAX_SOURCES
        or len(value["entries"]) > 256
        or len(value["package_pins"]) > _MAX_PACKAGE_PINS
        or len(_canonical(value)) > _MAX_CHECKPOINT_BYTES
    ):
        raise WorkspacePolicyError()
    policy = WorkspacePolicy.from_dict(value["policy"])
    anchors = [
        _source_anchor_from_dict(anchor, allow_verified=True)
        for anchor in value["sources"]
    ]
    entries = [_entry_from_input(entry) for entry in value["entries"]]
    pins = [PackagePin.from_dict(pin) for pin in value["package_pins"]]
    if [_plain(anchor.to_dict()) for anchor in anchors] != value["sources"]:
        raise WorkspaceIncompatibleError("source anchor projection is not canonical")
    if [_plain(entry.to_dict()) for entry in entries] != value["entries"]:
        raise WorkspaceIncompatibleError("ProjectContext projection is not canonical")
    if [_plain(pin.to_dict()) for pin in pins] != value["package_pins"]:
        raise WorkspaceIncompatibleError("package pin projection is not canonical")
    sources: Dict[str, SourceAnchor] = {}
    for anchor in anchors:
        if anchor.source_id in sources:
            raise WorkspaceIncompatibleError("duplicate source anchor")
        sources[anchor.source_id] = anchor
    if list(sources) != sorted(sources):
        raise WorkspaceIncompatibleError("source anchors are not sorted")
    if any(
        any(source_id not in sources for source_id in entry.source_ids)
        for entry in entries
    ):
        raise WorkspaceIncompatibleError("ProjectContext references an unknown source")
    if len({entry.entry_id for entry in entries}) != len(entries):
        raise WorkspaceIncompatibleError("duplicate ProjectContext entry")
    package_pins = {(pin.name, pin.version): pin for pin in pins}
    if len(package_pins) != len(pins) or list(package_pins) != sorted(package_pins):
        raise WorkspaceIncompatibleError("package pins are duplicated or unsorted")
    lock_digest = _package_lock(pins)
    if value["package_lock_digest"] != lock_digest:
        raise WorkspaceCorruptError()
    return policy, sources, entries, package_pins, lock_digest


def _validate_checkpoint_projections(checkpoint: ContextCheckpointV2) -> None:
    logical_state = _plain(checkpoint.logical_state)
    policy, sources, entries, package_pins, package_lock_digest = _logical_state_components(
        logical_state,
        checkpoint.workspace_id,
    )
    expected_anchors = [
        _plain(sources[source_id].to_dict()) for source_id in sorted(sources)
    ]
    expected_recovery_refs = tuple(
        sorted({ref for entry in entries for ref in entry.recovery_refs})
    )
    expected_package_pins = [
        _plain(package_pins[key].to_dict()) for key in sorted(package_pins)
    ]
    expected_package_lock_digest = package_lock_digest
    expected_policy_digest = _domain_digest(
        "leanctx.workspace.policy.v1",
        policy.to_dict(),
    )
    expected_project_context_digest = _domain_digest(
        "leanctx.project-context.state.v1",
        [entry.to_dict() for entry in entries],
    )
    if (
        [_plain(anchor) for anchor in checkpoint.source_anchors]
        != expected_anchors
        or checkpoint.recovery_refs != expected_recovery_refs
        or [_plain(pin) for pin in checkpoint.package_pins]
        != expected_package_pins
        or checkpoint.package_lock_digest != expected_package_lock_digest
        or checkpoint.policy_digest != expected_policy_digest
        or checkpoint.project_context_digest != expected_project_context_digest
    ):
        raise WorkspaceCorruptError()


def _verify_checkpoint_trust(
    state: _WorkspaceState,
    sources: Mapping[str, SourceAnchor],
) -> None:
    for anchor in sources.values():
        trust = anchor.trust
        if trust is None:
            raise WorkspaceIncompatibleError("checkpoint source lacks trust")
        if trust.level != "verified":
            continue
        if anchor.engine_binding is None:
            raise WorkspaceIncompatibleError("verified checkpoint source lacks Engine binding")
        trusted_seed = False
        for event in state.events:
            if event.get("kind") != "workspace_seeded":
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            package = payload.get("package")
            admission = payload.get("admission")
            checkpoint = payload.get("checkpoint")
            if not (
                isinstance(package, Mapping)
                and isinstance(admission, Mapping)
                and isinstance(checkpoint, Mapping)
            ):
                continue
            source_anchors = checkpoint.get("source_anchors")
            if (
                package.get("signature_state") == "signed_valid"
                and admission.get("trusted_signer") is True
                and isinstance(source_anchors, list)
                and _plain(anchor.to_dict()) in source_anchors
            ):
                trusted_seed = True
                break
        if trusted_seed:
            continue
        matched_refs = set()
        for event in state.events:
            if event["kind"] != "context_committed":
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            provenance = payload.get("provenance")
            if not isinstance(provenance, Mapping):
                continue
            proof = provenance.get("receipt_proof")
            if not isinstance(proof, Mapping):
                continue
            if _plain(proof.get("source")) != _plain(anchor.engine_binding):
                continue
            refs = provenance.get("receipt_refs")
            if isinstance(refs, list):
                matched_refs.update(refs)
        if not set(trust.evidence_refs).issubset(matched_refs):
            raise WorkspaceIncompatibleError(
                "verified checkpoint source lacks durable sealed evidence"
            )


def _source_from_binding(binding: Mapping[str, object]) -> ContextSource:
    try:
        path = binding.get("path")
        project_root = binding.get("project_root")
        media_type = binding.get("media_type")
        source_ref = binding.get("source_ref")
        source_digest = binding.get("source_digest")
        if not isinstance(path, str) or not isinstance(media_type, str):
            raise WorkspaceValidationError("engine_binding path/media_type must be strings")
        if project_root is not None and not isinstance(project_root, str):
            raise WorkspaceValidationError("engine_binding project_root must be a string")
        if source_ref is not None and not isinstance(source_ref, str):
            raise WorkspaceValidationError("engine_binding source_ref must be a string")
        if source_digest is not None and not isinstance(source_digest, str):
            raise WorkspaceValidationError("engine_binding source_digest must be a string")
        return ContextSource(
            path,
            project_root=project_root,
            media_type=media_type,
            source_ref=source_ref,
            source_digest=source_digest,
        )
    except Exception as exc:
        raise WorkspaceValidationError("engine_binding cannot be reconstructed") from exc


def _verify_directory_nofollow(path: str) -> None:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise WorkspaceIOError()
    try:
        canonical = os.path.realpath(os.path.abspath(path))
        fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in canonical.split(os.sep):
                if not component:
                    continue
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fd,
                )
                os.close(fd)
                fd = child
            item = os.fstat(fd)
            if not stat.S_ISDIR(item.st_mode):
                raise WorkspaceIOError()
        finally:
            os.close(fd)
    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceIOError() from exc


def _open_relative_nofollow(directory_fd: int, relative_path: str) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise WorkspaceIOError()
    components = relative_path.replace(os.sep, "/").split("/")
    if not components or any(component in ("", ".", "..") for component in components):
        raise WorkspaceValidationError("source path is not a rooted relative file path")
    current_fd = os.dup(directory_fd)
    try:
        for component in components[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = child
        return os.open(
            components[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current_fd
        )
    finally:
        os.close(current_fd)


@dataclass(frozen=True)
class _SourcePin:
    root_fd: int
    source_fd: int
    root_stat: os.stat_result
    source_stat: os.stat_result
    relative_path: str


@contextlib.contextmanager
def _pin_source(source: ContextSource) -> Iterator[_SourcePin]:
    if not isinstance(source, ContextSource):
        raise WorkspaceValidationError("source must be ContextSource")
    root_fd = -1
    source_fd = -1
    try:
        root = source.project_root
        if root is None:
            raise WorkspaceValidationError("source project_root is unavailable")
        root_stat_path = os.lstat(root)
        if stat.S_ISLNK(root_stat_path.st_mode) or not stat.S_ISDIR(root_stat_path.st_mode):
            raise WorkspaceValidationError("source project_root is not a directory")
        root_fd, root_stat = _open_directory_ancestry(os.path.realpath(root))
        relative_path = source.relative_path
        source_fd = _open_relative_nofollow(root_fd, relative_path)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise WorkspaceValidationError("source path is not a regular file")
        yield _SourcePin(root_fd, source_fd, root_stat, source_stat, relative_path)
    except WorkspaceError:
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise WorkspaceValidationError("source path must not be a symlink") from exc
        raise WorkspaceIOError() from exc
    finally:
        if source_fd != -1:
            os.close(source_fd)
        if root_fd != -1:
            os.close(root_fd)


def _revalidate_source_pin(source: ContextSource, pin: _SourcePin) -> None:
    try:
        root = source.project_root
        if root is None:
            raise WorkspaceValidationError("source project_root is unavailable")
        if not _same_inode(pin.root_stat, os.fstat(pin.root_fd)) or not _same_inode(
            pin.source_stat, os.fstat(pin.source_fd)
        ):
            raise WorkspaceConflictError()
        root_fd, root_stat = _open_directory_ancestry(os.path.realpath(root))
        try:
            source_fd = _open_relative_nofollow(root_fd, source.relative_path)
            try:
                current_source = os.fstat(source_fd)
            finally:
                os.close(source_fd)
        finally:
            os.close(root_fd)
        if not _same_inode(pin.root_stat, root_stat) or not _same_inode(
            pin.source_stat, current_source
        ):
            raise WorkspaceConflictError()
    except WorkspaceError:
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise WorkspaceConflictError() from exc
        raise WorkspaceIOError() from exc


def _verify_checkpoint_sources(checkpoint: ContextCheckpointV2) -> None:
    _, sources, _, _, _ = _logical_state_components(
        checkpoint.logical_state,
        checkpoint.workspace_id,
    )
    for anchor in sources.values():
        if (
            anchor.kind != "filesystem"
            or anchor.engine_binding is None
            or anchor.revision is None
            or anchor.revision.kind != "filesystem"
            or not _DIGEST_RE.fullmatch(anchor.revision.value)
        ):
            raise WorkspaceConflictError()
        source = _source_from_binding(anchor.engine_binding)
        with _pin_source(source) as pin:
            if pin.source_stat.st_size > _MAX_EVENT_BYTES:
                raise WorkspacePolicyError()
            digests = []
            for _ in range(2):
                before = os.fstat(pin.source_fd)
                digest = hashlib.sha256()
                os.lseek(pin.source_fd, 0, os.SEEK_SET)
                while True:
                    chunk = os.read(pin.source_fd, 64 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                after = os.fstat(pin.source_fd)
                stable_fields = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if any(getattr(before, name) != getattr(after, name) for name in stable_fields):
                    raise WorkspaceConflictError()
                digests.append("sha256:" + digest.hexdigest())
            if digests[0] != digests[1]:
                raise WorkspaceConflictError()
            actual = digests[0]
            if actual != anchor.revision.value:
                raise WorkspaceConflictError()
            if anchor.recovery is not None and anchor.recovery.digest is not None:
                if anchor.recovery.digest != actual:
                    raise WorkspaceConflictError()
            _revalidate_source_pin(source, pin)


def _validate_anchor_binding(anchor: SourceAnchor, source: ContextSource) -> None:
    if anchor.kind != "filesystem" or anchor.engine_binding is None:
        raise WorkspaceValidationError("source is not eligible for a local Engine session")
    if not isinstance(source, ContextSource):
        raise WorkspaceValidationError("source must be ContextSource")
    binding_source = _source_from_binding(anchor.engine_binding)
    if _plain(binding_source.to_dict()) != _plain(source.to_dict()):
        raise WorkspaceConflictError()
    root = source.project_root
    if root is None:
        raise WorkspaceValidationError("source project_root is unavailable")
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise WorkspaceIOError() from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise WorkspaceValidationError("source project_root is not a directory")
    _verify_directory_nofollow(root)


def _bind_portable_anchor(anchor: SourceAnchor, source: ContextSource) -> SourceAnchor:
    """Verify a local source without adding its path to portable durable state."""
    if not isinstance(source, ContextSource):
        raise WorkspaceValidationError("source must be ContextSource")
    if (
        anchor.kind != "filesystem"
        or anchor.revision is None
        or anchor.revision.kind != "filesystem"
        or not _DIGEST_RE.fullmatch(anchor.revision.value)
        or anchor.canonical_id != "file://" + source.relative_path
    ):
        raise WorkspaceConflictError()
    with _pin_source(source) as pin:
        if pin.source_stat.st_size > _MAX_EVENT_BYTES:
            raise WorkspacePolicyError()
        digest = hashlib.sha256()
        os.lseek(pin.source_fd, 0, os.SEEK_SET)
        while True:
            chunk = os.read(pin.source_fd, 64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if "sha256:" + digest.hexdigest() != anchor.revision.value:
            raise WorkspaceConflictError()
        _revalidate_source_pin(source, pin)
    return SourceAnchor(
        anchor.source_id,
        anchor.kind,
        anchor.canonical_id,
        anchor.revision,
        anchor.freshness,
        anchor.recovery,
        anchor.trust,
        anchor.scope,
        source.to_dict(),
    )


def _anchor_with_evidence(
    anchor: SourceAnchor,
    evidence_receipts: Sequence[ContextReceipt],
) -> SourceAnchor:
    if not isinstance(evidence_receipts, (list, tuple)):
        raise WorkspaceValidationError("evidence_receipts must be ContextReceipt values")
    if not evidence_receipts:
        if anchor.trust is not None and anchor.trust.level == "verified":
            raise WorkspaceValidationError("verified trust requires SDK evidence")
        return anchor
    if anchor.engine_binding is None or anchor.kind != "filesystem":
        raise WorkspaceValidationError("evidence requires a filesystem engine binding")
    refs: List[str] = []
    for evidence in evidence_receipts:
        if not isinstance(evidence, ContextReceipt):
            raise WorkspaceValidationError("evidence_receipts must be ContextReceipt values")
        try:
            verified = evidence.sealed is True and evidence.verify() is True
            source = evidence.source
            link = evidence.receipt_link
        except Exception as exc:
            raise WorkspaceValidationError("evidence receipt is invalid") from exc
        if not verified or source is None or link is None:
            raise WorkspaceValidationError("evidence receipt is not sealed and verified")
        if _plain(source.to_dict()) != _plain(anchor.engine_binding):
            raise WorkspaceConflictError()
        if not _P4_RECEIPT_REF_RE.fullmatch(link.receipt_ref):
            raise WorkspaceValidationError("evidence receipt ref is not P4-bound")
        _validate_anchor_binding(anchor, source)
        refs.append(link.receipt_ref)
    refs = list(_refs(refs, "evidence_receipts", _MAX_ENTRY_REFS))
    return SourceAnchor(
        anchor.source_id,
        anchor.kind,
        anchor.canonical_id,
        anchor.revision,
        anchor.freshness,
        anchor.recovery,
        _verified_source_trust(refs),
        anchor.scope,
        anchor.engine_binding,
    )


def _ensure_external_allowed(state: _WorkspaceState, anchor: SourceAnchor) -> None:
    if state.policy is None:
        raise WorkspaceCorruptError()
    if anchor.kind != "filesystem" and not _state_policy(state).allow_external_sources:
        raise WorkspacePolicyError()


def _ensure_state_bounds(state: _WorkspaceState, policy: Optional[WorkspacePolicy] = None, *, extra_event: bool = False) -> None:
    policy = policy or state.policy
    if policy is None:
        raise WorkspaceCorruptError()
    if len(state.events) + (1 if extra_event else 0) > policy.max_events:
        raise WorkspacePolicyError()
    if len(state.sources) > policy.max_sources:
        raise WorkspacePolicyError()
    if len(state.sessions) > policy.max_sessions:
        raise WorkspacePolicyError()
    if len(state.entries) > policy.max_context_entries:
        raise WorkspacePolicyError()
    if len(state.package_pins) > _MAX_PACKAGE_PINS:
        raise WorkspacePolicyError()
    if any(len(entry.value.encode("utf-8")) > policy.max_entry_bytes for entry in state.entries):
        raise WorkspacePolicyError()
    if _state_context_bytes(state.entries) > policy.max_context_bytes:
        raise WorkspacePolicyError()


def _session_record(session_id: str, task_id: str, source_ids: Sequence[str]) -> Mapping[str, object]:
    return _mapping(
        {
            "session_id": session_id,
            "task_id": task_id,
            "source_ids": list(source_ids),
        }
    )


def _event_payload_keys(kind: str) -> set:
    return {
        "workspace_created": {"identity", "policy"},
        "source_attached": {"anchor"},
        "source_updated": {"anchor", "previous_anchor_digest"},
        "policy_tightened": {"policy", "previous_policy_digest"},
        "session_attached": {"session_id", "task_id", "source_ids"},
        "context_committed": {"entries", "provenance"},
        "checkpoint_created": {"checkpoint"},
        "workspace_restored": {"checkpoint_id", "checkpoint_envelope_digest"},
        "workspace_seeded": {"checkpoint", "package", "admission"},
        "workspace_sealed": {"checkpoint_id", "checkpoint_envelope_digest", "package"},
        "package_pinned": {"pin", "previous_lock_digest"},
        "workspace_forked": {"fork", "inherited_state"},
        "handoff_applied": {"handoff", "admission"},
        "workspace_completed": set(),
        "workspace_aborted": {"reason_code"},
    }[kind]


def _validate_context_provenance(
    state: _WorkspaceState,
    raw_provenance: Any,
    entries: Sequence[ProjectContextEntry],
) -> None:
    if raw_provenance is None:
        if any(
            entry.session_id is not None or entry.receipt_refs or entry.recovery_refs
            for entry in entries
        ):
            raise _ProjectionFailure("incompatible", state)
        return
    try:
        _exact(
            raw_provenance,
            {
                "session_id",
                "task_id",
                "source_ids",
                "receipt_refs",
                "recovery_refs",
                "receipt_proof",
                "receipt_proof_digest",
            },
            "context provenance",
        )
        for field_name in ("source_ids", "receipt_refs", "recovery_refs"):
            if not isinstance(raw_provenance[field_name], list):
                raise WorkspaceIncompatibleError("context provenance arrays are required")
        session_id = _text(raw_provenance["session_id"], "provenance.session_id", 512)
        task_id = _text(raw_provenance["task_id"], "provenance.task_id", 512)
        source_ids = _ids(raw_provenance["source_ids"], "provenance.source_ids", _MAX_SOURCE_IDS)
        receipt_refs = _refs(raw_provenance["receipt_refs"], "provenance.receipt_refs", _MAX_ENTRY_REFS)
        recovery_refs = _refs(
            raw_provenance["recovery_refs"], "provenance.recovery_refs", _MAX_ENTRY_REFS
        )
        receipt_proof = raw_provenance["receipt_proof"]
        _exact(
            receipt_proof,
            {
                "schema_version",
                "session_id",
                "task_id",
                "plan_id",
                "integrity_status",
                "status",
                "source",
                "receipt_link",
                "recovery_ref",
                "output_digest",
            },
            "receipt proof",
        )
        receipt_proof_digest = _digest_value(
            raw_provenance["receipt_proof_digest"], "provenance.receipt_proof_digest"
        )
    except WorkspaceError as exc:
        raise _ProjectionFailure("incompatible", state) from exc
    if not source_ids or not receipt_refs or any(
        not _P4_RECEIPT_REF_RE.fullmatch(ref) for ref in receipt_refs
    ):
        raise _ProjectionFailure("incompatible", state)
    normalized = {
        "session_id": session_id,
        "task_id": task_id,
        "source_ids": list(source_ids),
        "receipt_refs": list(receipt_refs),
        "recovery_refs": list(recovery_refs),
        "receipt_proof": _plain(receipt_proof),
        "receipt_proof_digest": receipt_proof_digest,
    }
    if _plain(normalized) != _plain(raw_provenance):
        raise _ProjectionFailure("incompatible", state)
    durable = [record for record in state.sessions if record["session_id"] == session_id]
    if len(durable) != 1 or durable[0]["task_id"] != task_id:
        raise _ProjectionFailure("incompatible", state)
    durable_source_ids = durable[0].get("source_ids")
    if not isinstance(durable_source_ids, (list, tuple)):
        raise _ProjectionFailure("incompatible", state)
    if not set(source_ids).issubset(durable_source_ids):
        raise _ProjectionFailure("incompatible", state)
    try:
        proof_source = receipt_proof["source"]
        proof_link = receipt_proof["receipt_link"]
        if not isinstance(proof_source, Mapping) or not isinstance(proof_link, Mapping):
            raise WorkspaceIncompatibleError("receipt proof mappings are required")
        _exact(
            proof_link,
            {
                "schema_version",
                "receipt_id",
                "receipt_ref",
                "receipt_digest",
                "invocation_id",
            },
            "receipt link",
        )
        canonical_source = _source_from_binding(proof_source)
        canonical_link = ContextReceiptLink(
            proof_link["schema_version"],
            proof_link["receipt_id"],
            proof_link["receipt_ref"],
            proof_link["receipt_digest"],
            proof_link["invocation_id"],
        )
        plan_id = _ref(receipt_proof["plan_id"], "receipt proof plan_id")
        recovery_ref = _ref(receipt_proof["recovery_ref"], "receipt proof recovery_ref")
        output_digest = _digest_value(
            receipt_proof["output_digest"], "receipt proof output_digest"
        )
        if (
            _digest(receipt_proof) != receipt_proof_digest
            or receipt_proof["schema_version"] != 1
            or receipt_proof["session_id"] != session_id
            or receipt_proof["task_id"] != task_id
            or receipt_proof["integrity_status"] != "sealed"
            or receipt_proof["status"] not in {"succeeded", "degraded"}
            or not plan_id.startswith("plan:sha256:")
            or _plain(canonical_source.to_dict()) != _plain(proof_source)
            or _plain(canonical_link.to_dict()) != _plain(proof_link)
            or canonical_link.receipt_ref != "receipt:" + canonical_link.receipt_digest
            or tuple(receipt_refs) != (canonical_link.receipt_ref,)
            or tuple(recovery_refs) != (recovery_ref,)
            or receipt_proof["output_digest"] != output_digest
        ):
            raise WorkspaceIncompatibleError("receipt proof binding is invalid")
        if not any(
            (
                state.sources[source_id].engine_binding is not None
                and _plain(state.sources[source_id].engine_binding) == _plain(proof_source)
            )
            or (
                state.sources[source_id].engine_binding is None
                and state.sources[source_id].kind == "filesystem"
                and state.sources[source_id].canonical_id
                == "file://" + canonical_source.relative_path
            )
            for source_id in source_ids
        ):
            raise WorkspaceIncompatibleError("receipt proof source is invalid")
    except WorkspaceError as exc:
        raise _ProjectionFailure("incompatible", state) from exc
    except Exception as exc:
        raise _ProjectionFailure("incompatible", state) from exc
    for entry in entries:
        if (
            entry.session_id != session_id
            or tuple(entry.source_ids) != tuple(source_ids)
            or tuple(entry.receipt_refs) != tuple(receipt_refs)
            or tuple(entry.recovery_refs) != tuple(recovery_refs)
        ):
            raise _ProjectionFailure("incompatible", state)


def _apply_event_unchecked(
    state: _WorkspaceState,
    event: Mapping[str, object],
    *,
    replay: bool = False,
    verified_evidence: bool = False,
) -> None:
    if not isinstance(event, Mapping):
        raise _ProjectionFailure("incompatible", state)
    try:
        kind = event["kind"]
        payload = event["payload"]
        event_id = event["event_id"]
        event_digest = event["event_digest"]
        if not isinstance(kind, str) or kind not in _EVENT_KINDS:
            raise _ProjectionFailure("incompatible", state)
        if not isinstance(event_id, str) or not isinstance(event_digest, str):
            raise _ProjectionFailure("incompatible", state)
        if not isinstance(payload, Mapping):
            raise _ProjectionFailure("incompatible", state)
        _reject_sensitive(payload, "payload")
        _exact(payload, _event_payload_keys(kind), f"{kind} payload")
    except _ProjectionFailure:
        raise
    except WorkspaceError as exc:
        raise _ProjectionFailure("incompatible", state) from exc
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _ProjectionFailure("incompatible", state) from exc
    if kind == "workspace_created":
        if state.events or state.identity is not None:
            raise _ProjectionFailure("corrupt", state)
        try:
            identity = WorkspaceIdentity.from_dict(payload["identity"])
            policy = WorkspacePolicy.from_dict(payload["policy"])
        except WorkspaceIncompatibleError:
            raise _ProjectionFailure("incompatible", state)
        except WorkspaceError:
            raise _ProjectionFailure("corrupt", state)
        if (
            _plain(identity.to_dict()) != _plain(payload["identity"])
            or _plain(policy.to_dict()) != _plain(payload["policy"])
        ):
            raise _ProjectionFailure("incompatible", state)
        if identity.workspace_id != event["workspace_id"]:
            raise _ProjectionFailure("corrupt", state)
        state.identity = identity
        state.policy = policy
        state.lifecycle = "active"
    else:
        if state.identity is None or state.policy is None or not state.events:
            raise _ProjectionFailure("corrupt", state)
        if state.lifecycle != "active":
            raise _ProjectionFailure("incompatible", state)
    if kind in {"source_attached", "source_updated"}:
        try:
            anchor = _source_anchor_from_dict(
                payload["anchor"], allow_verified=replay or verified_evidence
            )
        except WorkspaceIncompatibleError:
            raise _ProjectionFailure("incompatible", state)
        except WorkspaceError:
            raise _ProjectionFailure("incompatible", state)
        try:
            _reject_sensitive(anchor.to_dict(), "anchor")
        except WorkspaceError:
            raise _ProjectionFailure("incompatible", state)
        if _plain(anchor.to_dict()) != _plain(payload["anchor"]):
            raise _ProjectionFailure("incompatible", state)
        if kind == "source_attached":
            if anchor.source_id in state.sources:
                raise _ProjectionFailure("corrupt", state)
            if len(state.sources) + 1 > _state_policy(state).max_sources:
                raise _ProjectionFailure("incompatible", state)
        else:
            if anchor.source_id not in state.sources:
                raise _ProjectionFailure("corrupt", state)
            try:
                previous_digest = _digest_value(payload["previous_anchor_digest"], "previous_anchor_digest")
            except WorkspaceError:
                raise _ProjectionFailure("incompatible", state)
            if previous_digest != _anchor_digest(state.sources[anchor.source_id]):
                raise _ProjectionFailure("corrupt", state)
        try:
            _ensure_external_allowed(state, anchor)
        except WorkspacePolicyError:
            raise _ProjectionFailure("incompatible", state)
        state.sources[anchor.source_id] = anchor
    elif kind == "policy_tightened":
        try:
            policy = WorkspacePolicy.from_dict(payload["policy"])
            previous = _digest_value(payload["previous_policy_digest"], "previous_policy_digest")
        except WorkspaceError:
            raise _ProjectionFailure("incompatible", state)
        if _plain(policy.to_dict()) != _plain(payload["policy"]):
            raise _ProjectionFailure("incompatible", state)
        if previous != _digest(_state_policy(state).to_dict()):
            raise _ProjectionFailure("corrupt", state)
        if not policy.is_tightening(state.policy):
            raise _ProjectionFailure("incompatible", state)
        if any(
            entry.category not in policy.allowed_categories for entry in state.entries
        ):
            raise _ProjectionFailure("incompatible", state)
        if not policy.allow_external_sources and any(
            anchor.kind != "filesystem" for anchor in state.sources.values()
        ):
            raise _ProjectionFailure("incompatible", state)
        candidate = state.clone()
        candidate.policy = policy
        try:
            _ensure_state_bounds(candidate, policy, extra_event=True)
        except WorkspacePolicyError:
            raise _ProjectionFailure("incompatible", state)
        state.policy = policy
    elif kind == "session_attached":
        try:
            session_id = _text(payload["session_id"], "session_id", 512)
            task_id = _text(payload["task_id"], "task_id", 512)
            source_ids = _ids(payload["source_ids"], "source_ids", _MAX_SOURCE_IDS)
        except WorkspaceError:
            raise _ProjectionFailure("incompatible", state)
        if list(source_ids) != payload["source_ids"] or not source_ids:
            raise _ProjectionFailure("incompatible", state)
        if session_id in {session["session_id"] for session in state.sessions}:
            raise _ProjectionFailure("corrupt", state)
        if any(source_id not in state.sources for source_id in source_ids):
            raise _ProjectionFailure("corrupt", state)
        if len(state.sessions) + 1 > _state_policy(state).max_sessions:
            raise _ProjectionFailure("incompatible", state)
        state.sessions.append(_session_record(session_id, task_id, source_ids))
    elif kind == "context_committed":
        raw_entries = payload["entries"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise _ProjectionFailure("incompatible", state)
        try:
            entries = [_entry_from_input(item) for item in raw_entries]
        except WorkspaceError:
            raise _ProjectionFailure("incompatible", state)
        if any(_plain(entry.to_dict()) != _plain(raw) for entry, raw in zip(entries, raw_entries)):
            raise _ProjectionFailure("incompatible", state)
        _validate_context_provenance(state, payload["provenance"], entries)
        existing_ids = {entry.entry_id for entry in state.entries}
        if any(entry.entry_id in existing_ids for entry in entries):
            raise _ProjectionFailure("corrupt", state)
        sessions = {session["session_id"] for session in state.sessions}
        for entry in entries:
            if any(source_id not in state.sources for source_id in entry.source_ids):
                raise _ProjectionFailure("corrupt", state)
            if entry.session_id is not None and entry.session_id not in sessions:
                raise _ProjectionFailure("corrupt", state)
            if entry.category not in _state_policy(state).allowed_categories:
                raise _ProjectionFailure("incompatible", state)
            if len(entry.value.encode("utf-8")) > _state_policy(state).max_entry_bytes:
                raise _ProjectionFailure("incompatible", state)
        candidate = state.clone()
        candidate.entries.extend(entries)
        try:
            _ensure_state_bounds(candidate, extra_event=True)
        except WorkspacePolicyError:
            raise _ProjectionFailure("incompatible", state)
        state.entries.extend(entries)
    elif kind == "workspace_forked":
        from .parallel_context import WorkspaceForkV1

        if len(state.events) != 1 or state.fork_lineage is not None:
            raise _ProjectionFailure("corrupt", state)
        if state.sources or state.entries or state.sessions or state.package_pins:
            raise _ProjectionFailure("corrupt", state)
        try:
            fork = WorkspaceForkV1.from_dict(payload["fork"])
            inherited_state = _plain(payload["inherited_state"])
            logical_state = dict(
                inherited_state,
                workspace_id=fork.source.workspace_id,
            )
            parent_policy, sources, entries, package_pins, lock_digest = (
                _logical_state_components(logical_state, fork.source.workspace_id)
            )
            sources = {
                source_id: SourceAnchor(
                    anchor.source_id,
                    anchor.kind,
                    anchor.canonical_id,
                    anchor.revision,
                    anchor.freshness,
                    anchor.recovery,
                    anchor.trust,
                    anchor.scope,
                    None,
                )
                for source_id, anchor in sources.items()
            }
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        if (
            fork.child_workspace_id != state.identity.workspace_id
            or fork.lineage.fork_event_ref != "event-id:" + event_id
            or fork.policy_inheritance.parent_policy != parent_policy
            or fork.policy_inheritance.effective_child_policy != state.policy
            or fork.package_lock_digest != lock_digest
            or fork.inherited_content_digest
            != _domain_digest("leanctx.fork.inherited-content.v1", inherited_state)
        ):
            raise _ProjectionFailure("corrupt", state)
        candidate = state.clone()
        candidate.sources = dict(sources)
        candidate.entries = list(entries)
        candidate.package_pins = dict(package_pins)
        candidate.package_lock_digest = lock_digest
        candidate.fork_lineage = _plain(fork.lineage.to_dict())
        try:
            _ensure_state_bounds(candidate, extra_event=True)
        except WorkspacePolicyError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        state.sources = dict(sources)
        state.entries = list(entries)
        state.package_pins = dict(package_pins)
        state.package_lock_digest = lock_digest
        state.fork_lineage = _plain(fork.lineage.to_dict())
    elif kind == "handoff_applied":
        from .parallel_context import ContextHandoffV1, ForkLineageV1, HandoffAdmissionV1

        try:
            handoff = ContextHandoffV1.from_dict(payload["handoff"])
            admission = HandoffAdmissionV1.from_dict(payload["admission"])
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        if (
            admission.handoff_id != handoff.handoff_id
            or admission.handoff_digest != handoff.handoff_digest
            or admission.receiver_workspace_id != state.identity.workspace_id
            or admission.decision not in {"admitted", "degraded"}
            or handoff.target_workspace_id != state.identity.workspace_id
        ):
            raise _ProjectionFailure("corrupt", state)
        receiver_lineage = (
            ForkLineageV1.from_dict(state.fork_lineage)
            if state.fork_lineage is not None
            else None
        )
        source_lineage = handoff.source_lineage
        lineage_ok = False
        if handoff.source.workspace_id == state.identity.workspace_id:
            source_checkpoint = state.checkpoints.get(handoff.source.checkpoint_id)
            lineage_ok = bool(
                source_checkpoint is not None
                and source_checkpoint.state_digest == handoff.source.state_digest
                and source_checkpoint.envelope_digest
                == handoff.source.checkpoint_envelope_digest
                and source_lineage == receiver_lineage
            )
        elif source_lineage is None and receiver_lineage is not None:
            lineage_ok = (
                handoff.source.workspace_id == receiver_lineage.parent_workspace_id
                and handoff.source.checkpoint_id
                == receiver_lineage.parent_checkpoint_id
                and handoff.source.state_digest
                == receiver_lineage.parent_checkpoint_state_digest
                and handoff.source.checkpoint_envelope_digest
                == receiver_lineage.parent_checkpoint_envelope_digest
            )
        elif source_lineage is not None and receiver_lineage is not None:
            lineage_ok = (
                source_lineage.parent_workspace_id
                == receiver_lineage.parent_workspace_id
                and source_lineage.parent_checkpoint_id
                == receiver_lineage.parent_checkpoint_id
                and source_lineage.parent_checkpoint_state_digest
                == receiver_lineage.parent_checkpoint_state_digest
                and source_lineage.parent_checkpoint_envelope_digest
                == receiver_lineage.parent_checkpoint_envelope_digest
                and source_lineage.child_workspace_id
                != receiver_lineage.child_workspace_id
                and source_lineage.fork_id != receiver_lineage.fork_id
                and source_lineage.fork_event_ref != receiver_lineage.fork_event_ref
            )
        if (
            not lineage_ok
            or admission.lineage_result != "verified"
            or admission.target_result != "match"
            or admission.policy_result != "monotonic"
            or admission.package_result != "exact"
            or admission.conflicts.entries
            or not _state_policy(state).is_tightening(handoff.required_policy_floor)
        ):
            raise _ProjectionFailure("corrupt", state)
        previous_handoff = state.applied_handoffs.get(handoff.handoff_id)
        record: Mapping[str, str] = {
            "handoff_id": handoff.handoff_id,
            "handoff_digest": handoff.handoff_digest,
            "event_id": event_id,
        }
        if previous_handoff is not None:
            if previous_handoff != record:
                raise _ProjectionFailure("corrupt", state)
            raise _ProjectionFailure("incompatible", state)
        imported_sources = {}
        for raw_anchor in handoff.source_anchors:
            try:
                anchor = _source_anchor_from_dict(_plain(raw_anchor), allow_verified=True)
            except WorkspaceError as exc:
                raise _ProjectionFailure("incompatible", state) from exc
            existing_anchor = state.sources.get(anchor.source_id)
            if existing_anchor is None:
                if admission.decision != "degraded":
                    raise _ProjectionFailure("corrupt", state)
                imported_sources[anchor.source_id] = anchor
            elif _plain(existing_anchor.to_dict()) != _plain(anchor.to_dict()):
                raise _ProjectionFailure("corrupt", state)
        if imported_sources or admission.source_result == "unavailable":
            if admission.decision != "degraded" or admission.source_result != "unavailable":
                raise _ProjectionFailure("corrupt", state)
        elif admission.decision != "admitted" or admission.source_result != "available":
            raise _ProjectionFailure("corrupt", state)
        for raw_pin in handoff.package_refs:
            try:
                pin = PackagePin.from_dict(_plain(raw_pin))
            except WorkspaceError as exc:
                raise _ProjectionFailure("incompatible", state) from exc
            existing_pin = state.package_pins.get((pin.name, pin.version))
            if existing_pin is None or _plain(existing_pin.to_dict()) != _plain(pin.to_dict()):
                raise _ProjectionFailure("corrupt", state)
        existing_by_id = {entry.entry_id: entry for entry in state.entries}
        appended = []
        for raw_entry in handoff.selected_entries:
            try:
                entry = ProjectContextEntry.from_dict(_plain(raw_entry))
            except WorkspaceError as exc:
                raise _ProjectionFailure("incompatible", state) from exc
            existing_entry = existing_by_id.get(entry.entry_id)
            if existing_entry is not None:
                if _plain(existing_entry.to_dict()) != _plain(entry.to_dict()):
                    raise _ProjectionFailure("corrupt", state)
                continue
            if entry.category not in _state_policy(state).allowed_categories:
                raise _ProjectionFailure("incompatible", state)
            if any(
                source_id not in state.sources and source_id not in imported_sources
                for source_id in entry.source_ids
            ):
                raise _ProjectionFailure("corrupt", state)
            appended.append(entry)
            existing_by_id[entry.entry_id] = entry
        candidate = state.clone()
        candidate.sources.update(imported_sources)
        candidate.entries.extend(appended)
        candidate.applied_handoffs[handoff.handoff_id] = record
        try:
            for anchor in imported_sources.values():
                _ensure_external_allowed(candidate, anchor)
            _ensure_state_bounds(candidate, extra_event=True)
        except WorkspacePolicyError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        state.sources.update(imported_sources)
        state.entries.extend(appended)
        state.applied_handoffs[handoff.handoff_id] = record
    elif kind == "package_pinned":
        try:
            pin = PackagePin.from_dict(payload["pin"])
            previous_lock_digest = payload["previous_lock_digest"]
            if previous_lock_digest is not None:
                previous_lock_digest = _digest_value(
                    previous_lock_digest, "previous package lock digest"
                )
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        if previous_lock_digest != state.package_lock_digest:
            raise _ProjectionFailure("corrupt", state)
        key = (pin.name, pin.version)
        existing = state.package_pins.get(key)
        if existing is not None:
            if _plain(existing.to_dict()) != _plain(pin.to_dict()):
                raise _ProjectionFailure("corrupt", state)
            raise _ProjectionFailure("incompatible", state)
        if any(
            existing_pin.name == pin.name
            and existing_pin.artifact_digest != pin.artifact_digest
            for existing_pin in state.package_pins.values()
        ):
            raise _ProjectionFailure("corrupt", state)
        if len(state.package_pins) + 1 > _MAX_PACKAGE_PINS:
            raise _ProjectionFailure("incompatible", state)
        state.package_pins[key] = pin
        state.package_lock_digest = _package_lock(tuple(state.package_pins.values()))
    elif kind == "checkpoint_created":
        try:
            checkpoint = ContextCheckpointV2.from_dict(payload["checkpoint"])
            policy, sources, entries, _, _ = _logical_state_components(
                checkpoint.logical_state, _state_identity(state).workspace_id
            )
        except WorkspaceConflictError as exc:
            raise _ProjectionFailure("corrupt", state) from exc
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        if checkpoint.checkpoint_id in state.checkpoints:
            raise _ProjectionFailure("corrupt", state)
        if len(state.checkpoints) + 1 > _MAX_CHECKPOINTS:
            raise _ProjectionFailure("incompatible", state)
        if checkpoint.workspace_id != _state_identity(state).workspace_id:
            raise _ProjectionFailure("corrupt", state)
        if checkpoint.lineage["state_id"] != _state_identity(state).state_id:
            raise _ProjectionFailure("corrupt", state)
        if checkpoint.workspace_state_ref != "event:" + state.last_event_digest:
            raise _ProjectionFailure("corrupt", state)
        if checkpoint.state_digest != state.logical_digest():
            raise _ProjectionFailure("corrupt", state)
        if _plain(checkpoint.logical_state) != _plain(state.logical_state()):
            raise _ProjectionFailure("corrupt", state)
        if [_plain(anchor.to_dict()) for anchor in sources.values()] != [
            _plain(anchor) for anchor in checkpoint.source_anchors
        ]:
            raise _ProjectionFailure("corrupt", state)
        recovery_refs = tuple(
            sorted({ref for entry in entries for ref in entry.recovery_refs})
        )
        if recovery_refs != checkpoint.recovery_refs:
            raise _ProjectionFailure("corrupt", state)
        if checkpoint.policy_digest != _domain_digest(
            "leanctx.workspace.policy.v1", policy.to_dict()
        ):
            raise _ProjectionFailure("corrupt", state)
        if checkpoint.project_context_digest != _domain_digest(
            "leanctx.project-context.state.v1",
            [entry.to_dict() for entry in entries],
        ):
            raise _ProjectionFailure("corrupt", state)
        try:
            _verify_checkpoint_trust(state, sources)
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        state.checkpoints[checkpoint.checkpoint_id] = checkpoint
    elif kind == "workspace_restored":
        try:
            checkpoint_id = _uuid(payload["checkpoint_id"], "checkpoint_id")
            envelope_digest = _digest_value(
                payload["checkpoint_envelope_digest"],
                "checkpoint_envelope_digest",
            )
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        restored_checkpoint = state.checkpoints.get(checkpoint_id)
        if restored_checkpoint is None or restored_checkpoint.envelope_digest != envelope_digest:
            raise _ProjectionFailure("corrupt", state)
        try:
            (
                target_policy,
                target_sources,
                target_entries,
                target_package_pins,
                target_package_lock_digest,
            ) = _logical_state_components(
                restored_checkpoint.logical_state,
                _state_identity(state).workspace_id,
            )
        except WorkspaceConflictError as exc:
            raise _ProjectionFailure("corrupt", state) from exc
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        if not target_policy.is_tightening(state.policy):
            raise _ProjectionFailure("incompatible", state)
        candidate = state.clone()
        candidate.policy = target_policy
        candidate.sources = target_sources
        candidate.entries = target_entries
        candidate.package_pins = target_package_pins
        candidate.package_lock_digest = target_package_lock_digest
        try:
            _ensure_state_bounds(candidate, target_policy, extra_event=True)
            for anchor in target_sources.values():
                _ensure_external_allowed(candidate, anchor)
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        state.policy = target_policy
        state.sources = target_sources
        state.entries = target_entries
        state.package_pins = target_package_pins
        state.package_lock_digest = target_package_lock_digest
    elif kind == "workspace_seeded":
        if state.sources or state.entries or state.checkpoints or len(state.events) != 1:
            raise _ProjectionFailure("incompatible", state)
        try:
            checkpoint = ContextCheckpointV2.from_dict(payload["checkpoint"])
            package = payload["package"]
            admission = payload["admission"]
            _exact(
                package,
                {
                    "name",
                    "version",
                    "package_digest",
                    "content_hash",
                    "signature_state",
                    "signer_public_key",
                },
                "seed package",
            )
            _exact(admission, {"trusted_signer", "allow_unsigned"}, "seed admission")
            for name in ("package_digest", "content_hash"):
                _digest_value(package[name], f"seed package {name}")
            if package["signature_state"] not in {"signed_valid", "unsigned"}:
                raise WorkspaceIncompatibleError("seed signature state is unsupported")
            if not isinstance(admission["trusted_signer"], bool) or not isinstance(
                admission["allow_unsigned"], bool
            ):
                raise WorkspaceIncompatibleError("seed admission flags are invalid")
            if package["signature_state"] == "signed_valid" and not admission["trusted_signer"]:
                raise WorkspacePolicyError()
            if package["signature_state"] == "unsigned" and not admission["allow_unsigned"]:
                raise WorkspacePolicyError()
            (
                target_policy,
                target_sources,
                target_entries,
                target_package_pins,
                target_package_lock_digest,
            ) = _logical_state_components(
                checkpoint.logical_state,
                _state_identity(state).workspace_id,
            )
        except WorkspaceConflictError as exc:
            raise _ProjectionFailure("corrupt", state) from exc
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        if (
            checkpoint.workspace_id != _state_identity(state).workspace_id
            or _plain(target_policy.to_dict()) != _plain(_state_policy(state).to_dict())
            or checkpoint.state_digest
            != _domain_digest("leanctx.workspace.state.v1", checkpoint.logical_state)
        ):
            raise _ProjectionFailure("corrupt", state)
        if any(
            anchor.trust is not None and anchor.trust.level == "verified"
            for anchor in target_sources.values()
        ) and not (
            package["signature_state"] == "signed_valid" and admission["trusted_signer"]
        ):
            raise _ProjectionFailure("incompatible", state)
        candidate = state.clone()
        candidate.sources = target_sources
        candidate.entries = target_entries
        candidate.package_pins = target_package_pins
        candidate.package_lock_digest = target_package_lock_digest
        candidate.checkpoints[checkpoint.checkpoint_id] = checkpoint
        try:
            _ensure_state_bounds(candidate, target_policy, extra_event=True)
            for anchor in target_sources.values():
                _ensure_external_allowed(candidate, anchor)
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        state.sources = target_sources
        state.entries = target_entries
        state.package_pins = target_package_pins
        state.package_lock_digest = target_package_lock_digest
        state.checkpoints[checkpoint.checkpoint_id] = checkpoint
    elif kind == "workspace_sealed":
        try:
            checkpoint_id = _uuid(payload["checkpoint_id"], "checkpoint_id")
            envelope_digest = _digest_value(
                payload["checkpoint_envelope_digest"],
                "checkpoint_envelope_digest",
            )
            package = payload["package"]
            _exact(
                package,
                {
                    "name",
                    "version",
                    "package_digest",
                    "content_hash",
                    "signature_state",
                    "signer_public_key",
                },
                "sealed package",
            )
            _digest_value(package["package_digest"], "package_digest")
            _digest_value(package["content_hash"], "content_hash")
        except WorkspaceError as exc:
            raise _ProjectionFailure("incompatible", state) from exc
        sealed_checkpoint = state.checkpoints.get(checkpoint_id)
        if sealed_checkpoint is None or sealed_checkpoint.envelope_digest != envelope_digest:
            raise _ProjectionFailure("corrupt", state)
    elif kind == "workspace_completed":
        state.lifecycle = "completed"
    elif kind == "workspace_aborted":
        try:
            _text(payload["reason_code"], "reason_code", 128)
        except WorkspaceError:
            raise _ProjectionFailure("incompatible", state)
        state.lifecycle = "aborted"
    state.events.append(event)
    state.last_event_digest = event_digest
    if state.identity is None or state.policy is None:
        raise _ProjectionFailure("corrupt", state)
    try:
        _ensure_state_bounds(state, extra_event=False)
    except WorkspacePolicyError:
        raise _ProjectionFailure("incompatible", state)
    receipt = _receipt_for_event(state, event)
    if event_id in state.receipts:
        raise _ProjectionFailure("corrupt", state)
    state.receipts[event_id] = receipt


def _apply_event(
    state: _WorkspaceState,
    event: Mapping[str, object],
    *,
    replay: bool = False,
    verified_evidence: bool = False,
) -> None:
    try:
        _apply_event_unchecked(
            state,
            event,
            replay=replay,
            verified_evidence=verified_evidence,
        )
    except _ProjectionFailure:
        raise
    except WorkspaceError as exc:
        raise _ProjectionFailure("incompatible", state) from exc
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _ProjectionFailure("incompatible", state) from exc


def _receipt_for_event(state: _WorkspaceState, event: Mapping[str, object]) -> WorkspaceReceipt:
    payload = event.get("payload")
    kind = event.get("kind")
    identity = state.identity
    if identity is None:
        raise WorkspaceCorruptError()
    if not isinstance(payload, Mapping) or not isinstance(kind, str):
        raise WorkspaceIncompatibleError("event fields are invalid")
    source_ids: Tuple[str, ...] = ()
    session_id = None
    engine_refs: Tuple[str, ...] = ()
    recovery_refs: Tuple[str, ...] = ()
    if kind in {"source_attached", "source_updated"}:
        anchor = payload.get("anchor")
        source_id = anchor.get("source_id") if isinstance(anchor, Mapping) else None
        if not isinstance(source_id, str):
            raise WorkspaceIncompatibleError("event anchor source_id is invalid")
        source_ids = (source_id,)
    elif kind == "session_attached":
        raw_source_ids = payload.get("source_ids")
        raw_session_id = payload.get("session_id")
        if not isinstance(raw_source_ids, (list, tuple)) or not all(
            isinstance(source_id, str) for source_id in raw_source_ids
        ):
            raise WorkspaceIncompatibleError("event source_ids are invalid")
        if not isinstance(raw_session_id, str):
            raise WorkspaceIncompatibleError("event session_id is invalid")
        source_ids = tuple(raw_source_ids)
        session_id = raw_session_id
    elif kind == "context_committed":
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, (list, tuple)):
            raise WorkspaceIncompatibleError("event entries are invalid")
        entries = [_entry_from_input(item) for item in raw_entries]
        source_ids = tuple(sorted({source_id for entry in entries for source_id in entry.source_ids}))
        sessions = {entry.session_id for entry in entries if entry.session_id is not None}
        session_id = next(iter(sessions)) if len(sessions) == 1 else None
        engine_refs = tuple(sorted({ref for entry in entries for ref in entry.receipt_refs}))
        recovery_refs = tuple(sorted({ref for entry in entries for ref in entry.recovery_refs}))
    return WorkspaceReceipt(
        identity.workspace_id,
        identity.state_id,
        _positive_int(event.get("sequence"), "event sequence"),
        _uuid(event.get("event_id"), "event_id"),
        kind,
        _digest_value(event.get("event_digest"), "event_digest"),
        state.digest(),
        source_ids,
        session_id,
        engine_refs,
        recovery_refs,
    )


def _safe_lstat(path: str, *, error_kind: str) -> os.stat_result:
    try:
        result = os.lstat(path)
    except FileNotFoundError as exc:
        raise _ProjectionFailure(error_kind) from exc
    except OSError as exc:
        raise _ProjectionFailure("io") from exc
    return result


def _read_regular_file(path: str, *, directory_fd: Optional[int] = None) -> bytes:
    try:
        before_path = os.stat(path, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
            raise _ProjectionFailure("corrupt")
        if before_path.st_nlink not in (1, 2) or stat.S_IMODE(before_path.st_mode) & 0o077:
            raise _ProjectionFailure("corrupt")
        flags = os.O_RDONLY
        if not hasattr(os, "O_NOFOLLOW"):
            raise _ProjectionFailure("corrupt")
        flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, dir_fd=directory_fd)
        try:
            opened = os.fstat(fd)
            if (
                opened.st_dev != before_path.st_dev
                or opened.st_ino != before_path.st_ino
                or opened.st_nlink not in (1, 2)
                or not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) & 0o077
            ):
                raise _ProjectionFailure("corrupt")
            with os.fdopen(fd, "rb", closefd=True) as stream:
                fd = -1
                data = stream.read(_MAX_EVENT_BYTES + 1)
                after = os.fstat(stream.fileno())
            if len(data) > _MAX_EVENT_BYTES or (
                after.st_dev != opened.st_dev
                or after.st_ino != opened.st_ino
                or after.st_nlink not in (1, 2)
            ):
                raise _ProjectionFailure("corrupt")
            return data
        finally:
            if fd != -1:
                os.close(fd)
    except _ProjectionFailure:
        raise
    except OSError as exc:
        raise _ProjectionFailure("io") from exc


def _read_event(
    path: str,
    workspace_id: str,
    expected_sequence: int,
    previous_digest: str,
    *,
    directory_fd: Optional[int] = None,
) -> Mapping[str, object]:
    match = _EVENT_FILE_RE.fullmatch(os.path.basename(path))
    if match is None:
        raise _ProjectionFailure("corrupt")
    filename_sequence = int(match.group(1))
    filename_digest = match.group(2)
    if filename_sequence != expected_sequence:
        raise _ProjectionFailure("corrupt")
    data = _read_regular_file(os.path.basename(path), directory_fd=directory_fd)
    if not data.endswith(b"\n") or data[:-1].endswith(b"\n"):
        raise _ProjectionFailure("corrupt")
    try:
        value = strict_json_loads(data[:-1], label="workspace event")
    except Exception as exc:
        raise _ProjectionFailure("corrupt") from exc
    expected_keys = {
        "schema_version",
        "workspace_id",
        "sequence",
        "event_id",
        "kind",
        "payload",
        "previous_digest",
        "event_digest",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise _ProjectionFailure("incompatible")
    try:
        if value["schema_version"] != _EVENT_SCHEMA:
            raise _ProjectionFailure("incompatible")
        if _canonical(value) + b"\n" != data:
            raise _ProjectionFailure("corrupt")
        if _uuid(value["workspace_id"], "workspace_id") != workspace_id:
            raise _ProjectionFailure("corrupt")
        if isinstance(value["sequence"], bool) or not isinstance(value["sequence"], int):
            raise _ProjectionFailure("incompatible")
        if value["sequence"] != expected_sequence:
            raise _ProjectionFailure("incompatible")
        _uuid(value["event_id"], "event_id")
        if not isinstance(value["kind"], str) or value["kind"] not in _EVENT_KINDS:
            raise _ProjectionFailure("incompatible")
        if not isinstance(value["payload"], Mapping):
            raise _ProjectionFailure("incompatible")
        _reject_sensitive(value["payload"], "payload")
        if expected_sequence == 1:
            if value["previous_digest"] != _ZERO_DIGEST:
                raise _ProjectionFailure("corrupt")
        else:
            _digest_value(value["previous_digest"], "previous_digest")
        _digest_value(value["event_digest"], "event_digest")
        if value["previous_digest"] != previous_digest:
            raise _ProjectionFailure("corrupt")
        event_digest = _digest({key: value[key] for key in expected_keys if key != "event_digest"})
        if value["event_digest"] != event_digest or event_digest.removeprefix("sha256:") != filename_digest:
            raise _ProjectionFailure("corrupt")
    except _ProjectionFailure:
        raise
    except WorkspaceError as exc:
        raise _ProjectionFailure("incompatible") from exc
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _ProjectionFailure("incompatible") from exc
    return value


def _events_path(workspace_path: str) -> str:
    path = os.path.join(workspace_path, "events")
    try:
        item = os.lstat(path)
    except OSError as exc:
        raise _ProjectionFailure("corrupt") from exc
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
        raise _ProjectionFailure("corrupt")
    return path


def _open_directory_fd(
    path: str,
    *,
    parent_fd: Optional[int] = None,
    failure: str = "corrupt",
) -> Tuple[int, os.stat_result]:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise _ProjectionFailure("io")
    if parent_fd is None:
        try:
            return _open_directory_ancestry(path)
        except WorkspaceNotFoundError as exc:
            raise _ProjectionFailure("not_found") from exc
        except WorkspaceError as exc:
            raise _ProjectionFailure(failure) from exc
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, dir_fd=parent_fd)
        item = os.fstat(fd)
        if not stat.S_ISDIR(item.st_mode) or stat.S_IMODE(item.st_mode) & 0o077:
            os.close(fd)
            raise _ProjectionFailure(failure)
        return fd, item
    except _ProjectionFailure:
        raise
    except FileNotFoundError as exc:
        raise _ProjectionFailure("not_found") from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _ProjectionFailure(failure) from exc
        raise _ProjectionFailure("io") from exc


def _open_directory_ancestry(path: str) -> Tuple[int, os.stat_result]:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise WorkspaceIOError()
    try:
        absolute = os.path.abspath(path)
        fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in absolute.split(os.sep):
                if not component:
                    continue
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fd,
                )
                os.close(fd)
                fd = child
            item = os.fstat(fd)
            if not stat.S_ISDIR(item.st_mode):
                raise WorkspaceIOError()
            return fd, item
        except Exception:
            os.close(fd)
            raise
    except WorkspaceError:
        raise
    except FileNotFoundError as exc:
        raise WorkspaceNotFoundError() from exc
    except OSError as exc:
        raise WorkspaceIOError() from exc


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _enforce_owner_only(path: str, mode: int) -> None:
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode):
            raise WorkspaceIOError()
        os.chmod(path, mode)
        after = os.lstat(path)
        if stat.S_ISLNK(after.st_mode) or stat.S_IMODE(after.st_mode) & 0o077:
            raise WorkspaceIOError()
    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceIOError() from exc


@dataclass(frozen=True)
class _WorkspaceLockHandle:
    workspace_fd: int
    workspace_stat: os.stat_result
    events_fd: int
    events_stat: os.stat_result


def _project(
    workspace_path: str,
    workspace_id: str,
    *,
    workspace_directory_fd: Optional[int] = None,
    events_directory_fd: Optional[int] = None,
) -> _WorkspaceState:
    if workspace_directory_fd is None:
        workspace_fd, workspace_stat = _open_directory_fd(workspace_path, failure="corrupt")
    else:
        try:
            workspace_fd = os.dup(workspace_directory_fd)
            workspace_stat = os.fstat(workspace_fd)
            if not stat.S_ISDIR(workspace_stat.st_mode):
                os.close(workspace_fd)
                raise _ProjectionFailure("corrupt")
        except _ProjectionFailure:
            raise
        except OSError as exc:
            raise _ProjectionFailure("io") from exc
    events_fd = -1
    events_stat = None
    try:
        root_names = os.listdir(workspace_fd)
        if any(name not in {"events", ".workspace.lock"} for name in root_names):
            raise _ProjectionFailure("corrupt")
        if events_directory_fd is None:
            events_fd, events_stat = _open_directory_fd(
                "events", parent_fd=workspace_fd, failure="corrupt"
            )
        else:
            events_fd = os.dup(events_directory_fd)
            events_stat = os.fstat(events_fd)
            if not stat.S_ISDIR(events_stat.st_mode):
                raise _ProjectionFailure("corrupt")
        try:
            names = os.listdir(events_fd)
        except OSError as exc:
            raise _ProjectionFailure("io") from exc
        published = []
        temporary_names = []
        for name in names:
            if name == ".workspace.lock":
                try:
                    temporary = os.stat(name, dir_fd=events_fd, follow_symlinks=False)
                except OSError as exc:
                    raise _ProjectionFailure("io") from exc
                if (
                    stat.S_ISLNK(temporary.st_mode)
                    or not stat.S_ISREG(temporary.st_mode)
                    or temporary.st_nlink != 1
                    or stat.S_IMODE(temporary.st_mode) & 0o077
                ):
                    raise _ProjectionFailure("corrupt")
                continue
            if name.startswith(".event-"):
                try:
                    temporary = os.stat(name, dir_fd=events_fd, follow_symlinks=False)
                except OSError as exc:
                    raise _ProjectionFailure("io") from exc
                if (
                    stat.S_ISLNK(temporary.st_mode)
                    or not stat.S_ISREG(temporary.st_mode)
                    or temporary.st_nlink not in (1, 2)
                    or stat.S_IMODE(temporary.st_mode) & 0o077
                ):
                    raise _ProjectionFailure("corrupt")
                temporary_names.append(name)
                continue
            if name.startswith("."):
                raise _ProjectionFailure("corrupt")
            if not name.endswith(".json") or _EVENT_FILE_RE.fullmatch(name) is None:
                raise _ProjectionFailure("corrupt")
            published.append(name)
        published.sort()
        state = _WorkspaceState()
        previous_digest = _ZERO_DIGEST
        expected_sequence = 1
        for name in published:
            try:
                event = _read_event(
                    name,
                    workspace_id,
                    expected_sequence,
                    previous_digest,
                    directory_fd=events_fd,
                )
                _apply_event(event=event, state=state, replay=True)
            except _ProjectionFailure as exc:
                if exc.state is None:
                    exc.state = state
                raise
            event_digest = event.get("event_digest")
            if not isinstance(event_digest, str):
                raise _ProjectionFailure("incompatible", state)
            previous_digest = event_digest
            expected_sequence += 1
        final_inodes = []
        for name in published:
            try:
                item = os.stat(name, dir_fd=events_fd, follow_symlinks=False)
            except OSError as exc:
                raise _ProjectionFailure("io") from exc
            if item.st_nlink == 2:
                final_inodes.append((name, item))
            elif item.st_nlink != 1:
                raise _ProjectionFailure("corrupt")
        for name in temporary_names:
            try:
                item = os.stat(name, dir_fd=events_fd, follow_symlinks=False)
            except OSError as exc:
                raise _ProjectionFailure("io") from exc
            if item.st_nlink == 1:
                continue
            if item.st_nlink != 2:
                raise _ProjectionFailure("corrupt")
            matches = [
                final for final, final_item in final_inodes if _same_inode(item, final_item)
            ]
            if len(matches) != 1:
                raise _ProjectionFailure("corrupt")
        for final, final_item in final_inodes:
            matches = []
            for name in temporary_names:
                try:
                    temporary = os.stat(name, dir_fd=events_fd, follow_symlinks=False)
                except OSError as exc:
                    raise _ProjectionFailure("io") from exc
                if _same_inode(final_item, temporary):
                    matches.append(name)
            if len(matches) != 1:
                raise _ProjectionFailure("corrupt")
        try:
            if not _same_inode(workspace_stat, os.fstat(workspace_fd)) or not _same_inode(
                events_stat, os.fstat(events_fd)
            ):
                raise _ProjectionFailure("corrupt")
        except OSError as exc:
            raise _ProjectionFailure("io") from exc
        if not state.events or state.identity is None or state.policy is None:
            raise _ProjectionFailure("corrupt", state)
        if state.identity.workspace_id != workspace_id:
            raise _ProjectionFailure("corrupt", state)
        return state
    finally:
        if events_fd != -1:
            os.close(events_fd)
        os.close(workspace_fd)


def _canonical_root(state_root: Any, *, create: bool) -> str:
    try:
        raw = os.fspath(state_root)
    except (TypeError, ValueError) as exc:
        raise WorkspaceValidationError("state_root must be a filesystem path") from exc
    if isinstance(raw, bytes):
        raise WorkspaceValidationError("state_root must be a text path")
    try:
        absolute = os.path.abspath(raw)
    except (TypeError, ValueError, OSError) as exc:
        raise WorkspaceValidationError("state_root is invalid") from exc
    if "\x00" in absolute:
        raise WorkspaceValidationError("state_root is invalid")
    if os.path.lexists(absolute):
        item = os.lstat(absolute)
        if stat.S_ISLNK(item.st_mode):
            if create:
                raise WorkspaceValidationError("state_root must not be a symlink")
            raise WorkspaceCorruptError()
        if not stat.S_ISDIR(item.st_mode):
            raise WorkspaceValidationError("state_root must be a directory")
    elif create:
        try:
            os.makedirs(absolute, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise WorkspaceIOError() from exc
    else:
        raise WorkspaceNotFoundError()
    try:
        root = os.path.realpath(absolute)
        item = os.lstat(root)
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
            raise WorkspaceCorruptError()
        _enforce_owner_only(root, 0o700)
        return root
    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceIOError() from exc


def _workspaces_path(root: str, *, create: bool) -> str:
    path = os.path.join(root, "workspaces")
    created = False
    if os.path.lexists(path):
        try:
            item = os.lstat(path)
        except OSError as exc:
            raise WorkspaceIOError() from exc
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
            raise WorkspaceCorruptError()
    elif create:
        try:
            os.mkdir(path, 0o700)
            created = True
        except FileExistsError:
            return _workspaces_path(root, create=False)
        except OSError as exc:
            raise WorkspaceIOError() from exc
    else:
        raise WorkspaceNotFoundError()
    _enforce_owner_only(path, 0o700)
    if created:
        _fsync_directory(root)
    return path


def _workspace_path(root: str, workspace_id: str, *, create: bool = False) -> str:
    _uuid(workspace_id, "workspace_id")
    workspaces = _workspaces_path(root, create=create)
    path = os.path.join(workspaces, workspace_id)
    try:
        if os.path.commonpath((os.path.realpath(workspaces), os.path.abspath(path))) != os.path.realpath(workspaces):
            raise WorkspaceValidationError("workspace path is outside state root")
    except ValueError as exc:
        raise WorkspaceValidationError("workspace path is outside state root") from exc
    return path


def _fsync_directory(path: str) -> None:
    directory_fd = -1
    try:
        directory_fd, _ = _open_directory_ancestry(path)
        os.fsync(directory_fd)
    except OSError as exc:
        raise WorkspaceIOError() from exc
    finally:
        if directory_fd != -1:
            os.close(directory_fd)


@contextlib.contextmanager
def _directory_lock(directory_path: str, lock_name: str) -> Iterator[int]:
    if fcntl is None:
        raise WorkspaceLockError()
    directory_fd = -1
    locked = False
    try:
        directory_fd, directory_stat = _open_directory_ancestry(directory_path)
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode) or stat.S_IMODE(directory_stat.st_mode) & 0o077:
            raise WorkspaceLockError()
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (BlockingIOError, OSError) as exc:
                if not isinstance(exc, BlockingIOError) and getattr(exc, "errno", None) not in (
                    errno.EACCES,
                    errno.EAGAIN,
                ):
                    raise WorkspaceLockError() from exc
                if time.monotonic() >= deadline:
                    raise WorkspaceLockError()
                time.sleep(0.01)
        yield directory_fd
        if not _same_inode(directory_stat, os.fstat(directory_fd)):
            raise WorkspaceLockError()
    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceLockError() from exc
    finally:
        if locked:
            try:
                fcntl.flock(directory_fd, fcntl.LOCK_UN)
            except OSError as exc:
                raise WorkspaceLockError() from exc
        if directory_fd != -1:
            os.close(directory_fd)


@contextlib.contextmanager
def _workspace_lock(workspace_path: str) -> Iterator[_WorkspaceLockHandle]:
    with _directory_lock(workspace_path, "") as workspace_fd:
        events_fd = -1
        try:
            events_fd = os.open(
                "events", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace_fd
            )
            events_stat = os.fstat(events_fd)
            if not stat.S_ISDIR(events_stat.st_mode) or stat.S_IMODE(events_stat.st_mode) & 0o077:
                raise WorkspaceLockError()
            yield _WorkspaceLockHandle(
                workspace_fd,
                os.fstat(workspace_fd),
                events_fd,
                events_stat,
            )
        except WorkspaceError:
            raise
        except OSError as exc:
            raise WorkspaceLockError() from exc
        finally:
            if events_fd != -1:
                os.close(events_fd)


@contextlib.contextmanager
def _parent_create_lock(workspaces_path: str) -> Iterator[None]:
    with _directory_lock(workspaces_path, ""):
        yield


def _write_event(
    events_path: str,
    filename: str,
    data: bytes,
    *,
    directory_fd: Optional[int] = None,
) -> None:
    owned_directory_fd = False
    temporary_name = None
    fd = -1
    try:
        if directory_fd is None:
            directory_fd, _ = _open_directory_ancestry(events_path)
            owned_directory_fd = True
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_EXCL"):
            raise WorkspaceIOError()
        try:
            os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise WorkspaceConflictError()
        for _ in range(8):
            temporary_name = ".event-" + uuid.uuid4().hex
            try:
                fd = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                temporary_name = None
        if fd == -1 or temporary_name is None:
            raise WorkspaceIOError()
        os.fchmod(fd, 0o600)
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
        temporary_stat = os.fstat(fd)
        if (
            not stat.S_ISREG(temporary_stat.st_mode)
            or temporary_stat.st_nlink != 1
            or stat.S_IMODE(temporary_stat.st_mode) & 0o077
        ):
            raise WorkspaceIOError()
        os.close(fd)
        fd = -1
        # link() gives an atomic no-overwrite publication. There is no
        # replace-based fallback: overwrite support would violate the journal.
        if not hasattr(os, "link"):
            raise WorkspaceIOError()
        try:
            os.link(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise WorkspaceConflictError() from exc
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_name = None
        os.fsync(directory_fd)
        item = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(item.st_mode)
            or item.st_nlink != 1
            or stat.S_IMODE(item.st_mode) & 0o077
            or not _same_inode(item, temporary_stat)
        ):
            raise WorkspaceIOError()
    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceIOError() from exc
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary_name is not None and directory_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
        if owned_directory_fd and directory_fd is not None:
            os.close(directory_fd)


def _event_record(workspace_id: str, sequence: int, event_id: str, kind: str, payload: Mapping[str, object], previous_digest: str) -> Mapping[str, object]:
    unsigned = {
        "schema_version": _EVENT_SCHEMA,
        "workspace_id": workspace_id,
        "sequence": sequence,
        "event_id": event_id,
        "kind": kind,
        "payload": _plain(payload),
        "previous_digest": previous_digest,
    }
    digest = _digest(unsigned)
    return _mapping(dict(unsigned, event_digest=digest))


_SESSION_BINDINGS: weakref.WeakKeyDictionary[ContextSession, Tuple[str, str]] = (
    weakref.WeakKeyDictionary()
)
_SESSION_BINDINGS_LOCK = threading.RLock()


class ContextWorkspace:
    """Durable Product state rooted under an explicit state directory."""

    def __init__(self, root: str, workspace_path: str, state: _WorkspaceState) -> None:
        self._root = root
        self._workspace_path = workspace_path
        event_id = state.events[0].get("event_id")
        if not isinstance(event_id, str):
            raise WorkspaceCorruptError()
        self._creation_receipt = state.receipts[event_id]
        self._runtime_sessions: Dict[str, ContextSession] = {}
        self._runtime_source_bindings: Dict[str, SourceAnchor] = {}

    @property
    def workspace_id(self) -> str:
        return self._creation_receipt.workspace_id

    @property
    def identity(self) -> WorkspaceIdentity:
        state = self._read_state()
        if state.identity is None:
            raise WorkspaceCorruptError()
        return state.identity

    @property
    def fork_lineage(self) -> Optional[Mapping[str, object]]:
        lineage = self._read_state().fork_lineage
        return _freeze(_plain(lineage)) if lineage is not None else None

    @property
    def creation_receipt(self) -> WorkspaceReceipt:
        return self._creation_receipt

    def _session_binding_key(self) -> Tuple[str, str]:
        return (self._root, self._creation_receipt.state_id)

    @classmethod
    def create(
        cls,
        state_root: Any,
        name: str,
        *,
        policy: Optional[WorkspacePolicy] = None,
        workspace_id: Optional[str] = None,
    ) -> "ContextWorkspace":
        selected_id = str(uuid.uuid4()) if workspace_id is None else _uuid(workspace_id, "workspace_id")
        _text(name, "name", 128)
        _reject_sensitive(name, "name")
        root = _canonical_root(state_root, create=True)
        workspaces = _workspaces_path(root, create=True)
        if policy is None:
            policy = WorkspacePolicy()
        if not isinstance(policy, WorkspacePolicy):
            raise WorkspaceValidationError("policy must be WorkspacePolicy")
        identity = WorkspaceIdentity(selected_id, name, _now_timestamp())
        workspace_path = os.path.join(workspaces, selected_id)
        if os.path.lexists(workspace_path):
            raise WorkspaceAlreadyExistsError()
        state = _WorkspaceState()
        event = _event_record(
            selected_id,
            1,
            str(uuid.uuid4()),
            "workspace_created",
            {"identity": _plain(identity.to_dict()), "policy": _plain(policy.to_dict())},
            _ZERO_DIGEST,
        )
        _apply_event(state, event)
        event_bytes = _canonical(event) + b"\n"
        temporary_workspace = None
        try:
            temporary_workspace = tempfile.mkdtemp(prefix=".workspace-", dir=workspaces)
            _enforce_owner_only(temporary_workspace, 0o700)
            events_path = os.path.join(temporary_workspace, "events")
            os.mkdir(events_path, 0o700)
            _enforce_owner_only(events_path, 0o700)
            event_digest = _digest_value(event.get("event_digest"), "event_digest")
            filename = "0000000000000001-" + event_digest.removeprefix("sha256:") + ".json"
            _write_event(events_path, filename, event_bytes)
            _fsync_directory(temporary_workspace)
            with _parent_create_lock(workspaces):
                if os.path.lexists(workspace_path):
                    raise WorkspaceAlreadyExistsError()
                try:
                    os.rename(temporary_workspace, workspace_path)
                except FileExistsError as exc:
                    raise WorkspaceAlreadyExistsError() from exc
            temporary_workspace = None
            _fsync_directory(workspaces)
            _enforce_owner_only(workspace_path, 0o700)
            _enforce_owner_only(os.path.join(workspace_path, "events"), 0o700)
        except WorkspaceError:
            raise
        except OSError as exc:
            raise WorkspaceIOError() from exc
        finally:
            if temporary_workspace is not None:
                try:
                    for filename in os.listdir(os.path.join(temporary_workspace, "events")):
                        os.unlink(os.path.join(temporary_workspace, "events", filename))
                    os.rmdir(os.path.join(temporary_workspace, "events"))
                    os.rmdir(temporary_workspace)
                except OSError:
                    pass
        return cls(root, workspace_path, state)

    def fork(
        self,
        name: str,
        *,
        from_checkpoint: Optional[ContextCheckpointV2] = None,
        policy: Optional[WorkspacePolicy] = None,
        workspace_id: Optional[str] = None,
        fork_id: Optional[str] = None,
        event_id: Optional[str] = None,
        execution_ref: Optional[str] = None,
        source_bindings: Optional[Mapping[str, ContextSource]] = None,
    ) -> "ContextWorkspace":
        """Atomically publish a fresh child from one immutable stored checkpoint."""
        from .parallel_context import WorkspaceForkV1

        parent_state = self._read_state()
        if parent_state.lifecycle != "active":
            raise WorkspaceLifecycleError()
        checkpoint = self.checkpoint() if from_checkpoint is None else from_checkpoint
        if from_checkpoint is None:
            parent_state = self._read_state()
        if not isinstance(checkpoint, ContextCheckpointV2):
            raise WorkspaceValidationError("from_checkpoint must be ContextCheckpointV2")
        stored = parent_state.checkpoints.get(checkpoint.checkpoint_id)
        if stored is None or _canonical(stored.to_dict()) != _canonical(checkpoint.to_dict()):
            raise WorkspaceConflictError()
        selected_workspace_id = (
            str(uuid.uuid4())
            if workspace_id is None
            else _uuid(workspace_id, "workspace_id")
        )
        selected_event_id = str(uuid.uuid4()) if event_id is None else _uuid(event_id, "event_id")
        _text(name, "name", 128)
        _reject_sensitive(name, "name")
        identity = WorkspaceIdentity(selected_workspace_id, name, _now_timestamp())
        fork = WorkspaceForkV1.create(
            checkpoint,
            selected_workspace_id,
            name,
            selected_event_id,
            requested_policy=policy,
            fork_id=fork_id,
            execution_ref=execution_ref,
        )
        inherited_state = _plain(checkpoint.logical_state)
        inherited_state.pop("workspace_id", None)
        for anchor in inherited_state["sources"]:
            anchor["engine_binding"] = None
        root = self._root
        workspaces = _workspaces_path(root, create=True)
        workspace_path = os.path.join(workspaces, selected_workspace_id)
        if os.path.lexists(workspace_path):
            raise WorkspaceAlreadyExistsError()
        created = _event_record(
            selected_workspace_id,
            1,
            str(uuid.uuid4()),
            "workspace_created",
            {
                "identity": _plain(identity.to_dict()),
                "policy": _plain(fork.policy_inheritance.effective_child_policy.to_dict()),
            },
            _ZERO_DIGEST,
        )
        forked = _event_record(
            selected_workspace_id,
            2,
            selected_event_id,
            "workspace_forked",
            {
                "fork": _plain(fork.to_dict()),
                "inherited_state": inherited_state,
            },
            _digest_value(created.get("event_digest"), "event_digest"),
        )
        candidate = _WorkspaceState()
        try:
            _apply_event(candidate, created)
            _apply_event(candidate, forked)
        except _ProjectionFailure as exc:
            self._raise_projection(exc)
            raise AssertionError("unreachable")
        runtime_bindings = {}
        if source_bindings is not None:
            if not isinstance(source_bindings, Mapping):
                raise WorkspaceValidationError("source_bindings must be a mapping")
            unknown = set(source_bindings) - set(candidate.sources)
            if unknown:
                raise WorkspaceValidationError("source_bindings contains an unknown source")
            for source_id in sorted(source_bindings):
                runtime_bindings[source_id] = _bind_portable_anchor(
                    candidate.sources[source_id], source_bindings[source_id]
                )
        temporary_workspace = None
        try:
            temporary_workspace = tempfile.mkdtemp(prefix=".workspace-", dir=workspaces)
            _enforce_owner_only(temporary_workspace, 0o700)
            events_path = os.path.join(temporary_workspace, "events")
            os.mkdir(events_path, 0o700)
            _enforce_owner_only(events_path, 0o700)
            for event in (created, forked):
                filename = (
                    f"{event['sequence']:016d}-"
                    + _digest_value(event.get("event_digest"), "event_digest").removeprefix("sha256:")
                    + ".json"
                )
                _write_event(events_path, filename, _canonical(event) + b"\n")
            _fsync_directory(temporary_workspace)
            with _parent_create_lock(workspaces):
                if os.path.lexists(workspace_path):
                    raise WorkspaceAlreadyExistsError()
                try:
                    os.rename(temporary_workspace, workspace_path)
                except FileExistsError as exc:
                    raise WorkspaceAlreadyExistsError() from exc
            temporary_workspace = None
            _fsync_directory(workspaces)
            _enforce_owner_only(workspace_path, 0o700)
            _enforce_owner_only(os.path.join(workspace_path, "events"), 0o700)
        except WorkspaceError:
            raise
        except OSError as exc:
            raise WorkspaceIOError() from exc
        finally:
            if temporary_workspace is not None:
                try:
                    for filename in os.listdir(os.path.join(temporary_workspace, "events")):
                        os.unlink(os.path.join(temporary_workspace, "events", filename))
                    os.rmdir(os.path.join(temporary_workspace, "events"))
                    os.rmdir(temporary_workspace)
                except OSError:
                    pass
        child = type(self)(root, workspace_path, candidate)
        child._runtime_source_bindings = runtime_bindings
        return child

    @classmethod
    def open(cls, state_root: Any, workspace_id: str) -> "ContextWorkspace":
        root = _canonical_root(state_root, create=False)
        try:
            workspace_path = _workspace_path(root, workspace_id, create=False)
        except WorkspaceNotFoundError:
            raise
        try:
            state = _project(workspace_path, workspace_id)
        except _ProjectionFailure as exc:
            cls._raise_projection(exc)
        return cls(root, workspace_path, state)

    @staticmethod
    def _raise_projection(failure: _ProjectionFailure) -> None:
        status = None
        if failure.state is not None:
            try:
                status = failure.state.status(
                    "incompatible" if failure.kind == "incompatible" else "corrupt"
                )
            except Exception:
                status = None
        if failure.kind == "not_found":
            raise WorkspaceNotFoundError()
        if failure.kind == "incompatible":
            raise WorkspaceIncompatibleError(status=status)
        if failure.kind == "io":
            raise WorkspaceIOError(status=status)
        raise WorkspaceCorruptError(status=status)

    def _read_state(self) -> _WorkspaceState:
        try:
            return _project(self._workspace_path, self.workspace_id)
        except _ProjectionFailure as exc:
            self._raise_projection(exc)
            raise AssertionError("unreachable")

    def _retry(
        self,
        kind: str,
        event_id: Optional[str],
        payload: Mapping[str, object],
    ) -> Optional[WorkspaceReceipt]:
        if event_id is None:
            return None
        selected_event_id = _uuid(event_id, "event_id")
        state = self._read_state()
        receipt = state.receipts.get(selected_event_id)
        if receipt is None:
            return None
        event = next(event for event in state.events if event["event_id"] == selected_event_id)
        if event["kind"] == kind and _canonical(event["payload"]) == _canonical(payload):
            return receipt
        raise WorkspaceConflictError()

    def status(self) -> WorkspaceStatus:
        return self._read_state().status()

    def _append(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        event_id: Optional[str],
        source_ids: Sequence[str] = (),
        session_id: Optional[str] = None,
        pre_publish: Optional[Callable[[], None]] = None,
        evidence_receipts: Sequence[ContextReceipt] = (),
    ) -> WorkspaceReceipt:
        if kind not in _EVENT_KINDS or kind == "workspace_created":
            raise WorkspaceValidationError("event kind is not a mutable P5 event")
        _exact(payload, _event_payload_keys(kind), f"{kind} payload")
        selected_event_id = str(uuid.uuid4()) if event_id is None else _uuid(event_id, "event_id")
        _reject_sensitive(payload, "payload")
        verified_evidence = False
        if kind in {"source_attached", "source_updated"}:
            try:
                candidate_anchor = _source_anchor_from_dict(
                    payload["anchor"], allow_verified=True
                )
            except (KeyError, WorkspaceError) as exc:
                raise WorkspaceValidationError("source anchor is invalid") from exc
            if candidate_anchor.trust is not None and candidate_anchor.trust.level == "verified":
                evidenced_anchor = _anchor_with_evidence(candidate_anchor, evidence_receipts)
                if _plain(evidenced_anchor.to_dict()) != _plain(candidate_anchor.to_dict()):
                    raise WorkspaceConflictError()
                verified_evidence = True
            elif evidence_receipts:
                raise WorkspaceValidationError(
                    "evidence receipts require verified source trust"
                )
        try:
            with _workspace_lock(self._workspace_path) as lock:
                try:
                    if not _same_inode(lock.workspace_stat, os.fstat(lock.workspace_fd)) or not _same_inode(
                        lock.events_stat, os.fstat(lock.events_fd)
                    ):
                        raise WorkspaceLockError()
                except OSError as exc:
                    raise WorkspaceLockError() from exc
                try:
                    state = _project(
                        self._workspace_path,
                        self.workspace_id,
                        workspace_directory_fd=lock.workspace_fd,
                        events_directory_fd=lock.events_fd,
                    )
                except _ProjectionFailure as exc:
                    self._raise_projection(exc)
                previous = state.receipts.get(selected_event_id)
                if previous is not None:
                    existing_event = next(
                        event for event in state.events if event["event_id"] == selected_event_id
                    )
                    if (
                        existing_event["kind"] == kind
                        and _canonical(existing_event["payload"]) == _canonical(payload)
                    ):
                        return previous
                    raise WorkspaceConflictError()
                if state.lifecycle != "active":
                    raise WorkspaceLifecycleError()
                if len(state.events) + 1 > _state_policy(state).max_events:
                    raise WorkspacePolicyError()
                candidate = state.clone()
                event = _event_record(
                    self.workspace_id,
                    len(state.events) + 1,
                    selected_event_id,
                    kind,
                    payload,
                    state.last_event_digest,
                )
                try:
                    _apply_event(
                        candidate,
                        event,
                        verified_evidence=verified_evidence,
                    )
                except _ProjectionFailure as exc:
                    if exc.kind == "incompatible":
                        raise WorkspacePolicyError() from exc
                    if exc.kind == "corrupt":
                        raise WorkspaceConflictError() from exc
                    raise WorkspaceIOError() from exc
                filename = (
                    f"{event['sequence']:016d}-"
                    f"{_digest_value(event.get('event_digest'), 'event_digest').removeprefix('sha256:')}.json"
                )
                try:
                    if not _same_inode(lock.workspace_stat, os.fstat(lock.workspace_fd)) or not _same_inode(
                        lock.events_stat, os.fstat(lock.events_fd)
                    ):
                        raise WorkspaceLockError()
                except OSError as exc:
                    raise WorkspaceLockError() from exc
                if pre_publish is not None:
                    pre_publish()
                _write_event(
                    "",
                    filename,
                    _canonical(event) + b"\n",
                    directory_fd=lock.events_fd,
                )
                return _receipt_for_event(candidate, event)
        except WorkspaceError:
            raise
        except _ProjectionFailure as exc:
            self._raise_projection(exc)
            raise AssertionError("unreachable")

    def _require_source(self, source_id: str) -> SourceAnchor:
        source_id = _text(source_id, "source_id", 128)
        state = self._read_state()
        try:
            return state.sources[source_id]
        except KeyError as exc:
            raise WorkspaceValidationError("source_id is not attached") from exc

    def bind_source(self, source_id: str, source: ContextSource) -> SourceAnchor:
        """Bind a portable inherited anchor to this process after exact recheck."""
        anchor = self._require_source(source_id)
        bound = _bind_portable_anchor(anchor, source)
        self._runtime_source_bindings[source_id] = bound
        return bound

    def attach_source(
        self,
        anchor: SourceAnchor,
        *,
        evidence_receipts: Sequence[ContextReceipt] = (),
        event_id: Optional[str] = None,
    ) -> WorkspaceReceipt:
        if not isinstance(anchor, SourceAnchor):
            raise WorkspaceValidationError("anchor must be SourceAnchor")
        anchor = _anchor_with_evidence(anchor, evidence_receipts)
        payload = {"anchor": _plain(anchor.to_dict())}
        retry = self._retry("source_attached", event_id, payload)
        if retry is not None:
            return retry
        state = self._read_state()
        if state.lifecycle != "active":
            raise WorkspaceLifecycleError()
        if anchor.source_id in state.sources:
            raise WorkspaceConflictError()
        if len(state.sources) + 1 > _state_policy(state).max_sources:
            raise WorkspacePolicyError()
        _ensure_external_allowed(state, anchor)
        _reject_sensitive(anchor.to_dict(), "anchor")
        return self._append(
            "source_attached",
            payload,
            event_id=event_id,
            evidence_receipts=evidence_receipts,
        )

    def update_source(
        self,
        anchor: SourceAnchor,
        *,
        evidence_receipts: Sequence[ContextReceipt] = (),
        event_id: Optional[str] = None,
    ) -> WorkspaceReceipt:
        if not isinstance(anchor, SourceAnchor):
            raise WorkspaceValidationError("anchor must be SourceAnchor")
        anchor = _anchor_with_evidence(anchor, evidence_receipts)
        if event_id is not None:
            selected_event_id = _uuid(event_id, "event_id")
            current = self._read_state()
            existing = current.receipts.get(selected_event_id)
            if existing is not None:
                event = next(event for event in current.events if event["event_id"] == selected_event_id)
                event_payload = event.get("payload")
                if not isinstance(event_payload, Mapping):
                    raise WorkspaceConflictError()
                if event.get("kind") == "source_updated" and _canonical(
                    event_payload.get("anchor")
                ) == _canonical(anchor.to_dict()):
                    return existing
                raise WorkspaceConflictError()
        state = self._read_state()
        if state.lifecycle != "active":
            raise WorkspaceLifecycleError()
        previous = state.sources.get(anchor.source_id)
        if previous is None:
            raise WorkspaceValidationError("source_id is not attached")
        _ensure_external_allowed(state, anchor)
        _reject_sensitive(anchor.to_dict(), "anchor")
        payload = {
            "anchor": _plain(anchor.to_dict()),
            "previous_anchor_digest": _anchor_digest(previous),
        }
        return self._append(
            "source_updated",
            payload,
            event_id=event_id,
            evidence_receipts=evidence_receipts,
        )

    def start_session(
        self,
        task: str,
        *,
        source_id: str,
        source: ContextSource,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        fail_open: bool = False,
        event_id: Optional[str] = None,
    ) -> WorkspaceSessionAttachment:
        if not isinstance(source, ContextSource):
            raise WorkspaceValidationError("source must be ContextSource")
        anchor = self._require_source(source_id)
        if anchor.engine_binding is None:
            bound = self._runtime_source_bindings.get(source_id)
            if bound is None:
                raise WorkspaceValidationError(
                    "portable source must be explicitly rebound in this process"
                )
            anchor = bound
        _validate_anchor_binding(anchor, source)
        _text(task, "task", 16 * 1024, controls=False)
        with _pin_source(source) as pin:
            session = ContextSession(
                task,
                project_root=source.project_root,
                session_id=session_id,
                task_id=task_id,
                fail_open=fail_open,
            )
            session.plan(source)
            session.prepare(source)
            _revalidate_source_pin(source, pin)
        with _SESSION_BINDINGS_LOCK:
            session_binding = _SESSION_BINDINGS.get(session)
            if session_binding is not None and session_binding != self._session_binding_key():
                raise WorkspaceConflictError()
            _SESSION_BINDINGS[session] = self._session_binding_key()
        payload = {
            "session_id": session.session_id,
            "task_id": session.task_id,
            "source_ids": [source_id],
        }
        try:
            receipt = self._append("session_attached", payload, event_id=event_id)
        except Exception:
            with _SESSION_BINDINGS_LOCK:
                _SESSION_BINDINGS.pop(session, None)
            raise
        self._runtime_sessions[session.session_id] = session
        return WorkspaceSessionAttachment(session, receipt)

    def attach_session(
        self,
        session: ContextSession,
        *,
        source_ids: Sequence[str],
        event_id: Optional[str] = None,
    ) -> WorkspaceSessionAttachment:
        if not isinstance(session, ContextSession):
            raise WorkspaceValidationError("session must be ContextSession")
        source_ids = _ids(source_ids, "source_ids", _MAX_SOURCE_IDS)
        if not source_ids:
            raise WorkspaceValidationError("source_ids must not be empty")
        try:
            session_id_value = _text(session.session_id, "session.session_id", 512)
            task_id_value = _text(session.task_id, "session.task_id", 512)
            session_state = _text(session.state, "session.state", 64)
            plan = session.current_plan
        except WorkspaceError:
            raise WorkspaceValidationError("session fields are invalid")
        except Exception as exc:
            raise WorkspaceValidationError("session fields are invalid") from exc
        if session_state not in {"created", "planned", "executing", "completed", "aborted", "closed"}:
            raise WorkspaceValidationError("session.state is invalid")
        if plan is None:
            raise WorkspaceValidationError("session current_plan is required")
        if not hasattr(plan, "source") or not isinstance(plan.source, ContextSource):
            raise WorkspaceValidationError("session current_plan source is invalid")
        if plan.session_id != session_id_value or plan.task_id != task_id_value:
            raise WorkspaceValidationError("session current_plan fields are invalid")
        state = self._read_state()
        if state.lifecycle != "active":
            raise WorkspaceLifecycleError()
        anchors = []
        for source_id in source_ids:
            try:
                anchors.append(state.sources[source_id])
            except KeyError as exc:
                raise WorkspaceValidationError("source_id is not attached") from exc
        with _SESSION_BINDINGS_LOCK:
            bound = _SESSION_BINDINGS.get(session)
            if bound is not None and bound != self._session_binding_key():
                raise WorkspaceConflictError()
        matching_anchors = [
            anchor
            for anchor in anchors
            if anchor.engine_binding is not None
            and _plain(anchor.engine_binding) == _plain(plan.source.to_dict())
        ]
        if len(matching_anchors) != 1:
            raise WorkspaceConflictError()
        _validate_anchor_binding(matching_anchors[0], plan.source)
        with _pin_source(plan.source) as pin:
            _revalidate_source_pin(plan.source, pin)
        existing = next(
            (record for record in state.sessions if record["session_id"] == session_id_value),
            None,
        )
        payload = {
            "session_id": session_id_value,
            "task_id": task_id_value,
            "source_ids": list(source_ids),
        }
        retry = self._retry("session_attached", event_id, payload)
        if retry is not None:
            with _SESSION_BINDINGS_LOCK:
                _SESSION_BINDINGS[session] = self._session_binding_key()
            self._runtime_sessions[session_id_value] = session
            return WorkspaceSessionAttachment(session, retry)
        if existing is not None:
            if event_id is not None:
                raise WorkspaceConflictError()
            existing_source_ids = existing.get("source_ids")
            if not isinstance(existing_source_ids, (list, tuple)):
                raise WorkspaceConflictError()
            if (
                existing["task_id"] == task_id_value
                and tuple(existing_source_ids) == tuple(source_ids)
            ):
                receipt = next(
                    receipt
                    for receipt in state.receipts.values()
                    if receipt.event_kind == "session_attached"
                    and receipt.session_id == session_id_value
                )
                with _SESSION_BINDINGS_LOCK:
                    _SESSION_BINDINGS[session] = self._session_binding_key()
                self._runtime_sessions[session_id_value] = session
                return WorkspaceSessionAttachment(session, receipt)
            raise WorkspaceConflictError()
        with _SESSION_BINDINGS_LOCK:
            _SESSION_BINDINGS[session] = self._session_binding_key()
        try:
            receipt = self._append("session_attached", payload, event_id=event_id)
        except Exception:
            with _SESSION_BINDINGS_LOCK:
                _SESSION_BINDINGS.pop(session, None)
            raise
        self._runtime_sessions[session_id_value] = session
        return WorkspaceSessionAttachment(session, receipt)

    def commit_context(
        self,
        entries: Sequence[Any],
        *,
        session: Optional[ContextSession] = None,
        event_id: Optional[str] = None,
    ) -> WorkspaceReceipt:
        if not isinstance(entries, (list, tuple)) or not entries:
            raise WorkspaceValidationError("entries must be a non-empty sequence")
        state = self._read_state()
        if state.lifecycle != "active":
            raise WorkspaceLifecycleError()
        selected_session = None
        provenance: Optional[Dict[str, object]] = None
        receipt_refs: Tuple[str, ...] = ()
        recovery_refs: Tuple[str, ...] = ()
        bound_source_ids: Tuple[str, ...] = ()
        if session is not None:
            if not isinstance(session, ContextSession):
                raise WorkspaceValidationError("session must be ContextSession")
            try:
                selected_session = _text(session.session_id, "session.session_id", 512)
                task_id = _text(session.task_id, "session.task_id", 512)
                session_state = _text(session.state, "session.state", 64)
                plan = session.current_plan
                current_receipt = session.receipt
            except WorkspaceError:
                raise WorkspaceValidationError("session fields are invalid")
            except Exception as exc:
                raise WorkspaceValidationError("session fields are invalid") from exc
            if session_state != "completed" or plan is None:
                raise WorkspaceValidationError("session must have a completed current plan")
            with _SESSION_BINDINGS_LOCK:
                if (
                    self._runtime_sessions.get(selected_session) is not session
                    or _SESSION_BINDINGS.get(session) != self._session_binding_key()
                ):
                    raise WorkspaceConflictError()
            durable = [
                record for record in state.sessions if record["session_id"] == selected_session
            ]
            if len(durable) != 1 or durable[0]["task_id"] != task_id:
                raise WorkspaceConflictError()
            if not isinstance(plan.source, ContextSource) or plan.session_id != selected_session:
                raise WorkspaceValidationError("session current plan is invalid")
            if not isinstance(current_receipt, ContextReceipt):
                raise WorkspaceValidationError("session requires a ContextReceipt")
            try:
                verified = current_receipt.sealed is True and current_receipt.verify() is True
                receipt_session = _text(current_receipt.session_id, "receipt.session_id", 512)
                receipt_task = _text(current_receipt.task_id, "receipt.task_id", 512)
                receipt_source = current_receipt.source
                receipt_link = current_receipt.receipt_link
                recovery_ref = current_receipt.recovery_ref
            except Exception as exc:
                raise WorkspaceValidationError("session receipt is invalid") from exc
            if (
                not verified
                or receipt_session != selected_session
                or receipt_task != task_id
                or receipt_source is None
                or receipt_link is None
                or current_receipt.plan_id != plan.plan_id
            ):
                raise WorkspaceConflictError()
            matching = []
            matching_anchors = {}
            durable_source_ids = durable[0].get("source_ids")
            if not isinstance(durable_source_ids, (list, tuple)):
                raise WorkspaceConflictError()
            for source_id in durable_source_ids:
                if not isinstance(source_id, str):
                    raise WorkspaceConflictError()
                anchor = state.sources[source_id]
                effective: Optional[SourceAnchor] = anchor
                if anchor.engine_binding is None:
                    effective = self._runtime_source_bindings.get(source_id)
                if effective is not None and effective.engine_binding is not None and _plain(
                    effective.engine_binding
                ) == _plain(receipt_source.to_dict()):
                    matching.append(source_id)
                    matching_anchors[source_id] = effective
            if len(matching) != 1:
                raise WorkspaceConflictError()
            _validate_anchor_binding(matching_anchors[matching[0]], receipt_source)
            if not _P4_RECEIPT_REF_RE.fullmatch(receipt_link.receipt_ref):
                raise WorkspaceValidationError("receipt ref is not P4-bound")
            bound_source_ids = (matching[0],)
            receipt_refs = _refs((receipt_link.receipt_ref,), "receipt_refs", _MAX_ENTRY_REFS)
            if recovery_ref is not None:
                recovery_refs = _refs((recovery_ref,), "recovery_refs", _MAX_ENTRY_REFS)
            provenance = {
                "session_id": selected_session,
                "task_id": task_id,
                "source_ids": list(bound_source_ids),
                "receipt_refs": list(receipt_refs),
                "recovery_refs": list(recovery_refs),
            }
            receipt_proof = {
                "schema_version": current_receipt.schema_version,
                "session_id": current_receipt.session_id,
                "task_id": current_receipt.task_id,
                "plan_id": current_receipt.plan_id,
                "integrity_status": current_receipt.integrity_status,
                "status": current_receipt.status,
                "source": _plain(receipt_source.to_dict()),
                "receipt_link": _plain(receipt_link.to_dict()),
                "recovery_ref": recovery_ref,
                "output_digest": current_receipt.output_digest,
            }
            provenance["receipt_proof"] = receipt_proof
            provenance["receipt_proof_digest"] = _digest(receipt_proof)
        normalized: List[ProjectContextEntry] = []
        existing_ids = {entry.entry_id for entry in state.entries}
        for raw in entries:
            entry = _entry_from_input(raw)
            if (
                any(item.entry_id == entry.entry_id for item in normalized)
                or (event_id is None and entry.entry_id in existing_ids)
            ):
                raise WorkspaceConflictError()
            if session is not None and entry.session_id not in (None, selected_session):
                raise WorkspaceConflictError()
            if entry.receipt_refs or entry.recovery_refs:
                raise WorkspaceValidationError("entry lineage is derived from ContextReceipt")
            entry_session = selected_session if session is not None else None
            source_ids = entry.source_ids
            if session is not None:
                if not source_ids:
                    source_ids = bound_source_ids
                if tuple(source_ids) != bound_source_ids:
                    raise WorkspaceConflictError()
            elif entry.session_id is not None:
                raise WorkspaceValidationError("session entries require session")
            if any(source_id not in state.sources for source_id in source_ids):
                raise WorkspaceValidationError("entry source_id is not attached")
            entry = ProjectContextEntry(
                entry.entry_id,
                entry.category,
                entry.value,
                source_ids,
                entry_session,
                receipt_refs,
                recovery_refs,
            )
            _reject_sensitive(entry.to_dict(), "value")
            if entry.category not in _state_policy(state).allowed_categories:
                raise WorkspacePolicyError()
            if len(entry.value.encode("utf-8")) > _state_policy(state).max_entry_bytes:
                raise WorkspacePolicyError()
            normalized.append(entry)
        if session is None and any(entry.receipt_refs or entry.recovery_refs for entry in normalized):
            raise WorkspaceValidationError("workspace entries cannot carry Engine lineage")
        payload = {
            "entries": [_plain(entry.to_dict()) for entry in normalized],
            "provenance": _plain(provenance),
        }
        retry = self._retry("context_committed", event_id, payload)
        if retry is not None:
            return retry
        if any(entry.entry_id in existing_ids for entry in normalized):
            raise WorkspaceConflictError()
        candidate_entries = list(state.entries) + normalized
        if len(candidate_entries) > _state_policy(state).max_context_entries:
            raise WorkspacePolicyError()
        if _state_context_bytes(candidate_entries) > _state_policy(state).max_context_bytes:
            raise WorkspacePolicyError()
        return self._append(
            "context_committed",
            payload,
            event_id=event_id,
        )

    def checkpoint(
        self,
        *,
        checkpoint_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> ContextCheckpointV2:
        state = self._read_state()
        if state.lifecycle != "active":
            raise WorkspaceLifecycleError()
        selected_id = (
            str(uuid.uuid4())
            if checkpoint_id is None
            else _uuid(checkpoint_id, "checkpoint_id")
        )
        logical_state = state.logical_state()
        recovery_refs = tuple(
            sorted({ref for entry in state.entries for ref in entry.recovery_refs})
        )
        unsigned = {
            "schema_version": _CHECKPOINT_SCHEMA,
            "checkpoint_id": selected_id,
            "workspace_id": self.workspace_id,
            "state_digest": state.logical_digest(),
            "state_schema_version": _LOGICAL_STATE_SCHEMA,
            "workspace_state_ref": "event:" + state.last_event_digest,
            "logical_state": _plain(logical_state),
            "source_anchors": logical_state["sources"],
            "recovery_refs": list(recovery_refs),
            "package_pins": logical_state["package_pins"],
            "package_lock_digest": logical_state["package_lock_digest"],
            "policy_digest": _domain_digest(
                "leanctx.workspace.policy.v1",
                _state_policy(state).to_dict(),
            ),
            "project_context_digest": _domain_digest(
                "leanctx.project-context.state.v1",
                [entry.to_dict() for entry in state.entries],
            ),
            "lineage": {
                "kind": "workspace",
                "workspace_id": self.workspace_id,
                "state_id": _state_identity(state).state_id,
            },
            "engine_identity": {
                "interface_version": "1.0.0",
                "schema_version": 1,
                "transport_version": 1,
            },
            "sdk_contract": _SDK_P6_CONTRACT,
        }
        checkpoint = ContextCheckpointV2.from_dict(
            dict(
                unsigned,
                envelope_digest=_checkpoint_envelope_digest(unsigned),
            )
        )
        self._append(
            "checkpoint_created",
            {"checkpoint": _plain(checkpoint.to_dict())},
            event_id=event_id,
        )
        return checkpoint

    def pin_package(
        self,
        pin: PackagePin,
        *,
        event_id: Optional[str] = None,
    ) -> WorkspaceReceipt:
        if not isinstance(pin, PackagePin):
            raise WorkspaceValidationError("pin must be PackagePin")
        state = self._read_state()
        return self._append(
            "package_pinned",
            {
                "pin": _plain(pin.to_dict()),
                "previous_lock_digest": state.package_lock_digest,
            },
            event_id=event_id,
        )

    def get_checkpoint(self, checkpoint_id: str) -> ContextCheckpointV2:
        selected_id = _uuid(checkpoint_id, "checkpoint_id")
        checkpoint = self._read_state().checkpoints.get(selected_id)
        if checkpoint is None:
            raise WorkspaceNotFoundError()
        return checkpoint

    def context_delta(
        self,
        base: ContextCheckpointV2,
        target: ContextCheckpointV2,
    ) -> Any:
        from .parallel_context import ContextDeltaV1, ForkLineageV1

        state = self._read_state()
        stored = state.checkpoints.get(target.checkpoint_id)
        if stored is None or _canonical(stored.to_dict()) != _canonical(target.to_dict()):
            raise WorkspaceConflictError()
        ancestry = "exact"
        if base.workspace_id == target.workspace_id:
            stored_base = state.checkpoints.get(base.checkpoint_id)
            if stored_base is None or _canonical(stored_base.to_dict()) != _canonical(
                base.to_dict()
            ):
                raise WorkspaceConflictError()
        else:
            if state.fork_lineage is None:
                raise WorkspaceConflictError()
            lineage = ForkLineageV1.from_dict(state.fork_lineage)
            if (
                lineage.parent_workspace_id != base.workspace_id
                or lineage.parent_checkpoint_id != base.checkpoint_id
                or lineage.parent_checkpoint_state_digest != base.state_digest
                or lineage.parent_checkpoint_envelope_digest != base.envelope_digest
            ):
                raise WorkspaceConflictError()
        target_lineage = (
            ForkLineageV1.from_dict(state.fork_lineage)
            if state.fork_lineage is not None
            else None
        )
        return ContextDeltaV1._between_verified(
            base,
            target,
            ancestry=ancestry,
            base_lineage=target_lineage if base.workspace_id == self.workspace_id else None,
            target_lineage=target_lineage,
        )

    def create_handoff(
        self,
        checkpoint: ContextCheckpointV2,
        *,
        target_workspace_id: str,
        task: str,
        entry_ids: Sequence[str],
        target_role: Optional[str] = None,
        handoff_id: Optional[str] = None,
    ) -> Any:
        from .parallel_context import ContextHandoffV1, ForkLineageV1

        state = self._read_state()
        stored = state.checkpoints.get(checkpoint.checkpoint_id)
        if stored is None or _canonical(stored.to_dict()) != _canonical(checkpoint.to_dict()):
            raise WorkspaceConflictError()
        lineage = (
            ForkLineageV1.from_dict(state.fork_lineage)
            if state.fork_lineage is not None
            else None
        )
        return ContextHandoffV1.create(
            checkpoint,
            target_workspace_id,
            task,
            entry_ids,
            target_role=target_role,
            handoff_id=handoff_id,
            source_lineage=lineage,
        )

    def admit_handoff(
        self,
        handoff: Any,
        receiver_checkpoint: ContextCheckpointV2,
    ) -> Any:
        from .parallel_context import (
            ConflictEntryV1,
            ConflictReportV1,
            ContextHandoffV1,
            EvidenceRefV1,
            ForkLineageV1,
            HandoffAdmissionV1,
            WorkspaceStateRefV1,
        )

        if not isinstance(handoff, ContextHandoffV1):
            raise WorkspaceValidationError("handoff must be ContextHandoffV1")
        state = self._read_state()
        stored = state.checkpoints.get(receiver_checkpoint.checkpoint_id)
        if stored is None or _canonical(stored.to_dict()) != _canonical(
            receiver_checkpoint.to_dict()
        ):
            raise WorkspaceConflictError()
        receiver_lineage = (
            ForkLineageV1.from_dict(state.fork_lineage)
            if state.fork_lineage is not None
            else None
        )
        source_state = None
        try:
            source_workspace = (
                self
                if handoff.source.workspace_id == self.workspace_id
                else type(self).open(self._root, handoff.source.workspace_id)
            )
            source_state = source_workspace._read_state()
        except WorkspaceError:
            pass
        source_checkpoint = (
            source_state.checkpoints.get(handoff.source.checkpoint_id)
            if source_state is not None
            else None
        )
        source_checkpoint_ok = (
            source_checkpoint is not None
            and source_checkpoint.workspace_id == handoff.source.workspace_id
            and source_checkpoint.state_digest == handoff.source.state_digest
            and source_checkpoint.envelope_digest
            == handoff.source.checkpoint_envelope_digest
        )
        stored_source_lineage = (
            ForkLineageV1.from_dict(source_state.fork_lineage)
            if source_state is not None and source_state.fork_lineage is not None
            else None
        )
        source_lineage = handoff.source_lineage
        source_lineage_ok = (
            (stored_source_lineage is None and source_lineage is None)
            or (
                stored_source_lineage is not None
                and source_lineage is not None
                and stored_source_lineage == source_lineage
            )
        )
        lineage_ok = source_checkpoint_ok and source_lineage_ok
        if handoff.source.workspace_id != self.workspace_id:
            if source_lineage is None and receiver_lineage is not None:
                lineage_ok = bool(
                    lineage_ok
                    and handoff.source.workspace_id
                    == receiver_lineage.parent_workspace_id
                    and handoff.source.checkpoint_id
                    == receiver_lineage.parent_checkpoint_id
                    and handoff.source.state_digest
                    == receiver_lineage.parent_checkpoint_state_digest
                    and handoff.source.checkpoint_envelope_digest
                    == receiver_lineage.parent_checkpoint_envelope_digest
                )
            else:
                lineage_ok = bool(
                    lineage_ok
                    and source_lineage is not None
                    and receiver_lineage is not None
                    and source_lineage.parent_workspace_id
                    == receiver_lineage.parent_workspace_id
                    and source_lineage.parent_checkpoint_id
                    == receiver_lineage.parent_checkpoint_id
                    and source_lineage.parent_checkpoint_state_digest
                    == receiver_lineage.parent_checkpoint_state_digest
                    and source_lineage.parent_checkpoint_envelope_digest
                    == receiver_lineage.parent_checkpoint_envelope_digest
                    and source_lineage.child_workspace_id
                    != receiver_lineage.child_workspace_id
                    and source_lineage.fork_id != receiver_lineage.fork_id
                    and source_lineage.fork_event_ref
                    != receiver_lineage.fork_event_ref
                )
        conflicts = []
        existing = {entry.entry_id: entry for entry in state.entries}
        receiver_ref = WorkspaceStateRefV1.from_checkpoint(
            receiver_checkpoint, fork_lineage=receiver_lineage
        )

        def entry_evidence(ref: Any, value: Mapping[str, Any]) -> Any:
            return EvidenceRefV1(
                ref.workspace_id,
                ref.checkpoint_id,
                ref.state_digest,
                tuple(value.get("source_ids", ())),
                tuple(value.get("receipt_refs", ())),
                tuple(value.get("recovery_refs", ())),
            )

        for raw in handoff.selected_entries:
            entry = ProjectContextEntry.from_dict(_plain(raw))
            current = existing.get(entry.entry_id)
            if current is not None and _canonical(current.to_dict()) != _canonical(
                entry.to_dict()
            ):
                conflicts.append(
                    ConflictEntryV1.create(
                        "DECISION" if entry.category == "decisions" else "PROJECT_CONTEXT",
                        entry.entry_id,
                        left=current.to_dict(),
                        right=entry.to_dict(),
                        evidence_refs=(
                            entry_evidence(receiver_ref, current.to_dict()),
                            entry_evidence(handoff.source, entry.to_dict()),
                        ),
                    )
                )
        report = ConflictReportV1.create(
            None,
            handoff.source,
            receiver_ref,
            "exact" if lineage_ok else "unknown",
            conflicts,
        )
        return HandoffAdmissionV1._evaluate_verified(
            handoff,
            receiver_checkpoint,
            lineage_ok=lineage_ok,
            conflicts=report,
            available_source_ids=tuple(
                sorted(
                    source_id
                    for source_id, anchor in state.sources.items()
                    if anchor.engine_binding is not None
                    or source_id in self._runtime_source_bindings
                )
            ),
        )

    def apply_handoff(
        self,
        handoff: Any,
        *,
        receiver_checkpoint: Optional[ContextCheckpointV2] = None,
        event_id: Optional[str] = None,
    ) -> WorkspaceReceipt:
        from .parallel_context import ContextHandoffV1

        if not isinstance(handoff, ContextHandoffV1):
            raise WorkspaceValidationError("handoff must be ContextHandoffV1")
        state = self._read_state()
        previous = state.applied_handoffs.get(handoff.handoff_id)
        if previous is not None:
            if previous["handoff_digest"] != handoff.handoff_digest:
                raise WorkspaceConflictError()
            return state.receipts[previous["event_id"]]
        checkpoint = self.checkpoint() if receiver_checkpoint is None else receiver_checkpoint
        admission = self.admit_handoff(handoff, checkpoint)
        if admission.decision == "rejected":
            if "POLICY_DOWNGRADE" in admission.reason_codes:
                raise WorkspacePolicyError()
            raise WorkspaceConflictError()
        return self._append(
            "handoff_applied",
            {
                "handoff": _plain(handoff.to_dict()),
                "admission": _plain(admission.to_dict()),
            },
            event_id=event_id,
        )

    def narrow_reconciliation(
        self,
        other: "ContextWorkspace",
        ancestor: ContextCheckpointV2,
        left: ContextCheckpointV2,
        right: ContextCheckpointV2,
        *,
        mode: str,
        reconciliation_id: Optional[str] = None,
        accepted_handoff: Optional[Any] = None,
        admission: Optional[Any] = None,
    ) -> Any:
        from .parallel_context import (
            ContextHandoffV1,
            ForkLineageV1,
            HandoffAdmissionV1,
            NarrowReconciliationV1,
            WorkspaceStateRefV1,
        )

        if not isinstance(other, ContextWorkspace) or other is self:
            raise WorkspaceValidationError("other must be a distinct ContextWorkspace")
        left_state = self._read_state()
        right_state = other._read_state()
        if (
            left_state.checkpoints.get(left.checkpoint_id) != left
            or right_state.checkpoints.get(right.checkpoint_id) != right
            or left_state.fork_lineage is None
            or right_state.fork_lineage is None
        ):
            raise WorkspaceConflictError()
        left_lineage = ForkLineageV1.from_dict(left_state.fork_lineage)
        right_lineage = ForkLineageV1.from_dict(right_state.fork_lineage)
        for lineage in (left_lineage, right_lineage):
            if (
                lineage.parent_workspace_id != ancestor.workspace_id
                or lineage.parent_checkpoint_id != ancestor.checkpoint_id
                or lineage.parent_checkpoint_state_digest != ancestor.state_digest
                or lineage.parent_checkpoint_envelope_digest != ancestor.envelope_digest
            ):
                raise WorkspaceConflictError()

        if mode == "accepted_handoff":
            if not isinstance(accepted_handoff, ContextHandoffV1) or not isinstance(
                admission, HandoffAdmissionV1
            ):
                raise WorkspaceValidationError(
                    "accepted_handoff mode requires exact handoff and admission"
                )
            left_ref = WorkspaceStateRefV1.from_checkpoint(
                left, fork_lineage=left_lineage
            )
            right_ref = WorkspaceStateRefV1.from_checkpoint(
                right, fork_lineage=right_lineage
            )
            if (
                accepted_handoff.source != left_ref
                or accepted_handoff.target_workspace_id != right.workspace_id
                or admission.receiver_workspace_id != right.workspace_id
                or admission.conflicts.left != left_ref
                or admission.conflicts.right != right_ref
            ):
                raise WorkspaceConflictError()

        return NarrowReconciliationV1.between(
            ancestor,
            left,
            right,
            mode=mode,
            reconciliation_id=reconciliation_id,
            accepted_handoff=accepted_handoff,
            admission=admission,
            left_lineage=left_lineage,
            right_lineage=right_lineage,
        )

    def restore(
        self,
        checkpoint: ContextCheckpointV2,
        *,
        event_id: Optional[str] = None,
    ) -> WorkspaceReceipt:
        if not isinstance(checkpoint, ContextCheckpointV2):
            raise WorkspaceValidationError("checkpoint must be ContextCheckpointV2")
        if checkpoint.workspace_id != self.workspace_id:
            raise WorkspaceConflictError()
        state = self._read_state()
        stored = state.checkpoints.get(checkpoint.checkpoint_id)
        if stored is None or _canonical(stored.to_dict()) != _canonical(checkpoint.to_dict()):
            raise WorkspaceConflictError()
        _verify_checkpoint_sources(checkpoint)
        receipt = self._append(
            "workspace_restored",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_envelope_digest": checkpoint.envelope_digest,
            },
            event_id=event_id,
            pre_publish=lambda: _verify_checkpoint_sources(checkpoint),
        )
        restored = self._read_state()
        if restored.logical_digest() != checkpoint.state_digest:
            raise WorkspaceCorruptError()
        return receipt

    def _record_sealed_package(self, inspection: Any) -> WorkspaceReceipt:
        checkpoint = inspection.checkpoint
        if not isinstance(checkpoint, ContextCheckpointV2):
            raise WorkspaceValidationError("sealed package checkpoint is invalid")
        if checkpoint.workspace_id != self.workspace_id:
            raise WorkspaceConflictError()
        package = {
            "name": _text(inspection.package_name, "package name", 128),
            "version": _text(inspection.package_version, "package version", 64),
            "package_digest": _digest_value(
                inspection.package_digest, "package digest"
            ),
            "content_hash": _digest_value(inspection.content_hash, "content hash"),
            "signature_state": inspection.signature_state,
            "signer_public_key": inspection.signer_public_key,
        }
        return self._append(
            "workspace_sealed",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_envelope_digest": checkpoint.envelope_digest,
                "package": package,
            },
            event_id=None,
        )

    def _seed_from_package(
        self,
        inspection: Any,
        *,
        trusted_signer: bool,
        allow_unsigned: bool,
    ) -> WorkspaceReceipt:
        checkpoint = inspection.checkpoint
        if not isinstance(checkpoint, ContextCheckpointV2):
            raise WorkspaceValidationError("seed checkpoint is invalid")
        if checkpoint.workspace_id != self.workspace_id:
            raise WorkspaceConflictError()
        package = {
            "name": _text(inspection.package_name, "package name", 128),
            "version": _text(inspection.package_version, "package version", 64),
            "package_digest": _digest_value(
                inspection.package_digest, "package digest"
            ),
            "content_hash": _digest_value(inspection.content_hash, "content hash"),
            "signature_state": inspection.signature_state,
            "signer_public_key": inspection.signer_public_key,
        }
        receipt = self._append(
            "workspace_seeded",
            {
                "checkpoint": _plain(checkpoint.to_dict()),
                "package": package,
                "admission": {
                    "trusted_signer": bool(trusted_signer),
                    "allow_unsigned": bool(allow_unsigned),
                },
            },
            event_id=None,
        )
        if self._read_state().logical_digest() != checkpoint.state_digest:
            raise WorkspaceCorruptError()
        return receipt

    def project_context(
        self,
        *,
        categories: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> ProjectContext:
        state = self._read_state()
        state_policy = _state_policy(state)
        state_identity = _state_identity(state)
        if categories is None:
            selected_categories = set(_CATEGORIES)
        else:
            if isinstance(categories, str):
                raise WorkspaceValidationError("categories must be a collection")
            selected_categories = set(categories)
            if not selected_categories.issubset(_CATEGORIES):
                raise WorkspaceValidationError("categories contains an invalid category")
        if limit is not None:
            _positive_int(limit, "limit")
            if limit > state_policy.max_context_entries:
                raise WorkspacePolicyError()
        if max_bytes is not None:
            _positive_int(max_bytes, "max_bytes")
            if max_bytes > state_policy.max_context_bytes:
                raise WorkspacePolicyError()
        entry_limit = state_policy.max_context_entries if limit is None else limit
        byte_limit = state_policy.max_context_bytes if max_bytes is None else max_bytes
        eligible = [entry for entry in state.entries if entry.category in selected_categories]
        filtered_count = len(state.entries) - len(eligible)
        selected: List[ProjectContextEntry] = []
        if eligible:
            # Find the longest newest suffix satisfying both whole-entry bounds.
            for start in range(len(eligible) - 1, -1, -1):
                candidate = eligible[start:]
                if len(candidate) > entry_limit:
                    continue
                if _state_context_bytes(candidate) <= byte_limit:
                    selected = candidate
                    continue
                if selected:
                    break
        omitted = len(eligible) - len(selected)
        return ProjectContext(
            state_identity.workspace_id,
            state.logical_digest(),
            tuple(selected),
            filtered_count,
            omitted,
        )

    def tighten_policy(
        self,
        policy: WorkspacePolicy,
        *,
        event_id: Optional[str] = None,
    ) -> WorkspaceReceipt:
        if not isinstance(policy, WorkspacePolicy):
            raise WorkspaceValidationError("policy must be WorkspacePolicy")
        if event_id is not None:
            selected_event_id = _uuid(event_id, "event_id")
            current = self._read_state()
            existing = current.receipts.get(selected_event_id)
            if existing is not None:
                event = next(event for event in current.events if event["event_id"] == selected_event_id)
                event_payload = event.get("payload")
                if not isinstance(event_payload, Mapping):
                    raise WorkspaceConflictError()
                if event.get("kind") == "policy_tightened" and _canonical(
                    event_payload.get("policy")
                ) == _canonical(policy.to_dict()):
                    return existing
                raise WorkspaceConflictError()
        state = self._read_state()
        if state.lifecycle != "active":
            raise WorkspaceLifecycleError()
        if not policy.is_tightening(_state_policy(state)):
            raise WorkspacePolicyError()
        if any(entry.category not in policy.allowed_categories for entry in state.entries):
            raise WorkspacePolicyError()
        if not policy.allow_external_sources and any(
            anchor.kind != "filesystem" for anchor in state.sources.values()
        ):
            raise WorkspacePolicyError()
        candidate = state.clone()
        candidate.policy = policy
        try:
            _ensure_state_bounds(candidate, policy, extra_event=True)
        except WorkspacePolicyError:
            raise
        payload = {
            "policy": _plain(policy.to_dict()),
            "previous_policy_digest": _digest(_state_policy(state).to_dict()),
        }
        return self._append("policy_tightened", payload, event_id=event_id)

    def complete(self, *, event_id: Optional[str] = None) -> WorkspaceReceipt:
        return self._append("workspace_completed", {}, event_id=event_id)

    def abort(self, reason_code: str, *, event_id: Optional[str] = None) -> WorkspaceReceipt:
        _text(reason_code, "reason_code", 128)
        _reject_sensitive(reason_code, "reason_code")
        return self._append(
            "workspace_aborted",
            {"reason_code": reason_code},
            event_id=event_id,
        )


__all__ = [
    "ContextCheckpointV2",
    "ContextWorkspace",
    "ProjectContext",
    "ProjectContextEntry",
    "SourceAnchor",
    "SourceFreshness",
    "SourceRecovery",
    "SourceRevision",
    "SourceScope",
    "SourceTrust",
    "WorkspaceIdentity",
    "WorkspacePolicy",
    "WorkspaceReceipt",
    "WorkspaceSessionAttachment",
    "WorkspaceStatus",
]
