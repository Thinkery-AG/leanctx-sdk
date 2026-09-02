"""Preview contracts for parallel local context.

The public SDK v1 root intentionally does not import this module.  Values here
are immutable canonical artifacts; Workspace lifecycle integration lives in
``workspace.py`` and the Engine remains unaware of these Product contracts.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .errors import (
    WorkspaceConflictError,
    WorkspaceIncompatibleError,
    WorkspacePolicyError,
    WorkspaceSensitiveDataError,
    WorkspaceValidationError,
)
from .protocol import canonical_bytes
from .workspace import (
    ContextCheckpointV2,
    PackagePin,
    ProjectContextEntry,
    WorkspacePolicy,
    _source_anchor_from_dict,
)


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_RE = re.compile(
    r"(?i)(?:-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|(?<![A-Za-z0-9])(?:sk-|gh[pousr]_)"
    r"[A-Za-z0-9_-]+|\bBearer\s+\S+|"
    r"(?:api[-_ ]?key|secret|token|password|private[-_ ]?key)"
    r"\s*(?:[:=])\s*[^\s,;}\"']+)"
)
_FORBIDDEN_KEYS = frozenset(
    {
        "credential",
        "credentials",
        "transcript",
        "tool_history",
        "raw_tool_history",
        "api_key",
        "provider_token",
        "cache",
    }
)
_DELTA_CATEGORIES = frozenset(
    {"source", "project_context", "package", "policy", "lineage"}
)
_DELTA_ACTIONS = {
    "source": frozenset(
        {
            "added",
            "removed",
            "revision_changed",
            "freshness_changed",
            "recovery_availability_changed",
        }
    ),
    "project_context": frozenset(
        {"added", "removed", "superseded", "contradicted", "provenance_changed"}
    ),
    "package": frozenset(
        {"pin_added", "pin_removed", "digest_changed", "trust_changed"}
    ),
    "policy": frozenset({"effective_changed"}),
    "lineage": frozenset({"fork_ancestry", "checkpoint_reference"}),
}
_CONFLICT_CATEGORIES = frozenset(
    {"PROJECT_CONTEXT", "SOURCE_REVISION", "PACKAGE", "POLICY", "DECISION"}
)
_MAX_DELTA_ITEMS = 512
_MAX_DELTA_BYTES = 512 * 1024
_MAX_CONFLICTS = 256
_MAX_HANDOFF_ENTRIES = 64
_MAX_HANDOFF_ANCHORS = 128
_MAX_HANDOFF_RECOVERY_REFS = 512
_MAX_HANDOFF_PACKAGE_REFS = 128
_MAX_HANDOFF_BYTES = 256 * 1024


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
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical(value: Any) -> bytes:
    try:
        return canonical_bytes(_plain(value))
    except Exception as exc:
        raise WorkspaceValidationError(
            "parallel-context value is not canonical JSON"
        ) from exc


def _digest(domain: str, value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(domain.encode("utf-8") + b"\n" + _canonical(value)).hexdigest()
    )


def _uuid(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise WorkspaceValidationError(f"{name} must be a canonical lowercase UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise WorkspaceValidationError(
            f"{name} must be a canonical lowercase UUID"
        ) from exc
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise WorkspaceValidationError(f"{name} must be a sha256 digest")
    return value


def _text(value: Any, name: str, maximum: int, *, controls: bool = True) -> str:
    if not isinstance(value, str) or not value:
        raise WorkspaceValidationError(f"{name} must be a non-empty string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise WorkspaceValidationError(f"{name} must be valid UTF-8") from exc
    if len(encoded) > maximum or "\x00" in value:
        raise WorkspaceValidationError(f"{name} exceeds its bound")
    if controls and any(ord(char) < 0x20 for char in value):
        raise WorkspaceValidationError(f"{name} contains a control character")
    return value


def _exact(value: Any, keys: Iterable[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise WorkspaceIncompatibleError(f"{name} fields do not match its v1 contract")
    if any(not isinstance(key, str) for key in value):
        raise WorkspaceIncompatibleError(f"{name} contains a non-string key")
    return value


def _reject_sensitive(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise WorkspaceSensitiveDataError("parallel-context artifact")
            _reject_sensitive(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive(item)
    elif isinstance(value, str) and _SECRET_RE.search(value):
        raise WorkspaceSensitiveDataError("parallel-context artifact")


def _sorted_unique_text(
    values: Sequence[Any], name: str, maximum: int
) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise WorkspaceValidationError(f"{name} must be an array")
    if len(values) > maximum:
        raise WorkspacePolicyError()
    selected = tuple(sorted({_text(value, name, 512) for value in values}))
    if len(selected) != len(values):
        raise WorkspaceValidationError(f"{name} must contain unique values")
    return selected


def _record_digest(domain: str, value: Mapping[str, Any], field: str) -> str:
    unsigned = {key: _plain(item) for key, item in value.items() if key != field}
    return _digest(domain, unsigned)


def _content_state(logical_state: Mapping[str, Any]) -> Mapping[str, Any]:
    result = _plain(logical_state)
    result.pop("workspace_id", None)
    for source in result.get("sources", []):
        source["engine_binding"] = None
    return result


def _portable_anchor(value: Mapping[str, Any]) -> Mapping[str, Any]:
    result = _plain(value)
    result["engine_binding"] = None
    return result


@dataclass(frozen=True)
class WorkspaceStateRefV1:
    workspace_id: str
    checkpoint_id: str
    state_digest: str
    checkpoint_envelope_digest: str
    fork_lineage: Optional["ForkLineageV1"] = None

    SCHEMA = "leanctx.workspace-state-ref/v1"

    def __post_init__(self) -> None:
        _uuid(self.workspace_id, "workspace_id")
        _uuid(self.checkpoint_id, "checkpoint_id")
        _sha(self.state_digest, "state_digest")
        _sha(self.checkpoint_envelope_digest, "checkpoint_envelope_digest")
        if self.fork_lineage is not None:
            if not isinstance(self.fork_lineage, ForkLineageV1):
                raise WorkspaceValidationError("fork_lineage must be ForkLineageV1")
            if self.fork_lineage.child_workspace_id != self.workspace_id:
                raise WorkspaceConflictError()

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: ContextCheckpointV2,
        *,
        fork_lineage: Optional["ForkLineageV1"] = None,
    ) -> "WorkspaceStateRefV1":
        if not isinstance(checkpoint, ContextCheckpointV2):
            raise WorkspaceValidationError("checkpoint must be ContextCheckpointV2")
        return cls(
            checkpoint.workspace_id,
            checkpoint.checkpoint_id,
            checkpoint.state_digest,
            checkpoint.envelope_digest,
            fork_lineage,
        )

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "workspace_id": self.workspace_id,
            "checkpoint_id": self.checkpoint_id,
            "state_digest": self.state_digest,
            "checkpoint_envelope_digest": self.checkpoint_envelope_digest,
            "fork_lineage": (
                _plain(self.fork_lineage.to_dict()) if self.fork_lineage else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "WorkspaceStateRefV1":
        value = _exact(
            value,
            {
                "schema_version",
                "workspace_id",
                "checkpoint_id",
                "state_digest",
                "checkpoint_envelope_digest",
                "fork_lineage",
            },
            "WorkspaceStateRefV1",
        )
        if value["schema_version"] != cls.SCHEMA:
            raise WorkspaceIncompatibleError("unsupported WorkspaceStateRefV1 schema")
        return cls(
            value["workspace_id"],
            value["checkpoint_id"],
            value["state_digest"],
            value["checkpoint_envelope_digest"],
            (
                ForkLineageV1.from_dict(value["fork_lineage"])
                if value["fork_lineage"] is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ForkLineageV1:
    child_workspace_id: str
    parent_workspace_id: str
    parent_checkpoint_id: str
    parent_checkpoint_state_digest: str
    parent_checkpoint_envelope_digest: str
    fork_id: str
    fork_event_ref: str

    SCHEMA = "leanctx.fork-lineage/v1"
    CONTRACT = "leanctx.workspace-fork/v1"

    def __post_init__(self) -> None:
        _uuid(self.child_workspace_id, "child_workspace_id")
        _uuid(self.parent_workspace_id, "parent_workspace_id")
        if self.child_workspace_id == self.parent_workspace_id:
            raise WorkspaceConflictError()
        _uuid(self.parent_checkpoint_id, "parent_checkpoint_id")
        _sha(self.parent_checkpoint_state_digest, "parent checkpoint state digest")
        _sha(
            self.parent_checkpoint_envelope_digest, "parent checkpoint envelope digest"
        )
        _uuid(self.fork_id, "fork_id")
        if not isinstance(
            self.fork_event_ref, str
        ) or not self.fork_event_ref.startswith("event-id:"):
            raise WorkspaceValidationError(
                "fork_event_ref must be an event-id reference"
            )
        _uuid(self.fork_event_ref.removeprefix("event-id:"), "fork event id")

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "child_workspace_id": self.child_workspace_id,
            "parent_workspace_id": self.parent_workspace_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "parent_checkpoint_state_digest": self.parent_checkpoint_state_digest,
            "parent_checkpoint_envelope_digest": self.parent_checkpoint_envelope_digest,
            "fork_contract": self.CONTRACT,
            "fork_id": self.fork_id,
            "fork_event_ref": self.fork_event_ref,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ForkLineageV1":
        value = _exact(
            value,
            {
                "schema_version",
                "child_workspace_id",
                "parent_workspace_id",
                "parent_checkpoint_id",
                "parent_checkpoint_state_digest",
                "parent_checkpoint_envelope_digest",
                "fork_contract",
                "fork_id",
                "fork_event_ref",
            },
            "ForkLineageV1",
        )
        if (
            value["schema_version"] != cls.SCHEMA
            or value["fork_contract"] != cls.CONTRACT
        ):
            raise WorkspaceIncompatibleError("unsupported fork lineage")
        return cls(
            value["child_workspace_id"],
            value["parent_workspace_id"],
            value["parent_checkpoint_id"],
            value["parent_checkpoint_state_digest"],
            value["parent_checkpoint_envelope_digest"],
            value["fork_id"],
            value["fork_event_ref"],
        )


@dataclass(frozen=True)
class EvidenceRefV1:
    """Checkpoint-bound, portable provenance for one semantic delta item."""

    workspace_id: str
    checkpoint_id: str
    state_digest: str
    source_ids: Tuple[str, ...] = ()
    receipt_refs: Tuple[str, ...] = ()
    recovery_refs: Tuple[str, ...] = ()

    SCHEMA = "leanctx.evidence-ref/v1"

    def __post_init__(self) -> None:
        _uuid(self.workspace_id, "workspace_id")
        _uuid(self.checkpoint_id, "checkpoint_id")
        _sha(self.state_digest, "state_digest")
        object.__setattr__(
            self,
            "source_ids",
            _sorted_unique_text(self.source_ids, "source_ids", _MAX_HANDOFF_ANCHORS),
        )
        object.__setattr__(
            self,
            "receipt_refs",
            _sorted_unique_text(
                self.receipt_refs, "receipt_refs", _MAX_HANDOFF_RECOVERY_REFS
            ),
        )
        object.__setattr__(
            self,
            "recovery_refs",
            _sorted_unique_text(
                self.recovery_refs, "recovery_refs", _MAX_HANDOFF_RECOVERY_REFS
            ),
        )

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "workspace_id": self.workspace_id,
            "checkpoint_id": self.checkpoint_id,
            "state_digest": self.state_digest,
            "source_ids": list(self.source_ids),
            "receipt_refs": list(self.receipt_refs),
            "recovery_refs": list(self.recovery_refs),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "EvidenceRefV1":
        value = _exact(
            value,
            {
                "schema_version",
                "workspace_id",
                "checkpoint_id",
                "state_digest",
                "source_ids",
                "receipt_refs",
                "recovery_refs",
            },
            "EvidenceRefV1",
        )
        if value["schema_version"] != cls.SCHEMA or any(
            not isinstance(value[name], list)
            for name in ("source_ids", "receipt_refs", "recovery_refs")
        ):
            raise WorkspaceIncompatibleError("unsupported EvidenceRefV1")
        return cls(
            value["workspace_id"],
            value["checkpoint_id"],
            value["state_digest"],
            tuple(value["source_ids"]),
            tuple(value["receipt_refs"]),
            tuple(value["recovery_refs"]),
        )


@dataclass(frozen=True)
class PolicyInheritanceV1:
    parent_policy: WorkspacePolicy
    requested_child_policy: WorkspacePolicy
    effective_child_policy: WorkspacePolicy
    relation: str
    parent_policy_digest: str
    inheritance_digest: str

    SCHEMA = "leanctx.policy-inheritance/v1"

    @classmethod
    def create(
        cls,
        parent: WorkspacePolicy,
        requested: Optional[WorkspacePolicy] = None,
    ) -> "PolicyInheritanceV1":
        if not isinstance(parent, WorkspacePolicy):
            raise WorkspaceValidationError("parent policy is invalid")
        selected = parent if requested is None else requested
        if not isinstance(selected, WorkspacePolicy) or not selected.is_tightening(
            parent
        ):
            raise WorkspacePolicyError()
        relation = "equal" if selected == parent else "tightened"
        base = {
            "schema_version": cls.SCHEMA,
            "parent_policy": _plain(parent.to_dict()),
            "parent_policy_digest": _digest(
                "leanctx.workspace.policy.v1", parent.to_dict()
            ),
            "requested_child_policy": _plain(selected.to_dict()),
            "effective_child_policy": _plain(selected.to_dict()),
            "relation": relation,
        }
        return cls(
            parent,
            selected,
            selected,
            relation,
            base["parent_policy_digest"],
            _digest("leanctx.policy-inheritance.v1", base),
        )

    def __post_init__(self) -> None:
        if self.relation not in {"equal", "tightened"}:
            raise WorkspaceValidationError("policy inheritance relation is invalid")
        if not self.effective_child_policy.is_tightening(self.parent_policy):
            raise WorkspacePolicyError()
        if self.requested_child_policy != self.effective_child_policy:
            raise WorkspaceConflictError()
        expected_relation = (
            "equal"
            if self.effective_child_policy == self.parent_policy
            else "tightened"
        )
        if self.relation != expected_relation:
            raise WorkspaceConflictError()
        _sha(self.parent_policy_digest, "parent_policy_digest")
        _sha(self.inheritance_digest, "inheritance_digest")
        if self.parent_policy_digest != _digest(
            "leanctx.workspace.policy.v1", self.parent_policy.to_dict()
        ):
            raise WorkspaceConflictError()
        unsigned = {
            "schema_version": self.SCHEMA,
            "parent_policy": _plain(self.parent_policy.to_dict()),
            "parent_policy_digest": self.parent_policy_digest,
            "requested_child_policy": _plain(self.requested_child_policy.to_dict()),
            "effective_child_policy": _plain(self.effective_child_policy.to_dict()),
            "relation": self.relation,
        }
        if self.inheritance_digest != _digest(
            "leanctx.policy-inheritance.v1", unsigned
        ):
            raise WorkspaceConflictError()

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "parent_policy": _plain(self.parent_policy.to_dict()),
            "parent_policy_digest": self.parent_policy_digest,
            "requested_child_policy": _plain(self.requested_child_policy.to_dict()),
            "effective_child_policy": _plain(self.effective_child_policy.to_dict()),
            "relation": self.relation,
            "inheritance_digest": self.inheritance_digest,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PolicyInheritanceV1":
        value = _exact(
            value,
            {
                "schema_version",
                "parent_policy",
                "parent_policy_digest",
                "requested_child_policy",
                "effective_child_policy",
                "relation",
                "inheritance_digest",
            },
            "PolicyInheritanceV1",
        )
        if value["schema_version"] != cls.SCHEMA:
            raise WorkspaceIncompatibleError("unsupported policy inheritance")
        parent = WorkspacePolicy.from_dict(value["parent_policy"])
        requested = WorkspacePolicy.from_dict(value["requested_child_policy"])
        effective = WorkspacePolicy.from_dict(value["effective_child_policy"])
        expected = cls.create(parent, requested)
        if effective != expected.effective_child_policy or _plain(
            expected.to_dict()
        ) != _plain(value):
            raise WorkspaceConflictError()
        return expected


@dataclass(frozen=True)
class WorkspaceForkV1:
    fork_id: str
    source: WorkspaceStateRefV1
    child_workspace_id: str
    child_name: str
    lineage: ForkLineageV1
    policy_inheritance: PolicyInheritanceV1
    package_lock_digest: Optional[str]
    inherited_content_digest: str
    execution_ref: Optional[str]
    fork_digest: str

    SCHEMA = "leanctx.workspace-fork/v1"

    @classmethod
    def create(
        cls,
        checkpoint: ContextCheckpointV2,
        child_workspace_id: str,
        child_name: str,
        event_id: str,
        *,
        requested_policy: Optional[WorkspacePolicy] = None,
        fork_id: Optional[str] = None,
        execution_ref: Optional[str] = None,
    ) -> "WorkspaceForkV1":
        selected_fork_id = (
            str(uuid.uuid4()) if fork_id is None else _uuid(fork_id, "fork_id")
        )
        child_workspace_id = _uuid(child_workspace_id, "child_workspace_id")
        _text(child_name, "child_name", 128)
        event_id = _uuid(event_id, "event_id")
        logical = checkpoint.logical_state
        parent_policy = WorkspacePolicy.from_dict(_plain(logical["policy"]))
        policy = PolicyInheritanceV1.create(parent_policy, requested_policy)
        lineage = ForkLineageV1(
            child_workspace_id,
            checkpoint.workspace_id,
            checkpoint.checkpoint_id,
            checkpoint.state_digest,
            checkpoint.envelope_digest,
            selected_fork_id,
            "event-id:" + event_id,
        )
        content_digest = _digest(
            "leanctx.fork.inherited-content.v1", _content_state(logical)
        )
        if execution_ref is not None:
            _text(execution_ref, "execution_ref", 512)
        base = {
            "schema_version": cls.SCHEMA,
            "fork_id": selected_fork_id,
            "source": _plain(WorkspaceStateRefV1.from_checkpoint(checkpoint).to_dict()),
            "child_workspace_id": child_workspace_id,
            "child_name": child_name,
            "lineage": _plain(lineage.to_dict()),
            "policy_inheritance": _plain(policy.to_dict()),
            "package_lock_digest": checkpoint.package_lock_digest,
            "inherited_content_digest": content_digest,
            "execution_ref": execution_ref,
        }
        return cls(
            selected_fork_id,
            WorkspaceStateRefV1.from_checkpoint(checkpoint),
            child_workspace_id,
            child_name,
            lineage,
            policy,
            checkpoint.package_lock_digest,
            content_digest,
            execution_ref,
            _digest("leanctx.workspace-fork.v1", base),
        )

    def __post_init__(self) -> None:
        _uuid(self.fork_id, "fork_id")
        _uuid(self.child_workspace_id, "child_workspace_id")
        _text(self.child_name, "child_name", 128)
        if (
            self.fork_id != self.lineage.fork_id
            or self.child_workspace_id != self.lineage.child_workspace_id
        ):
            raise WorkspaceConflictError()
        if (
            self.source.workspace_id != self.lineage.parent_workspace_id
            or self.source.checkpoint_id != self.lineage.parent_checkpoint_id
            or self.source.state_digest != self.lineage.parent_checkpoint_state_digest
            or self.source.checkpoint_envelope_digest
            != self.lineage.parent_checkpoint_envelope_digest
        ):
            raise WorkspaceConflictError()
        if self.package_lock_digest is not None:
            _sha(self.package_lock_digest, "package_lock_digest")
        _sha(self.inherited_content_digest, "inherited_content_digest")
        _sha(self.fork_digest, "fork_digest")
        if self.execution_ref is not None:
            _text(self.execution_ref, "execution_ref", 512)
        _reject_sensitive(
            {"child_name": self.child_name, "execution_ref": self.execution_ref}
        )
        if self.fork_digest != _digest(
            "leanctx.workspace-fork.v1", self._digest_dict()
        ):
            raise WorkspaceConflictError()

    def _digest_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "fork_id": self.fork_id,
            "source": _plain(self.source.to_dict()),
            "child_workspace_id": self.child_workspace_id,
            "child_name": self.child_name,
            "lineage": _plain(self.lineage.to_dict()),
            "policy_inheritance": _plain(self.policy_inheritance.to_dict()),
            "package_lock_digest": self.package_lock_digest,
            "inherited_content_digest": self.inherited_content_digest,
            "execution_ref": self.execution_ref,
        }

    def to_dict(self) -> Mapping[str, Any]:
        return dict(self._digest_dict(), fork_digest=self.fork_digest)

    @classmethod
    def from_dict(cls, value: Any) -> "WorkspaceForkV1":
        value = _exact(
            value,
            {
                "schema_version",
                "fork_id",
                "source",
                "child_workspace_id",
                "child_name",
                "lineage",
                "policy_inheritance",
                "package_lock_digest",
                "inherited_content_digest",
                "execution_ref",
                "fork_digest",
            },
            "WorkspaceForkV1",
        )
        if value["schema_version"] != cls.SCHEMA:
            raise WorkspaceIncompatibleError("unsupported WorkspaceForkV1")
        result = cls(
            value["fork_id"],
            WorkspaceStateRefV1.from_dict(value["source"]),
            value["child_workspace_id"],
            value["child_name"],
            ForkLineageV1.from_dict(value["lineage"]),
            PolicyInheritanceV1.from_dict(value["policy_inheritance"]),
            value["package_lock_digest"],
            value["inherited_content_digest"],
            value["execution_ref"],
            value["fork_digest"],
        )
        if (
            _digest("leanctx.workspace-fork.v1", result._digest_dict())
            != result.fork_digest
        ):
            raise WorkspaceConflictError()
        return result


@dataclass(frozen=True)
class ConflictEntryV1:
    category: str
    stable_key: str
    base: Optional[Mapping[str, Any]]
    left: Optional[Mapping[str, Any]]
    right: Optional[Mapping[str, Any]]
    evidence_refs: Tuple[EvidenceRefV1, ...]
    conflict_id: str

    SCHEMA = "leanctx.conflict-entry/v1"

    def __post_init__(self) -> None:
        if self.category not in _CONFLICT_CATEGORIES:
            raise WorkspaceValidationError("conflict category is invalid")
        _text(self.stable_key, "stable_key", 512)
        _sha(self.conflict_id, "conflict_id")
        for name in ("base", "left", "right"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _freeze(_plain(value)))
        if any(not isinstance(ref, EvidenceRefV1) for ref in self.evidence_refs):
            raise WorkspaceValidationError("conflict evidence refs are invalid")
        refs = tuple(
            sorted(self.evidence_refs, key=lambda ref: _canonical(ref.to_dict()))
        )
        if self.evidence_refs != refs:
            raise WorkspaceValidationError("conflict evidence refs are not canonical")
        object.__setattr__(self, "evidence_refs", refs)
        payload = {
            "schema_version": self.SCHEMA,
            "category": self.category,
            "stable_key": self.stable_key,
            "base": _plain(self.base),
            "left": _plain(self.left),
            "right": _plain(self.right),
            "evidence_refs": [_plain(ref.to_dict()) for ref in self.evidence_refs],
            "resolution": "manual_required",
        }
        if self.conflict_id != _digest("leanctx.conflict-entry.v1", payload):
            raise WorkspaceConflictError()

    @classmethod
    def create(
        cls,
        category: str,
        stable_key: str,
        *,
        base: Optional[Mapping[str, Any]] = None,
        left: Optional[Mapping[str, Any]] = None,
        right: Optional[Mapping[str, Any]] = None,
        evidence_refs: Sequence[EvidenceRefV1] = (),
    ) -> "ConflictEntryV1":
        if any(not isinstance(ref, EvidenceRefV1) for ref in evidence_refs):
            raise WorkspaceValidationError("conflict evidence refs are invalid")
        payload = {
            "schema_version": cls.SCHEMA,
            "category": category,
            "stable_key": stable_key,
            "base": _plain(base),
            "left": _plain(left),
            "right": _plain(right),
            "evidence_refs": [
                _plain(ref.to_dict())
                for ref in sorted(
                    evidence_refs, key=lambda ref: _canonical(ref.to_dict())
                )
            ],
            "resolution": "manual_required",
        }
        return cls(
            category,
            stable_key,
            _freeze(_plain(base)) if base is not None else None,
            _freeze(_plain(left)) if left is not None else None,
            _freeze(_plain(right)) if right is not None else None,
            tuple(sorted(evidence_refs, key=lambda ref: _canonical(ref.to_dict()))),
            _digest("leanctx.conflict-entry.v1", payload),
        )

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "conflict_id": self.conflict_id,
            "category": self.category,
            "stable_key": self.stable_key,
            "base": _plain(self.base),
            "left": _plain(self.left),
            "right": _plain(self.right),
            "evidence_refs": [_plain(ref.to_dict()) for ref in self.evidence_refs],
            "resolution": "manual_required",
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ConflictEntryV1":
        value = _exact(
            value,
            {
                "schema_version",
                "conflict_id",
                "category",
                "stable_key",
                "base",
                "left",
                "right",
                "evidence_refs",
                "resolution",
            },
            "ConflictEntryV1",
        )
        if (
            value["schema_version"] != cls.SCHEMA
            or value["resolution"] != "manual_required"
            or not isinstance(value["evidence_refs"], list)
        ):
            raise WorkspaceIncompatibleError("invalid conflict entry")
        result = cls.create(
            value["category"],
            value["stable_key"],
            base=value["base"],
            left=value["left"],
            right=value["right"],
            evidence_refs=tuple(
                EvidenceRefV1.from_dict(ref) for ref in value["evidence_refs"]
            ),
        )
        if _plain(result.to_dict()) != _plain(value):
            raise WorkspaceConflictError()
        return result


@dataclass(frozen=True)
class ConflictReportV1:
    base: Optional[WorkspaceStateRefV1]
    left: WorkspaceStateRefV1
    right: WorkspaceStateRefV1
    ancestry: str
    entries: Tuple[ConflictEntryV1, ...]
    report_id: str

    SCHEMA = "leanctx.conflict-report/v1"

    def __post_init__(self) -> None:
        if self.ancestry not in {"exact", "diverged", "unknown"}:
            raise WorkspaceValidationError("ancestry is invalid")
        if len(self.entries) > _MAX_CONFLICTS:
            raise WorkspacePolicyError()
        expected = tuple(
            sorted(
                self.entries,
                key=lambda item: (item.category, item.stable_key, item.conflict_id),
            )
        )
        if tuple(self.entries) != expected:
            raise WorkspaceValidationError("conflict entries are not canonical")
        unsigned = {
            "schema_version": self.SCHEMA,
            "base": _plain(self.base.to_dict()) if self.base else None,
            "left": _plain(self.left.to_dict()),
            "right": _plain(self.right.to_dict()),
            "ancestry": self.ancestry,
            "entries": [_plain(item.to_dict()) for item in self.entries],
        }
        if self.report_id != _digest("leanctx.conflict-report.v1", unsigned):
            raise WorkspaceConflictError()

    @classmethod
    def create(
        cls,
        base: Optional[WorkspaceStateRefV1],
        left: WorkspaceStateRefV1,
        right: WorkspaceStateRefV1,
        ancestry: str,
        entries: Sequence[ConflictEntryV1],
    ) -> "ConflictReportV1":
        if ancestry not in {"exact", "diverged", "unknown"}:
            raise WorkspaceValidationError("ancestry is invalid")
        selected = tuple(
            sorted(
                entries,
                key=lambda item: (item.category, item.stable_key, item.conflict_id),
            )
        )
        if len(selected) > _MAX_CONFLICTS:
            raise WorkspacePolicyError()
        base_dict = {
            "schema_version": cls.SCHEMA,
            "base": _plain(base.to_dict()) if base else None,
            "left": _plain(left.to_dict()),
            "right": _plain(right.to_dict()),
            "ancestry": ancestry,
            "entries": [_plain(item.to_dict()) for item in selected],
        }
        return cls(
            base,
            left,
            right,
            ancestry,
            selected,
            _digest("leanctx.conflict-report.v1", base_dict),
        )

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "base": _plain(self.base.to_dict()) if self.base else None,
            "left": _plain(self.left.to_dict()),
            "right": _plain(self.right.to_dict()),
            "ancestry": self.ancestry,
            "entries": [_plain(entry.to_dict()) for entry in self.entries],
            "report_id": self.report_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ConflictReportV1":
        value = _exact(
            value,
            {
                "schema_version",
                "base",
                "left",
                "right",
                "ancestry",
                "entries",
                "report_id",
            },
            "ConflictReportV1",
        )
        if value["schema_version"] != cls.SCHEMA or not isinstance(
            value["entries"], list
        ):
            raise WorkspaceIncompatibleError("unsupported ConflictReportV1")
        result = cls.create(
            WorkspaceStateRefV1.from_dict(value["base"])
            if value["base"] is not None
            else None,
            WorkspaceStateRefV1.from_dict(value["left"]),
            WorkspaceStateRefV1.from_dict(value["right"]),
            value["ancestry"],
            [ConflictEntryV1.from_dict(entry) for entry in value["entries"]],
        )
        if _plain(result.to_dict()) != _plain(value):
            raise WorkspaceConflictError()
        return result


@dataclass(frozen=True)
class DeltaItemV1:
    category: str
    action: str
    stable_key: str
    before: Optional[Mapping[str, Any]]
    after: Optional[Mapping[str, Any]]
    evidence_refs: Tuple[EvidenceRefV1, ...] = ()

    SCHEMA = "leanctx.delta-item/v1"

    def __post_init__(self) -> None:
        if self.category not in _DELTA_CATEGORIES:
            raise WorkspaceValidationError("delta category is invalid")
        if self.action not in _DELTA_ACTIONS[self.category]:
            raise WorkspaceValidationError("delta category/action pair is invalid")
        _text(self.stable_key, "stable_key", 512)
        for name in ("before", "after"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _freeze(_plain(value)))
        if not self.evidence_refs or any(
            not isinstance(ref, EvidenceRefV1) for ref in self.evidence_refs
        ):
            raise WorkspaceValidationError("factual delta item requires EvidenceRefV1")
        refs = tuple(
            sorted(self.evidence_refs, key=lambda ref: _canonical(ref.to_dict()))
        )
        object.__setattr__(self, "evidence_refs", refs)

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "category": self.category,
            "action": self.action,
            "stable_key": self.stable_key,
            "before": _plain(self.before),
            "after": _plain(self.after),
            "evidence_refs": [_plain(ref.to_dict()) for ref in self.evidence_refs],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "DeltaItemV1":
        value = _exact(
            value,
            {
                "schema_version",
                "category",
                "action",
                "stable_key",
                "before",
                "after",
                "evidence_refs",
            },
            "DeltaItemV1",
        )
        if value["schema_version"] != cls.SCHEMA or not isinstance(
            value["evidence_refs"], list
        ):
            raise WorkspaceIncompatibleError("unsupported DeltaItemV1")
        result = cls(
            value["category"],
            value["action"],
            value["stable_key"],
            value["before"],
            value["after"],
            tuple(EvidenceRefV1.from_dict(ref) for ref in value["evidence_refs"]),
        )
        if _plain(result.to_dict()) != _plain(value):
            raise WorkspaceConflictError()
        return result


def _evidence_ref(
    checkpoint: ContextCheckpointV2,
    value: Optional[Mapping[str, Any]] = None,
) -> EvidenceRefV1:
    selected = {} if value is None else value
    source_ids = list(selected.get("source_ids", []))
    source_id = selected.get("source_id")
    if isinstance(source_id, str):
        source_ids.append(source_id)
    return EvidenceRefV1(
        checkpoint.workspace_id,
        checkpoint.checkpoint_id,
        checkpoint.state_digest,
        tuple(sorted(set(source_ids))),
        tuple(sorted(set(selected.get("receipt_refs", [])))),
        tuple(sorted(set(selected.get("recovery_refs", [])))),
    )


def _keyed(values: Any, field: str, label: str) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(values, (list, tuple)):
        raise WorkspaceIncompatibleError(f"{label} must be an array")
    result = {}
    for raw in values:
        if not isinstance(raw, Mapping) or not isinstance(raw.get(field), str):
            raise WorkspaceIncompatibleError(f"{label} item is invalid")
        key = raw[field]
        if key in result:
            raise WorkspaceConflictError()
        result[key] = _plain(raw)
    return result


@dataclass(frozen=True)
class ContextDeltaV1:
    base: WorkspaceStateRefV1
    target: WorkspaceStateRefV1
    items: Tuple[DeltaItemV1, ...]
    conflicts: ConflictReportV1
    delta_id: str

    SCHEMA = "leanctx.context-delta/v1"

    def __post_init__(self) -> None:
        if len(self.items) > _MAX_DELTA_ITEMS:
            raise WorkspacePolicyError()
        expected = tuple(
            sorted(
                self.items,
                key=lambda item: (
                    item.category,
                    item.stable_key,
                    item.action,
                    _digest("leanctx.delta-item.v1", item.to_dict()),
                ),
            )
        )
        if tuple(self.items) != expected:
            raise WorkspaceValidationError("delta items are not canonical")
        if (
            self.conflicts.base != self.base
            or self.conflicts.left != self.base
            or self.conflicts.right != self.target
        ):
            raise WorkspaceConflictError()
        unsigned = self._unsigned_dict()
        if len(_canonical(unsigned)) > _MAX_DELTA_BYTES:
            raise WorkspacePolicyError()
        if self.delta_id != _digest("leanctx.context-delta.v1", unsigned):
            raise WorkspaceConflictError()

    def _unsigned_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "base": _plain(self.base.to_dict()),
            "target": _plain(self.target.to_dict()),
            "items": [_plain(item.to_dict()) for item in self.items],
            "conflicts": _plain(self.conflicts.to_dict()),
            "bounds": {"max_items": _MAX_DELTA_ITEMS, "max_bytes": _MAX_DELTA_BYTES},
        }

    @classmethod
    def between(
        cls,
        base: ContextCheckpointV2,
        target: ContextCheckpointV2,
    ) -> "ContextDeltaV1":
        """Compare checkpoint content without claiming unverified ancestry."""
        return cls._between_verified(base, target, ancestry="unknown")

    @classmethod
    def _between_verified(
        cls,
        base: ContextCheckpointV2,
        target: ContextCheckpointV2,
        *,
        ancestry: str,
        base_lineage: Optional[ForkLineageV1] = None,
        target_lineage: Optional[ForkLineageV1] = None,
    ) -> "ContextDeltaV1":
        if not isinstance(base, ContextCheckpointV2) or not isinstance(
            target, ContextCheckpointV2
        ):
            raise WorkspaceValidationError(
                "delta inputs must be ContextCheckpointV2 values"
            )
        if ancestry not in {"exact", "diverged", "unknown"}:
            raise WorkspaceValidationError("ancestry is invalid")
        base_ref = WorkspaceStateRefV1.from_checkpoint(base, fork_lineage=base_lineage)
        target_ref = WorkspaceStateRefV1.from_checkpoint(
            target, fork_lineage=target_lineage
        )
        left_state = _plain(base.logical_state)
        right_state = _plain(target.logical_state)
        for state in (left_state, right_state):
            for source in state["sources"]:
                source["engine_binding"] = None
        items = []
        conflicts = []

        left_sources = _keyed(left_state["sources"], "source_id", "sources")
        right_sources = _keyed(right_state["sources"], "source_id", "sources")
        for key in sorted(set(left_sources) | set(right_sources)):
            before, after = left_sources.get(key), right_sources.get(key)
            refs = tuple(
                _evidence_ref(cp, value)
                for cp, value in ((base, before), (target, after))
                if value is not None
            )
            if before is None:
                items.append(DeltaItemV1("source", "added", key, None, after, refs))
            elif after is None:
                items.append(DeltaItemV1("source", "removed", key, before, None, refs))
            elif _canonical(before) != _canonical(after):
                actions = []
                if before.get("revision") != after.get("revision"):
                    actions.append("revision_changed")
                if before.get("freshness") != after.get("freshness"):
                    actions.append("freshness_changed")
                if before.get("recovery") != after.get("recovery"):
                    actions.append("recovery_availability_changed")
                remaining_before = {
                    name: value
                    for name, value in before.items()
                    if name not in {"revision", "freshness", "recovery"}
                }
                remaining_after = {
                    name: value
                    for name, value in after.items()
                    if name not in {"revision", "freshness", "recovery"}
                }
                if not actions or _canonical(remaining_before) != _canonical(
                    remaining_after
                ):
                    raise WorkspaceConflictError()
                for action in actions:
                    items.append(
                        DeltaItemV1("source", action, key, before, after, refs)
                    )
                if "revision_changed" in actions:
                    conflicts.append(
                        ConflictEntryV1.create(
                            "SOURCE_REVISION",
                            key,
                            left=before,
                            right=after,
                            evidence_refs=refs,
                        )
                    )

        left_entries = _keyed(left_state["entries"], "entry_id", "entries")
        right_entries = _keyed(right_state["entries"], "entry_id", "entries")
        for key in sorted(set(left_entries) | set(right_entries)):
            before, after = left_entries.get(key), right_entries.get(key)
            refs = tuple(
                _evidence_ref(cp, value)
                for cp, value in ((base, before), (target, after))
                if value is not None
            )
            if before is None:
                items.append(
                    DeltaItemV1("project_context", "added", key, None, after, refs)
                )
            elif after is None:
                items.append(
                    DeltaItemV1("project_context", "removed", key, before, None, refs)
                )
            elif _canonical(before) != _canonical(after):
                semantic_before = (before.get("category"), before.get("value"))
                semantic_after = (after.get("category"), after.get("value"))
                action = (
                    "provenance_changed"
                    if semantic_before == semantic_after
                    else "contradicted"
                )
                items.append(
                    DeltaItemV1("project_context", action, key, before, after, refs)
                )
                if action == "contradicted":
                    category = (
                        "DECISION"
                        if after.get("category") == "decisions"
                        else "PROJECT_CONTEXT"
                    )
                    conflicts.append(
                        ConflictEntryV1.create(
                            category, key, left=before, right=after, evidence_refs=refs
                        )
                    )

        def package_key(value: Mapping[str, Any]) -> str:
            return str(value.get("name")) + "@" + str(value.get("version"))

        left_pins = {package_key(value): value for value in left_state["package_pins"]}
        right_pins = {
            package_key(value): value for value in right_state["package_pins"]
        }
        if len(left_pins) != len(left_state["package_pins"]) or len(right_pins) != len(
            right_state["package_pins"]
        ):
            raise WorkspaceConflictError()
        for key in sorted(set(left_pins) | set(right_pins)):
            before, after = left_pins.get(key), right_pins.get(key)
            refs = tuple(
                _evidence_ref(checkpoint)
                for checkpoint, value in ((base, before), (target, after))
                if value is not None
            )
            if before is None:
                items.append(
                    DeltaItemV1("package", "pin_added", key, None, after, refs)
                )
            elif after is None:
                items.append(
                    DeltaItemV1("package", "pin_removed", key, before, None, refs)
                )
            elif _canonical(before) != _canonical(after):
                action = (
                    "trust_changed"
                    if before.get("trust_state") != after.get("trust_state")
                    else "digest_changed"
                )
                items.append(DeltaItemV1("package", action, key, before, after, refs))
                conflicts.append(
                    ConflictEntryV1.create(
                        "PACKAGE", key, left=before, right=after, evidence_refs=refs
                    )
                )

        if _canonical(left_state["policy"]) != _canonical(right_state["policy"]):
            refs = (_evidence_ref(base), _evidence_ref(target))
            items.append(
                DeltaItemV1(
                    "policy",
                    "effective_changed",
                    "workspace-policy",
                    left_state["policy"],
                    right_state["policy"],
                    refs,
                )
            )
            conflicts.append(
                ConflictEntryV1.create(
                    "POLICY",
                    "workspace-policy",
                    left=left_state["policy"],
                    right=right_state["policy"],
                    evidence_refs=refs,
                )
            )
        lineage_action = (
            "fork_ancestry"
            if base.workspace_id != target.workspace_id
            else "checkpoint_reference"
        )
        items.append(
            DeltaItemV1(
                "lineage",
                lineage_action,
                target.workspace_id,
                {
                    "workspace_id": base.workspace_id,
                    "checkpoint_id": base.checkpoint_id,
                },
                {
                    "workspace_id": target.workspace_id,
                    "checkpoint_id": target.checkpoint_id,
                },
                (_evidence_ref(base), _evidence_ref(target)),
            )
        )

        selected = tuple(
            sorted(
                items,
                key=lambda item: (
                    item.category,
                    item.stable_key,
                    item.action,
                    _digest("leanctx.delta-item.v1", item.to_dict()),
                ),
            )
        )
        if len(selected) > _MAX_DELTA_ITEMS:
            raise WorkspacePolicyError()
        report = ConflictReportV1.create(
            base_ref, base_ref, target_ref, ancestry, conflicts
        )
        payload = {
            "schema_version": cls.SCHEMA,
            "base": _plain(base_ref.to_dict()),
            "target": _plain(target_ref.to_dict()),
            "items": [_plain(item.to_dict()) for item in selected],
            "conflicts": _plain(report.to_dict()),
            "bounds": {"max_items": _MAX_DELTA_ITEMS, "max_bytes": _MAX_DELTA_BYTES},
        }
        if len(_canonical(payload)) > _MAX_DELTA_BYTES:
            raise WorkspacePolicyError()
        return cls(
            base_ref,
            target_ref,
            selected,
            report,
            _digest("leanctx.context-delta.v1", payload),
        )

    def to_dict(self) -> Mapping[str, Any]:
        return dict(self._unsigned_dict(), delta_id=self.delta_id)

    @classmethod
    def from_dict(cls, value: Any) -> "ContextDeltaV1":
        value = _exact(
            value,
            {
                "schema_version",
                "base",
                "target",
                "items",
                "conflicts",
                "bounds",
                "delta_id",
            },
            "ContextDeltaV1",
        )
        if value["schema_version"] != cls.SCHEMA or not isinstance(
            value["items"], list
        ):
            raise WorkspaceIncompatibleError("unsupported ContextDeltaV1")
        expected_bounds = {"max_items": _MAX_DELTA_ITEMS, "max_bytes": _MAX_DELTA_BYTES}
        if value["bounds"] != expected_bounds:
            raise WorkspaceIncompatibleError("delta bounds are incompatible")
        result = cls(
            WorkspaceStateRefV1.from_dict(value["base"]),
            WorkspaceStateRefV1.from_dict(value["target"]),
            tuple(DeltaItemV1.from_dict(item) for item in value["items"]),
            ConflictReportV1.from_dict(value["conflicts"]),
            value["delta_id"],
        )
        if _plain(result.to_dict()) != _plain(value):
            raise WorkspaceConflictError()
        return result


def _checkpoint_entries(
    checkpoint: ContextCheckpointV2,
) -> Mapping[str, Mapping[str, Any]]:
    return _keyed(checkpoint.logical_state["entries"], "entry_id", "entries")


def _checkpoint_sources(
    checkpoint: ContextCheckpointV2,
) -> Mapping[str, Mapping[str, Any]]:
    return _keyed(checkpoint.logical_state["sources"], "source_id", "sources")


@dataclass(frozen=True)
class ContextHandoffV1:
    handoff_id: str
    source: WorkspaceStateRefV1
    target_workspace_id: str
    target_role: Optional[str]
    task: str
    selected_entries: Tuple[Mapping[str, Any], ...]
    source_anchors: Tuple[Mapping[str, Any], ...]
    recovery_refs: Tuple[str, ...]
    package_refs: Tuple[Mapping[str, Any], ...]
    required_policy_floor: WorkspacePolicy
    handoff_digest: str

    SCHEMA = "leanctx.context-handoff/v1"

    @classmethod
    def create(
        cls,
        checkpoint: ContextCheckpointV2,
        target_workspace_id: str,
        task: str,
        entry_ids: Sequence[str],
        *,
        target_role: Optional[str] = None,
        handoff_id: Optional[str] = None,
        source_lineage: Optional[ForkLineageV1] = None,
    ) -> "ContextHandoffV1":
        if not isinstance(checkpoint, ContextCheckpointV2):
            raise WorkspaceValidationError("checkpoint must be ContextCheckpointV2")
        target_workspace_id = _uuid(target_workspace_id, "target_workspace_id")
        task = _text(task, "task", 16 * 1024, controls=False)
        if target_role is not None:
            _text(target_role, "target_role", 128)
        selected_id = (
            str(uuid.uuid4()) if handoff_id is None else _uuid(handoff_id, "handoff_id")
        )
        ids = _sorted_unique_text(entry_ids, "entry_ids", _MAX_HANDOFF_ENTRIES)
        entries_by_id = _checkpoint_entries(checkpoint)
        try:
            entries = tuple(_freeze(entries_by_id[entry_id]) for entry_id in ids)
        except KeyError as exc:
            raise WorkspaceValidationError(
                "handoff entry is not present in source state"
            ) from exc
        source_ids = sorted(
            {
                source_id
                for entry in entries
                for source_id in entry.get("source_ids", ())
            }
        )
        sources_by_id = _checkpoint_sources(checkpoint)
        try:
            anchors = tuple(
                _freeze(_portable_anchor(sources_by_id[source_id]))
                for source_id in source_ids
            )
        except KeyError as exc:
            raise WorkspaceConflictError() from exc
        recovery = tuple(
            sorted({ref for entry in entries for ref in entry.get("recovery_refs", ())})
        )
        logical_state = _plain(checkpoint.logical_state)
        pins = tuple(
            _freeze(pin)
            for pin in sorted(
                [_plain(pin) for pin in logical_state["package_pins"]],
                key=lambda pin: (pin["name"], pin["version"], pin["artifact_digest"]),
            )
        )
        if (
            len(anchors) > _MAX_HANDOFF_ANCHORS
            or len(recovery) > _MAX_HANDOFF_RECOVERY_REFS
        ):
            raise WorkspacePolicyError()
        if len(pins) > _MAX_HANDOFF_PACKAGE_REFS:
            raise WorkspacePolicyError()
        policy = WorkspacePolicy.from_dict(_plain(checkpoint.logical_state["policy"]))
        base = {
            "schema_version": cls.SCHEMA,
            "handoff_id": selected_id,
            "source": _plain(
                WorkspaceStateRefV1.from_checkpoint(
                    checkpoint, fork_lineage=source_lineage
                ).to_dict()
            ),
            "target": {"workspace_id": target_workspace_id, "role": target_role},
            "task": task,
            "selected_entries": [_plain(entry) for entry in entries],
            "source_anchors": [_plain(anchor) for anchor in anchors],
            "recovery_refs": list(recovery),
            "package_refs": [_plain(pin) for pin in pins],
            "required_policy_floor": _plain(policy.to_dict()),
            "bounds": {
                "max_entries": _MAX_HANDOFF_ENTRIES,
                "max_source_anchors": _MAX_HANDOFF_ANCHORS,
                "max_recovery_refs": _MAX_HANDOFF_RECOVERY_REFS,
                "max_package_refs": _MAX_HANDOFF_PACKAGE_REFS,
                "max_bytes": _MAX_HANDOFF_BYTES,
            },
        }
        _reject_sensitive(base)
        if len(_canonical(base)) > _MAX_HANDOFF_BYTES:
            raise WorkspacePolicyError()
        return cls(
            selected_id,
            WorkspaceStateRefV1.from_checkpoint(
                checkpoint, fork_lineage=source_lineage
            ),
            target_workspace_id,
            target_role,
            task,
            entries,
            anchors,
            recovery,
            pins,
            policy,
            _digest("leanctx.context-handoff.v1", base),
        )

    def _unsigned_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "handoff_id": self.handoff_id,
            "source": _plain(self.source.to_dict()),
            "target": {
                "workspace_id": self.target_workspace_id,
                "role": self.target_role,
            },
            "task": self.task,
            "selected_entries": [_plain(entry) for entry in self.selected_entries],
            "source_anchors": [_plain(anchor) for anchor in self.source_anchors],
            "recovery_refs": list(self.recovery_refs),
            "package_refs": [_plain(pin) for pin in self.package_refs],
            "required_policy_floor": _plain(self.required_policy_floor.to_dict()),
            "bounds": {
                "max_entries": _MAX_HANDOFF_ENTRIES,
                "max_source_anchors": _MAX_HANDOFF_ANCHORS,
                "max_recovery_refs": _MAX_HANDOFF_RECOVERY_REFS,
                "max_package_refs": _MAX_HANDOFF_PACKAGE_REFS,
                "max_bytes": _MAX_HANDOFF_BYTES,
            },
        }

    def __post_init__(self) -> None:
        _uuid(self.handoff_id, "handoff_id")
        _uuid(self.target_workspace_id, "target_workspace_id")
        _text(self.task, "task", 16 * 1024, controls=False)
        _sha(self.handoff_digest, "handoff_digest")
        if self.target_role is not None:
            _text(self.target_role, "target_role", 128)
        object.__setattr__(
            self,
            "selected_entries",
            tuple(_freeze(_plain(v)) for v in self.selected_entries),
        )
        object.__setattr__(
            self,
            "source_anchors",
            tuple(_freeze(_plain(v)) for v in self.source_anchors),
        )
        object.__setattr__(
            self, "package_refs", tuple(_freeze(_plain(v)) for v in self.package_refs)
        )
        object.__setattr__(
            self,
            "recovery_refs",
            _sorted_unique_text(
                self.recovery_refs, "recovery_refs", _MAX_HANDOFF_RECOVERY_REFS
            ),
        )
        if (
            not self.selected_entries
            or len(self.selected_entries) > _MAX_HANDOFF_ENTRIES
        ):
            raise WorkspacePolicyError()
        if len(self.source_anchors) > _MAX_HANDOFF_ANCHORS:
            raise WorkspacePolicyError()
        if len(self.package_refs) > _MAX_HANDOFF_PACKAGE_REFS:
            raise WorkspacePolicyError()
        entries = [
            ProjectContextEntry.from_dict(_plain(entry))
            for entry in self.selected_entries
        ]
        entry_ids = [entry.entry_id for entry in entries]
        if entry_ids != sorted(entry_ids) or len(set(entry_ids)) != len(entry_ids):
            raise WorkspaceValidationError("handoff entries are not canonical")
        anchors = [
            _source_anchor_from_dict(_plain(anchor), allow_verified=True)
            for anchor in self.source_anchors
        ]
        if any(anchor.engine_binding is not None for anchor in anchors):
            raise WorkspaceValidationError("handoff anchors must be portable")
        if [_plain(anchor.to_dict()) for anchor in anchors] != [
            _plain(anchor) for anchor in self.source_anchors
        ]:
            raise WorkspaceValidationError("handoff anchors are not canonical")
        anchor_ids = [anchor.source_id for anchor in anchors]
        if anchor_ids != sorted(anchor_ids) or len(set(anchor_ids)) != len(anchor_ids):
            raise WorkspaceValidationError("handoff anchors are not canonical")
        if any(not set(entry.source_ids).issubset(anchor_ids) for entry in entries):
            raise WorkspaceConflictError()
        pins = [PackagePin.from_dict(_plain(pin)) for pin in self.package_refs]
        pin_keys = [(pin.name, pin.version, pin.artifact_digest) for pin in pins]
        if pin_keys != sorted(pin_keys) or len(set(pin_keys)) != len(pin_keys):
            raise WorkspaceValidationError("handoff package refs are not canonical")
        if self.source_lineage is not None and (
            self.source_lineage.child_workspace_id != self.source.workspace_id
        ):
            raise WorkspaceConflictError()
        _reject_sensitive(self._unsigned_dict())
        if (
            _digest("leanctx.context-handoff.v1", self._unsigned_dict())
            != self.handoff_digest
        ):
            raise WorkspaceConflictError()

    @property
    def source_lineage(self) -> Optional[ForkLineageV1]:
        return self.source.fork_lineage

    def to_dict(self) -> Mapping[str, Any]:
        return dict(self._unsigned_dict(), handoff_digest=self.handoff_digest)

    @classmethod
    def from_dict(cls, value: Any) -> "ContextHandoffV1":
        value = _exact(
            value,
            {
                "schema_version",
                "handoff_id",
                "source",
                "target",
                "task",
                "selected_entries",
                "source_anchors",
                "recovery_refs",
                "package_refs",
                "required_policy_floor",
                "bounds",
                "handoff_digest",
            },
            "ContextHandoffV1",
        )
        if value["schema_version"] != cls.SCHEMA:
            raise WorkspaceIncompatibleError("unsupported ContextHandoffV1")
        target = _exact(value["target"], {"workspace_id", "role"}, "handoff target")
        expected_bounds = {
            "max_entries": _MAX_HANDOFF_ENTRIES,
            "max_source_anchors": _MAX_HANDOFF_ANCHORS,
            "max_recovery_refs": _MAX_HANDOFF_RECOVERY_REFS,
            "max_package_refs": _MAX_HANDOFF_PACKAGE_REFS,
            "max_bytes": _MAX_HANDOFF_BYTES,
        }
        if value["bounds"] != expected_bounds:
            raise WorkspaceIncompatibleError("handoff bounds are incompatible")
        result = cls(
            value["handoff_id"],
            WorkspaceStateRefV1.from_dict(value["source"]),
            target["workspace_id"],
            target["role"],
            value["task"],
            tuple(value["selected_entries"]),
            tuple(value["source_anchors"]),
            tuple(value["recovery_refs"]),
            tuple(value["package_refs"]),
            WorkspacePolicy.from_dict(value["required_policy_floor"]),
            value["handoff_digest"],
        )
        if _plain(result.to_dict()) != _plain(value):
            raise WorkspaceConflictError()
        return result


@dataclass(frozen=True)
class HandoffAdmissionV1:
    handoff_id: str
    handoff_digest: str
    receiver_workspace_id: str
    target_result: str
    lineage_result: str
    policy_result: str
    package_result: str
    source_result: str
    conflicts: ConflictReportV1
    decision: str
    reason_codes: Tuple[str, ...]
    admission_digest: str

    SCHEMA = "leanctx.handoff-admission/v1"

    @classmethod
    def evaluate(
        cls,
        handoff: ContextHandoffV1,
        receiver_checkpoint: ContextCheckpointV2,
        *,
        conflicts: Optional[ConflictReportV1] = None,
        available_source_ids: Sequence[str] = (),
    ) -> "HandoffAdmissionV1":
        """Evaluate without claiming ancestry unavailable to this pure API."""
        return cls._evaluate_verified(
            handoff,
            receiver_checkpoint,
            lineage_ok=False,
            conflicts=conflicts,
            available_source_ids=available_source_ids,
        )

    @classmethod
    def _evaluate_verified(
        cls,
        handoff: ContextHandoffV1,
        receiver_checkpoint: ContextCheckpointV2,
        *,
        lineage_ok: bool,
        conflicts: Optional[ConflictReportV1] = None,
        available_source_ids: Sequence[str] = (),
    ) -> "HandoffAdmissionV1":
        if not isinstance(handoff, ContextHandoffV1) or not isinstance(
            receiver_checkpoint, ContextCheckpointV2
        ):
            raise WorkspaceValidationError("handoff admission inputs are invalid")
        receiver_state = _plain(receiver_checkpoint.logical_state)
        receiver_policy = WorkspacePolicy.from_dict(receiver_state["policy"])
        target_ok = handoff.target_workspace_id == receiver_checkpoint.workspace_id
        policy_ok = receiver_policy.is_tightening(handoff.required_policy_floor)
        receiver_pins = {
            (pin["name"], pin["version"]): _plain(pin)
            for pin in receiver_state["package_pins"]
        }
        package_ok = all(
            receiver_pins.get((pin["name"], pin["version"])) == _plain(pin)
            for pin in handoff.package_refs
        )
        available_sources = set(
            _sorted_unique_text(
                available_source_ids,
                "available_source_ids",
                _MAX_HANDOFF_ANCHORS,
            )
        )
        receiver_sources = {
            anchor["source_id"]: _portable_anchor(anchor)
            for anchor in receiver_state["sources"]
        }
        source_missing = any(
            anchor["source_id"] not in receiver_sources
            for anchor in handoff.source_anchors
        )
        source_mismatch = any(
            anchor["source_id"] in receiver_sources
            and receiver_sources[anchor["source_id"]] != _portable_anchor(anchor)
            for anchor in handoff.source_anchors
        )
        source_unavailable = any(
            anchor["source_id"] in receiver_sources
            and anchor["source_id"] not in available_sources
            for anchor in handoff.source_anchors
        )
        source_ok = (
            not source_missing and not source_mismatch and not source_unavailable
        )
        if conflicts is None:
            receiver_ref = WorkspaceStateRefV1.from_checkpoint(receiver_checkpoint)
            conflicts = ConflictReportV1.create(
                None,
                handoff.source,
                receiver_ref,
                "exact" if lineage_ok else "unknown",
                (),
            )
        reasons = []
        if not target_ok:
            reasons.append("WRONG_TARGET")
        if not lineage_ok:
            reasons.append("LINEAGE_UNVERIFIED")
        if not policy_ok:
            reasons.append("POLICY_DOWNGRADE")
        if not package_ok:
            reasons.append("PACKAGE_TRUST_MISMATCH")
        if source_mismatch:
            reasons.append("SOURCE_MISMATCH")
        elif source_missing or source_unavailable:
            reasons.append("SOURCE_UNAVAILABLE")
        if conflicts.entries:
            reasons.append("CONFLICTS_PRESENT")
        hard_reject = (
            not target_ok
            or not lineage_ok
            or not policy_ok
            or not package_ok
            or source_mismatch
            or bool(conflicts.entries)
        )
        decision = (
            "rejected" if hard_reject else ("degraded" if not source_ok else "admitted")
        )
        values = {
            "target_result": "match" if target_ok else "mismatch",
            "lineage_result": "verified" if lineage_ok else "unverified",
            "policy_result": "monotonic" if policy_ok else "downgrade",
            "package_result": "exact" if package_ok else "mismatch",
            "source_result": (
                "available"
                if source_ok
                else ("mismatch" if source_mismatch else "unavailable")
            ),
        }
        payload = {
            "schema_version": cls.SCHEMA,
            "handoff_id": handoff.handoff_id,
            "handoff_digest": handoff.handoff_digest,
            "receiver_workspace_id": receiver_checkpoint.workspace_id,
            **values,
            "conflicts": _plain(conflicts.to_dict()),
            "decision": decision,
            "reason_codes": sorted(reasons),
        }
        return cls(
            handoff.handoff_id,
            handoff.handoff_digest,
            receiver_checkpoint.workspace_id,
            values["target_result"],
            values["lineage_result"],
            values["policy_result"],
            values["package_result"],
            values["source_result"],
            conflicts,
            decision,
            tuple(sorted(reasons)),
            _digest("leanctx.handoff-admission.v1", payload),
        )

    def __post_init__(self) -> None:
        _uuid(self.handoff_id, "handoff_id")
        _sha(self.handoff_digest, "handoff_digest")
        _uuid(self.receiver_workspace_id, "receiver_workspace_id")
        if self.decision not in {"admitted", "rejected", "degraded"}:
            raise WorkspaceValidationError("admission decision is invalid")
        if self.target_result not in {"match", "mismatch"}:
            raise WorkspaceValidationError("target result is invalid")
        if self.lineage_result not in {"verified", "unverified"}:
            raise WorkspaceValidationError("lineage result is invalid")
        if self.policy_result not in {"monotonic", "downgrade"}:
            raise WorkspaceValidationError("policy result is invalid")
        if self.package_result not in {"exact", "mismatch"}:
            raise WorkspaceValidationError("package result is invalid")
        if self.source_result not in {"available", "unavailable", "mismatch"}:
            raise WorkspaceValidationError("source result is invalid")
        object.__setattr__(
            self,
            "reason_codes",
            _sorted_unique_text(self.reason_codes, "reason_codes", 16),
        )
        hard_failure = (
            self.target_result != "match"
            or self.lineage_result != "verified"
            or self.policy_result != "monotonic"
            or self.package_result != "exact"
            or self.source_result == "mismatch"
            or bool(self.conflicts.entries)
        )
        if self.decision == "admitted" and (
            hard_failure or self.source_result != "available" or self.reason_codes
        ):
            raise WorkspaceConflictError()
        if self.decision == "degraded" and (
            hard_failure
            or self.source_result != "unavailable"
            or self.reason_codes != ("SOURCE_UNAVAILABLE",)
        ):
            raise WorkspaceConflictError()
        if self.decision == "rejected" and not hard_failure:
            raise WorkspaceConflictError()
        _sha(self.admission_digest, "admission_digest")
        if self.admission_digest != _digest(
            "leanctx.handoff-admission.v1", self._unsigned_dict()
        ):
            raise WorkspaceConflictError()

    def _unsigned_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "handoff_id": self.handoff_id,
            "handoff_digest": self.handoff_digest,
            "receiver_workspace_id": self.receiver_workspace_id,
            "target_result": self.target_result,
            "lineage_result": self.lineage_result,
            "policy_result": self.policy_result,
            "package_result": self.package_result,
            "source_result": self.source_result,
            "conflicts": _plain(self.conflicts.to_dict()),
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
        }

    def to_dict(self) -> Mapping[str, Any]:
        return dict(self._unsigned_dict(), admission_digest=self.admission_digest)

    @classmethod
    def from_dict(cls, value: Any) -> "HandoffAdmissionV1":
        value = _exact(
            value,
            {
                "schema_version",
                "handoff_id",
                "handoff_digest",
                "receiver_workspace_id",
                "target_result",
                "lineage_result",
                "policy_result",
                "package_result",
                "source_result",
                "conflicts",
                "decision",
                "reason_codes",
                "admission_digest",
            },
            "HandoffAdmissionV1",
        )
        if value["schema_version"] != cls.SCHEMA or not isinstance(
            value["reason_codes"], list
        ):
            raise WorkspaceIncompatibleError("unsupported HandoffAdmissionV1")
        result = cls(
            value["handoff_id"],
            value["handoff_digest"],
            value["receiver_workspace_id"],
            value["target_result"],
            value["lineage_result"],
            value["policy_result"],
            value["package_result"],
            value["source_result"],
            ConflictReportV1.from_dict(value["conflicts"]),
            value["decision"],
            tuple(value["reason_codes"]),
            value["admission_digest"],
        )
        if (
            _digest("leanctx.handoff-admission.v1", result._unsigned_dict())
            != result.admission_digest
        ):
            raise WorkspaceConflictError()
        if _plain(result.to_dict()) != _plain(value):
            raise WorkspaceConflictError()
        return result


@dataclass(frozen=True)
class NarrowReconciliationV1:
    reconciliation_id: str
    common_ancestor: WorkspaceStateRefV1
    left: WorkspaceStateRefV1
    right: WorkspaceStateRefV1
    mode: str
    selected_entry_ids: Tuple[str, ...]
    conflicts: ConflictReportV1
    result: str
    reconciliation_digest: str

    SCHEMA = "leanctx.narrow-reconciliation/v1"

    def __post_init__(self) -> None:
        _uuid(self.reconciliation_id, "reconciliation_id")
        if self.mode not in {"knowledge", "decisions", "accepted_handoff"}:
            raise WorkspaceValidationError("reconciliation mode is invalid")
        if self.result not in {"applicable", "manual_required", "rejected"}:
            raise WorkspaceValidationError("reconciliation result is invalid")
        object.__setattr__(
            self,
            "selected_entry_ids",
            _sorted_unique_text(
                self.selected_entry_ids, "selected_entry_ids", _MAX_DELTA_ITEMS
            ),
        )
        if (self.result == "manual_required") != bool(self.conflicts.entries):
            raise WorkspaceConflictError()
        if (
            self.conflicts.base != self.common_ancestor
            or self.conflicts.left != self.left
            or self.conflicts.right != self.right
            or self.conflicts.ancestry != "diverged"
        ):
            raise WorkspaceConflictError()
        unsigned = {
            "schema_version": self.SCHEMA,
            "reconciliation_id": self.reconciliation_id,
            "common_ancestor": _plain(self.common_ancestor.to_dict()),
            "left": _plain(self.left.to_dict()),
            "right": _plain(self.right.to_dict()),
            "mode": self.mode,
            "selected_entry_ids": list(self.selected_entry_ids),
            "conflicts": _plain(self.conflicts.to_dict()),
            "result": self.result,
        }
        if self.reconciliation_digest != _digest(
            "leanctx.narrow-reconciliation.v1", unsigned
        ):
            raise WorkspaceConflictError()

    @classmethod
    def between(
        cls,
        ancestor: ContextCheckpointV2,
        left: ContextCheckpointV2,
        right: ContextCheckpointV2,
        *,
        mode: str,
        reconciliation_id: Optional[str] = None,
        accepted_handoff: Optional[ContextHandoffV1] = None,
        admission: Optional[HandoffAdmissionV1] = None,
        left_lineage: Optional[ForkLineageV1] = None,
        right_lineage: Optional[ForkLineageV1] = None,
    ) -> "NarrowReconciliationV1":
        if mode not in {"knowledge", "decisions", "accepted_handoff"}:
            raise WorkspaceValidationError("reconciliation mode is invalid")
        accepted_ids: Optional[set[str]] = None
        if mode == "accepted_handoff":
            if not isinstance(accepted_handoff, ContextHandoffV1) or not isinstance(
                admission, HandoffAdmissionV1
            ):
                raise WorkspaceValidationError(
                    "accepted_handoff mode requires exact handoff and admission"
                )
            if (
                admission.decision != "admitted"
                or admission.handoff_id != accepted_handoff.handoff_id
                or admission.handoff_digest != accepted_handoff.handoff_digest
                or accepted_handoff.source.workspace_id != left.workspace_id
                or accepted_handoff.source.checkpoint_id != left.checkpoint_id
                or accepted_handoff.source.state_digest != left.state_digest
                or accepted_handoff.source.checkpoint_envelope_digest
                != left.envelope_digest
                or accepted_handoff.target_workspace_id != right.workspace_id
                or admission.receiver_workspace_id != right.workspace_id
                or admission.conflicts.left != accepted_handoff.source
                or admission.conflicts.right.workspace_id != right.workspace_id
                or admission.conflicts.right.checkpoint_id != right.checkpoint_id
                or admission.conflicts.right.state_digest != right.state_digest
                or admission.conflicts.right.checkpoint_envelope_digest
                != right.envelope_digest
            ):
                raise WorkspaceConflictError()
            accepted_ids = {
                ProjectContextEntry.from_dict(_plain(entry)).entry_id
                for entry in accepted_handoff.selected_entries
            }
        elif accepted_handoff is not None or admission is not None:
            raise WorkspaceValidationError(
                "handoff evidence is only valid in accepted_handoff mode"
            )
        selected_id = (
            str(uuid.uuid4())
            if reconciliation_id is None
            else _uuid(reconciliation_id, "reconciliation_id")
        )
        ancestor_entries = _checkpoint_entries(ancestor)
        left_entries = _checkpoint_entries(left)
        right_entries = _checkpoint_entries(right)
        allowed = (
            {"decisions"}
            if mode == "decisions"
            else {"facts", "constraints", "unresolved_questions"}
        )
        if mode == "accepted_handoff":
            allowed = {"facts", "decisions", "constraints", "unresolved_questions"}
        selected = []
        conflicts = []
        changed_left = {
            key: value
            for key, value in left_entries.items()
            if key not in ancestor_entries
            or _canonical(value) != _canonical(ancestor_entries[key])
        }
        changed_right = {
            key: value
            for key, value in right_entries.items()
            if key not in ancestor_entries
            or _canonical(value) != _canonical(ancestor_entries[key])
        }
        for key in sorted(set(changed_left) | set(changed_right)):
            if accepted_ids is not None and key not in accepted_ids:
                continue
            left_value, right_value = changed_left.get(key), changed_right.get(key)
            values = [value for value in (left_value, right_value) if value is not None]
            if not values or any(
                value.get("category") not in allowed for value in values
            ):
                continue
            if (
                left_value is not None
                and right_value is not None
                and _canonical(left_value) != _canonical(right_value)
            ):
                category = (
                    "DECISION"
                    if left_value.get("category") == "decisions"
                    else "PROJECT_CONTEXT"
                )
                refs = tuple(
                    _evidence_ref(checkpoint, value)
                    for checkpoint, value in (
                        (ancestor, ancestor_entries.get(key)),
                        (left, left_value),
                        (right, right_value),
                    )
                    if value is not None
                )
                conflicts.append(
                    ConflictEntryV1.create(
                        category,
                        key,
                        base=ancestor_entries.get(key),
                        left=left_value,
                        right=right_value,
                        evidence_refs=refs,
                    )
                )
            else:
                selected.append(key)
        ancestor_ref = WorkspaceStateRefV1.from_checkpoint(ancestor)
        left_ref = WorkspaceStateRefV1.from_checkpoint(left, fork_lineage=left_lineage)
        right_ref = WorkspaceStateRefV1.from_checkpoint(
            right, fork_lineage=right_lineage
        )
        report = ConflictReportV1.create(
            ancestor_ref, left_ref, right_ref, "diverged", conflicts
        )
        result = "manual_required" if conflicts else "applicable"
        payload = {
            "schema_version": cls.SCHEMA,
            "reconciliation_id": selected_id,
            "common_ancestor": _plain(ancestor_ref.to_dict()),
            "left": _plain(left_ref.to_dict()),
            "right": _plain(right_ref.to_dict()),
            "mode": mode,
            "selected_entry_ids": sorted(selected),
            "conflicts": _plain(report.to_dict()),
            "result": result,
        }
        return cls(
            selected_id,
            ancestor_ref,
            left_ref,
            right_ref,
            mode,
            tuple(sorted(selected)),
            report,
            result,
            _digest("leanctx.narrow-reconciliation.v1", payload),
        )

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "reconciliation_id": self.reconciliation_id,
            "common_ancestor": _plain(self.common_ancestor.to_dict()),
            "left": _plain(self.left.to_dict()),
            "right": _plain(self.right.to_dict()),
            "mode": self.mode,
            "selected_entry_ids": list(self.selected_entry_ids),
            "conflicts": _plain(self.conflicts.to_dict()),
            "result": self.result,
            "reconciliation_digest": self.reconciliation_digest,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "NarrowReconciliationV1":
        value = _exact(
            value,
            {
                "schema_version",
                "reconciliation_id",
                "common_ancestor",
                "left",
                "right",
                "mode",
                "selected_entry_ids",
                "conflicts",
                "result",
                "reconciliation_digest",
            },
            "NarrowReconciliationV1",
        )
        if value["schema_version"] != cls.SCHEMA or not isinstance(
            value["selected_entry_ids"], list
        ):
            raise WorkspaceIncompatibleError("unsupported NarrowReconciliationV1")
        result = cls(
            value["reconciliation_id"],
            WorkspaceStateRefV1.from_dict(value["common_ancestor"]),
            WorkspaceStateRefV1.from_dict(value["left"]),
            WorkspaceStateRefV1.from_dict(value["right"]),
            value["mode"],
            tuple(value["selected_entry_ids"]),
            ConflictReportV1.from_dict(value["conflicts"]),
            value["result"],
            value["reconciliation_digest"],
        )
        if _plain(result.to_dict()) != _plain(value):
            raise WorkspaceConflictError()
        return result


__all__ = [
    "ConflictEntryV1",
    "ConflictReportV1",
    "ContextDeltaV1",
    "ContextHandoffV1",
    "DeltaItemV1",
    "EvidenceRefV1",
    "ForkLineageV1",
    "HandoffAdmissionV1",
    "NarrowReconciliationV1",
    "PolicyInheritanceV1",
    "WorkspaceForkV1",
    "WorkspaceStateRefV1",
]
