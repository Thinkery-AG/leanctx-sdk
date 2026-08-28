"""Truthful host/evaluator receipt projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from .errors import ArtifactIntegrityError, ValidationError
from .protocol import (
    ContextSource,
    ContextView,
    HostOutcome,
    Integrity,
    SCHEMA_VERSION,
    _freeze,
    _plain,
    _text,
    canonical_bytes,
    validate_ref,
)


def _safe_usage(value: Optional[Mapping[str, object]]) -> Optional[Mapping[str, object]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValidationError("usage must be a mapping")
    frozen = _freeze(dict(value))
    # Force a deterministic, finite JSON representation and prevent arbitrary
    # host objects from crossing into the receipt projection.
    try:
        canonical_bytes(_plain(frozen))
    except ValidationError as exc:
        raise ValidationError("usage must be deterministic JSON data") from exc
    return frozen


@dataclass(frozen=True)
class ContextReceipt:
    session_id: str
    task_id: str
    plan_id: Optional[str]
    view: Optional[ContextView]
    outcome: str
    integrity_status: str
    degradations: Tuple[str, ...] = ()
    usage: Optional[Mapping[str, object]] = None
    host_exception_type: Optional[str] = None
    host_result: object = field(default=None, repr=False, compare=False)
    host_exception: Optional[BaseException] = field(default=None, repr=False, compare=False)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        _text(self.session_id, "session_id", 512)
        _text(self.task_id, "task_id", 512)
        if self.plan_id is not None:
            validate_ref(self.plan_id, "plan_id")
            if not self.plan_id.startswith("plan:sha256:"):
                raise ValidationError("plan_id must be a deterministic plan reference")
        if self.view is not None and not isinstance(self.view, ContextView):
            raise ValidationError("receipt view must be ContextView")
        if self.outcome not in {item.value for item in HostOutcome}:
            raise ValidationError("invalid host outcome")
        if self.integrity_status not in {item.value for item in Integrity}:
            raise ValidationError("invalid integrity status")
        if self.outcome == HostOutcome.ABORTED.value and self.host_exception_type is not None:
            _text(self.host_exception_type, "host_exception_type", 512)
            if ":" in self.host_exception_type or "\n" in self.host_exception_type:
                raise ValidationError("host_exception_type must be a safe type name")
        if self.host_exception_type is not None:
            _text(self.host_exception_type, "host_exception_type", 512)
        if self.host_exception is not None:
            if not isinstance(self.host_exception, BaseException):
                raise ValidationError("host_exception must be a BaseException")
            if self.outcome != HostOutcome.ABORTED.value:
                raise ValidationError("host_exception requires an aborted outcome")
            expected_type = (
                f"{type(self.host_exception).__module__}."
                f"{type(self.host_exception).__qualname__}"
            )
            if self.host_exception_type != expected_type:
                raise ValidationError("host_exception_type does not match host_exception")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValidationError("receipt schema_version must be 1")
        degradations = tuple(self.degradations)
        if any(not isinstance(item, str) or not item for item in degradations):
            raise ValidationError("degradations must be non-empty strings")
        object.__setattr__(self, "degradations", degradations)
        object.__setattr__(self, "usage", _safe_usage(self.usage))
        if self.integrity_status == Integrity.SEALED.value:
            if self.view is None or not self.view.verify():
                raise ValidationError("sealed receipt requires verified Engine evidence")

    @property
    def sealed(self) -> bool:
        return self.integrity_status == Integrity.SEALED.value

    @property
    def status(self) -> Optional[str]:
        return self.view.status if self.view is not None else None

    @property
    def source(self) -> Optional[ContextSource]:
        return self.view.source if self.view is not None else None

    @property
    def invocation(self):
        return self.view.invocation if self.view is not None else None

    @property
    def observation(self):
        return self.view.observation if self.view is not None else None

    @property
    def receipt_link(self):
        return self.view.receipt_link if self.view is not None else None

    @property
    def recovery_ref(self) -> Optional[str]:
        return self.view.recovery_ref if self.view is not None else None

    @property
    def output_digest(self) -> Optional[str]:
        return self.view.output_digest if self.view is not None else None

    @property
    def exception(self) -> Optional[BaseException]:
        """Original host exception; excluded from serialized receipt output."""

        return self.host_exception

    def verify(self) -> bool:
        return self.sealed and self.view is not None and self.view.verify()

    def require_verified(self) -> None:
        """Fail closed when receipt evidence is absent, degraded, or unsealed."""

        if not self.verify():
            raise ArtifactIntegrityError("receipt evidence is not sealed")

    def to_dict(self) -> Mapping[str, object]:
        result = {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "outcome": self.outcome,
            "integrity_status": self.integrity_status,
            "degradations": list(self.degradations),
            "usage": _plain(self.usage) if self.usage is not None else None,
            "host_exception_type": self.host_exception_type,
            "status": self.status,
            "source": _plain(self.source.to_dict()) if self.source else None,
            "invocation": _plain(self.invocation) if self.invocation else None,
            "observation": _plain(self.observation) if self.observation else None,
            "receipt_link": _plain(self.receipt_link.to_dict()) if self.receipt_link else None,
            "recovery_ref": self.recovery_ref,
            "output_digest": self.output_digest,
        }
        return result


__all__ = ["ContextReceipt"]
