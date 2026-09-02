"""Product-owned lifecycle around one immutable intent and one Engine view."""

from __future__ import annotations

import threading
import uuid
from typing import Mapping, Optional

from .engine import EngineClient, SubprocessEngineClient
from .errors import (
    ArtifactIntegrityError,
    EngineExecutionError,
    EngineProtocolError,
    EngineRejected,
    EngineTimeout,
    EngineUnavailable,
    RecoveryUnavailableError,
    SessionStateError,
    ValidationError,
)
from .protocol import (
    ContextPlan,
    ContextSource,
    ContextView,
    HostOutcome,
    Integrity,
    RecoveredSource,
    SessionState,
    _plain,
    _text,
    canonical_bytes,
)
from .receipt import ContextReceipt


def _runtime_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class _PlanAccessor:
    """Callable read view resolving the contract's plan name collision."""

    def __init__(self, session):
        self._session = session

    def __call__(self, source, *, mode="aggressive", freshness="reuse"):
        return self._session.plan_intent(source, mode=mode, freshness=freshness)

    def __getattr__(self, name):
        plan = self._session.current_plan
        if plan is None:
            raise AttributeError(name)
        return getattr(plan, name)

    def __eq__(self, other):
        return self._session.current_plan == other

    def __repr__(self):
        return repr(self._session.current_plan)


class ContextSession:
    """Mutable local lifecycle; hosts own all work between SDK calls."""

    def __init__(
        self,
        task: str,
        *,
        project_root: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        fail_open: bool = False,
        engine: Optional[EngineClient] = None,
    ):
        _text(task, "task", 16 * 1024, controls=False)
        if not isinstance(fail_open, bool):
            raise ValidationError("fail_open must be boolean")
        self._lock = threading.RLock()
        self._task = task
        self._project_root = project_root
        self._session_id = session_id or _runtime_id("session")
        self._task_id = task_id or _runtime_id("task")
        self._fail_open = fail_open
        self._engine = engine if engine is not None else SubprocessEngineClient()
        self._state = SessionState.CREATED.value
        self._plan: Optional[ContextPlan] = None
        self._plan_accessor = _PlanAccessor(self)
        self._view: Optional[ContextView] = None
        self._receipt: Optional[ContextReceipt] = None
        self._prepared = False
        self._degradations: list[str] = []
        self._first_error: Optional[BaseException] = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def task(self) -> str:
        return self._task

    @property
    def plan(self):
        return self._plan_accessor

    @property
    def current_plan(self) -> Optional[ContextPlan]:
        return self._plan

    @property
    def view(self) -> Optional[ContextView]:
        return self._view

    @property
    def receipt(self) -> Optional[ContextReceipt]:
        return self._receipt

    @property
    def degradations(self):
        return tuple(self._degradations)

    def plan_for(
        self,
        source: ContextSource,
        *,
        mode: str = "aggressive",
        freshness: str = "reuse",
    ) -> ContextPlan:
        """Compatibility spelling for callers that avoid the plan property."""

        return self.plan_intent(source, mode=mode, freshness=freshness)

    def plan_intent(
        self,
        source: ContextSource,
        *,
        mode: str = "aggressive",
        freshness: str = "reuse",
    ) -> ContextPlan:
        with self._lock:
            self._ensure_not_terminal()
            candidate = ContextPlan(
                self._session_id,
                self._task_id,
                self._task,
                source,
                mode=mode,
                freshness=freshness,
            )
            if self._plan is not None:
                if self._plan.plan_id != candidate.plan_id:
                    raise SessionStateError(
                        "a session cannot replace its Product intent"
                    )
                return self._plan
            self._plan = candidate
            self._state = SessionState.PLANNED.value
            return candidate

    def prepare(
        self,
        source: Optional[ContextSource] = None,
        *,
        mode: str = "aggressive",
        freshness: str = "reuse",
    ) -> Optional[ContextView]:
        with self._lock:
            if self._state in {
                SessionState.COMPLETED.value,
                SessionState.ABORTED.value,
                SessionState.CLOSED.value,
            }:
                raise SessionStateError(
                    "prepare is not legal after terminal completion"
                )
            if self._prepared:
                return self._view
            if self._plan is None:
                if source is None:
                    raise SessionStateError("prepare requires a source before planning")
                self.plan_intent(source, mode=mode, freshness=freshness)
            elif source is not None:
                self.plan_intent(source, mode=mode, freshness=freshness)
            plan = self._plan
            if plan is None:
                raise SessionStateError("prepare could not establish a plan")
            self._state = SessionState.EXECUTING.value
            try:
                self._view = self._engine.context_view(plan)
                self._prepared = True
                if self._view.status == "degraded":
                    self._add_degradation("engine:degraded")
                return self._view
            except (EngineUnavailable, EngineTimeout) as exc:
                if self._fail_open:
                    self._add_degradation(f"engine:{exc.code}")
                    self._prepared = True
                    self._view = None
                    return None
                self._abort_engine_failure(exc)
                raise
            except (EngineProtocolError, EngineRejected, EngineExecutionError) as exc:
                self._abort_engine_failure(exc)
                raise
            except Exception as exc:
                # An injected transport is still untrusted at the Product
                # boundary: unexpected internal failures never fail open.
                self._abort_engine_failure(exc)
                raise

    def complete(
        self,
        host_result: object = None,
        *,
        outcome: str = HostOutcome.UNKNOWN.value,
        usage: Optional[Mapping[str, object]] = None,
    ) -> ContextReceipt:
        with self._lock:
            if self._state == SessionState.COMPLETED.value:
                if self._receipt is None or not self._same_completion(
                    self._receipt, outcome, usage
                ):
                    raise SessionStateError("conflicting repeated complete")
                return self._receipt
            if self._state in {SessionState.ABORTED.value, SessionState.CLOSED.value}:
                raise SessionStateError("complete is not legal after abort/close")
            if self._state != SessionState.EXECUTING.value:
                raise SessionStateError("complete requires an executing session")
            if outcome not in {
                HostOutcome.UNKNOWN.value,
                HostOutcome.ACCEPTED.value,
                HostOutcome.REJECTED.value,
                HostOutcome.COMPLETED.value,
                HostOutcome.FAILED.value,
            }:
                raise ValidationError(
                    "complete outcome must be an explicit non-aborted host outcome"
                )
            receipt = self._make_receipt(
                outcome=outcome,
                host_result=host_result,
                usage=usage,
                host_exception_type=None,
                host_exception=None,
            )
            self._receipt = receipt
            self._state = SessionState.COMPLETED.value
            return receipt

    def abort(self, error: BaseException) -> ContextReceipt:
        with self._lock:
            if not isinstance(error, BaseException):
                raise ValidationError("abort requires a BaseException")
            if self._state == SessionState.ABORTED.value:
                if self._receipt is None:
                    raise SessionStateError("aborted session has no receipt")
                return self._receipt
            if self._state == SessionState.CLOSED.value:
                if (
                    self._receipt is not None
                    and self._receipt.outcome == HostOutcome.ABORTED.value
                ):
                    return self._receipt
                raise SessionStateError("closed session has no abort receipt")
            if self._state == SessionState.COMPLETED.value:
                raise SessionStateError("cannot abort a completed session")
            type_name = f"{type(error).__module__}.{type(error).__qualname__}"
            receipt = self._make_receipt(
                outcome=HostOutcome.ABORTED.value,
                host_result=None,
                usage=None,
                host_exception_type=type_name,
                host_exception=error,
            )
            self._first_error = error
            self._receipt = receipt
            self._state = SessionState.ABORTED.value
            return receipt

    def recover(self, view: Optional[ContextView] = None) -> RecoveredSource:
        with self._lock:
            if self._state not in {
                SessionState.EXECUTING.value,
                SessionState.COMPLETED.value,
                SessionState.ABORTED.value,
            }:
                raise SessionStateError(
                    "recover requires an executing or terminal session"
                )
            selected = view if view is not None else self._view
            if selected is None or self._plan is None:
                raise RecoveryUnavailableError(
                    "no validated view is available for recovery"
                )
            current_view = self._view
            if selected is not current_view:
                if (
                    current_view is None
                    or selected.recovery_binding() != current_view.recovery_binding()
                ):
                    raise RecoveryUnavailableError(
                        "recovery view is not bound to this session"
                    )
            if selected.recovery_ref is None:
                raise RecoveryUnavailableError("view has no recovery binding")
            project_root = self._plan.source.project_root
            if project_root is None:
                raise RecoveryUnavailableError("recovery source has no project root")
            result = self._engine.recover(
                project_root,
                self._plan.source.relative_path,
                selected.recovery_ref,
                selected.source_ref,
                selected.source_digest,
            )
            if not isinstance(result, RecoveredSource):
                raise ArtifactIntegrityError(
                    "Engine client returned an invalid recovery value"
                )
            if (
                result.recovery_ref != selected.recovery_ref
                or result.source_ref != selected.source_ref
                or result.source_digest != selected.source_digest
            ):
                raise ArtifactIntegrityError(
                    "recovery binding differs from the validated view"
                )
            return result

    def close(self) -> None:
        with self._lock:
            if self._state == SessionState.CLOSED.value:
                return
            if self._state not in {
                SessionState.COMPLETED.value,
                SessionState.ABORTED.value,
            }:
                raise SessionStateError("close requires a terminal receipt")
            self._state = SessionState.CLOSED.value

    def _ensure_not_terminal(self) -> None:
        if self._state in {
            SessionState.COMPLETED.value,
            SessionState.ABORTED.value,
            SessionState.CLOSED.value,
        }:
            raise SessionStateError("planning is not legal after terminal completion")

    def _add_degradation(self, value: str) -> None:
        if value not in self._degradations:
            self._degradations.append(value)

    def _abort_engine_failure(self, error: BaseException) -> None:
        self._first_error = error
        if isinstance(error, EngineExecutionError) and error.view is not None:
            self._view = error.view
        code = getattr(error, "code", "engine_error")
        self._add_degradation(f"engine:{code}")
        self._receipt = self._make_receipt(
            outcome=HostOutcome.ABORTED.value,
            host_result=None,
            usage=None,
            host_exception_type=None,
            host_exception=None,
        )
        self._state = SessionState.ABORTED.value

    def _make_receipt(
        self,
        *,
        outcome: str,
        host_result: object,
        usage: Optional[Mapping[str, object]],
        host_exception_type: Optional[str],
        host_exception: Optional[BaseException],
    ) -> ContextReceipt:
        integrity = (
            self._view.integrity_status.value
            if self._view is not None
            else Integrity.UNSEALED.value
        )
        if self._view is None:
            integrity = Integrity.UNSEALED.value
        return ContextReceipt(
            session_id=self._session_id,
            task_id=self._task_id,
            plan_id=self._plan.plan_id if self._plan is not None else None,
            view=self._view,
            outcome=outcome,
            integrity_status=integrity,
            degradations=tuple(self._degradations),
            usage=usage,
            host_exception_type=host_exception_type,
            host_result=host_result,
            host_exception=host_exception,
        )

    @staticmethod
    def _same_completion(
        receipt: ContextReceipt, outcome: str, usage: Optional[Mapping[str, object]]
    ) -> bool:
        if outcome != receipt.outcome:
            return False
        try:
            left = (
                canonical_bytes(_plain(receipt.usage))
                if receipt.usage is not None
                else None
            )
            right = canonical_bytes(_plain(usage)) if usage is not None else None
        except ValidationError:
            return False
        return left == right


__all__ = ["ContextSession"]
