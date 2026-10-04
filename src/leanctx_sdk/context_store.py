"""Preview: read-only access to the local Engine's Context Store.

`read_policy_evidence` returns the scope's content-free read-strategy evidence
(`lean-ctx engine context-policy-evidence`): per workload, strategy and UTC day
how many tasks were accepted or rejected, what was measured about quality,
security and runtime friction, and which strategy evaluations exist.
`read_task_lineage` returns one task's decision lineage (`lean-ctx engine
context-lineage`): ledger steps joined with its Decision Receipts, the outcome,
and every missing link named as a gap.

Both reads are confined to one tenant/project scope. Parsing is strict and
mirrors the Engine's validation: unknown fields or values and inconsistent
counts are rejected, and "unmeasured" never reads as a passing measurement.
Preview contract `leanctx-context-store-preview` 0.1; it may change in minor
releases.
"""

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .errors import EngineProtocolError, ValidationError
from .protocol import strict_json_loads

CONTEXT_STORE_PREVIEW_CONTRACT = "leanctx-context-store-preview"
CONTEXT_STORE_PREVIEW_VERSION = "0.1.0"
TRANSPORT_VERSION = 1
ENGINE_INTERFACE_VERSION = "1.0.0"
EVIDENCE_SCHEMA_VERSION = 1
LINEAGE_SCHEMA_VERSION = 1
MAX_STORE_REQUEST_BYTES = 16 * 1024
MAX_STORE_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_RECORDS = 4096
MAX_LINEAGE_ITEMS = 4096
MAX_REF_BYTES = 512
MAX_TEXT_BYTES = 4096

TASK_CLASSES = ("bug_fix", "refactor", "test_addition", "documentation", "investigation")
LANGUAGES = (
    "rust", "python", "type_script", "java_script", "go", "java", "c", "cpp", "c_sharp",
    "swift", "kotlin", "ruby", "php", "shell", "other", "none",
)
SIZES = ("tiny", "small", "medium", "large", "very_large")
STRATEGIES = (
    "full", "map", "signatures", "aggressive", "entropy", "task", "reference", "diff",
    "lines", "auto", "other",
)
EVIDENCE_TIERS = (
    "mechanism", "deterministic_quality", "recorded_regression", "live_task_evaluation",
    "production_outcome",
)
VERDICTS = ("improved", "non_inferior", "regressed", "underpowered")
STEP_KINDS = (
    "task_started", "plan_created", "context_delivered", "model_invoked", "engine_invoked",
    "receipt_signed", "canonical_receipt_recorded", "outcome_recorded", "decision_recorded",
)
OUTCOMES = ("accepted", "rejected", "unknown")
DELIVERY_OUTCOMES = ("delivered", "withheld", "failed")
DELIVERY_ERRORS = ("missing", "unreadable", "tampered")
GAPS = (
    "ledger_unverified", "no_plan_recorded", "no_delivery_recorded", "delivery_unverified",
    "deliveries_incomplete", "no_outcome_recorded",
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_SAFE = 2**53 - 1


def _bad(message: str) -> EngineProtocolError:
    return EngineProtocolError("context store: " + message)


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise _bad(f"{label} must be an object")
    return value


def _keys(value: Mapping[str, Any], required: set, optional: set, label: str) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise _bad(f"{label} lacks {sorted(missing)}")
    if unknown:
        raise _bad(f"{label} has unknown fields {sorted(unknown)}")


def _int(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= _MAX_SAFE:
        raise _bad(f"{label} must be an integer in {minimum}..{_MAX_SAFE}")
    return value


def _signed(value: Any, label: str) -> int:
    return _int(value, label, minimum=-_MAX_SAFE)


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise _bad(f"{label} must be a boolean")
    return value


def _text(value: Any, label: str, maximum: int = MAX_REF_BYTES) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > maximum
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)
    ):
        raise _bad(f"{label} must be bounded text")
    return value


def _enum(value: Any, allowed: Sequence[str], label: str) -> str:
    if value not in allowed:
        raise _bad(f"{label} has unknown value {value!r}")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise _bad(f"{label} must be a sha256 digest")
    return value


def _list(value: Any, label: str, maximum: int) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise _bad(f"{label} must be a list of at most {maximum}")
    return value


@dataclass(frozen=True)
class Workload:
    """Task class, dominant language and budget bucket of a plan."""

    task_class: str
    language: str
    size: str


@dataclass(frozen=True)
class QualityEvidence:
    """Critical retention and recovery on a task's deliveries, summed.

    `measured` is False when any delivery lacked a measurement; the counts are
    then absent, never zero.
    """

    measured: bool
    retained: Optional[int] = None
    recoverable: Optional[int] = None
    lost: Optional[int] = None
    handles_emitted: Optional[int] = None
    handles_verified: Optional[int] = None
    failures: Optional[int] = None
    critical_failures: Optional[int] = None


@dataclass(frozen=True)
class SecurityEvidence:
    """Deliveries that left without full inspection. Unmeasured is not zero."""

    measured: bool
    regressions: Optional[int] = None


@dataclass(frozen=True)
class StrategyOutcomeRecord:
    """Outcomes of one read strategy on one workload, on one UTC day."""

    workload: Workload
    strategy: str
    observed_day: int
    samples: int
    accepted: int
    rejected: int
    explicit_overrides: int
    token_samples: int
    signal_samples: int
    bounce_tasks: int
    expand_tasks: int
    edit_failure_tasks: int
    tokens_original: int
    tokens_delivered: int
    quality: QualityEvidence
    security: SecurityEvidence


@dataclass(frozen=True)
class StrategyEvaluation:
    """A paired task evaluation of one strategy (`lean-ctx eval frontier`)."""

    strategy: str
    evidence_tier: str
    verdict: str
    pairs: int
    powered: bool
    delta_milli: int
    ci_low_milli: int
    ci_high_milli: int
    margin_milli: int


@dataclass(frozen=True)
class ContextPolicyEvidence:
    """`ContextPolicyEvidenceV1`: one scope's evidence, sorted and bounded."""

    records: Tuple[StrategyOutcomeRecord, ...]
    evaluations: Tuple[StrategyEvaluation, ...]


@dataclass(frozen=True)
class LineageStep:
    """One ledger observation: identifiers and counts, no content."""

    sequence: int
    kind: str
    timestamp: str
    fields: Mapping[str, str]


@dataclass(frozen=True)
class DeliverySummary:
    outcome: str
    destination: str
    policy_digest: Optional[str]
    inspected: int
    delivered: int
    withheld: int
    redactions: int
    tokens_original: int
    tokens_delivered: int
    final_context: Optional[str]


@dataclass(frozen=True)
class LineageDelivery:
    """One governed delivery; unverified receipts name their error."""

    digest: str
    verified: bool
    error: Optional[str]
    summary: Optional[DeliverySummary]


@dataclass(frozen=True)
class TaskLineage:
    """One task's lineage. `gaps` lists every missing link; empty = complete."""

    task_id: str
    scope: Optional[str]
    steps: Tuple[LineageStep, ...]
    deliveries: Tuple[LineageDelivery, ...]
    ledger_error: Optional[str]
    outcome: str
    gaps: Tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.gaps


def _workload(value: Any, label: str) -> Workload:
    value = _object(value, label)
    _keys(value, {"task_class", "language", "size"}, set(), label)
    return Workload(
        task_class=_enum(value["task_class"], TASK_CLASSES, label + ".task_class"),
        language=_enum(value["language"], LANGUAGES, label + ".language"),
        size=_enum(value["size"], SIZES, label + ".size"),
    )


def _quality(value: Any, label: str) -> QualityEvidence:
    value = _object(value, label)
    state = value.get("state")
    if state == "unmeasured":
        _keys(value, {"state"}, set(), label)
        return QualityEvidence(measured=False)
    if state != "measured":
        raise _bad(f"{label}.state has unknown value {state!r}")
    _keys(value, {"state", "retention", "recovery"}, set(), label)
    retention = _object(value["retention"], label + ".retention")
    _keys(retention, {"retained", "recoverable", "lost"}, set(), label + ".retention")
    recovery = _object(value["recovery"], label + ".recovery")
    _keys(recovery, {"handles_emitted", "handles_verified", "failures", "critical_failures"},
          set(), label + ".recovery")
    quality = QualityEvidence(
        measured=True,
        retained=_int(retention["retained"], label + ".retained"),
        recoverable=_int(retention["recoverable"], label + ".recoverable"),
        lost=_int(retention["lost"], label + ".lost"),
        handles_emitted=_int(recovery["handles_emitted"], label + ".handles_emitted"),
        handles_verified=_int(recovery["handles_verified"], label + ".handles_verified"),
        failures=_int(recovery["failures"], label + ".failures"),
        critical_failures=_int(recovery["critical_failures"], label + ".critical_failures"),
    )
    if (
        quality.handles_verified > quality.handles_emitted
        or quality.failures > quality.handles_emitted
        or quality.critical_failures > quality.failures
    ):
        raise _bad(f"{label} has impossible recovery counts")
    return quality


def _security(value: Any, label: str) -> SecurityEvidence:
    value = _object(value, label)
    state = value.get("state")
    if state == "unmeasured":
        _keys(value, {"state"}, set(), label)
        return SecurityEvidence(measured=False)
    if state != "measured":
        raise _bad(f"{label}.state has unknown value {state!r}")
    _keys(value, {"state", "regressions"}, set(), label)
    return SecurityEvidence(measured=True, regressions=_int(value["regressions"], label))


_COUNTS = (
    "observed_day", "samples", "accepted", "rejected", "explicit_overrides", "token_samples",
    "tokens_original", "tokens_delivered",
)
_SIGNAL_COUNTS = ("signal_samples", "bounce_tasks", "expand_tasks", "edit_failure_tasks")


def _record(value: Any, label: str) -> StrategyOutcomeRecord:
    value = _object(value, label)
    _keys(value, {"workload", "strategy", "quality", "security", *_COUNTS}, set(_SIGNAL_COUNTS),
          label)
    counts = {key: _int(value[key], f"{label}.{key}") for key in _COUNTS}
    # Signal counts default to zero, like the Engine's own deserializer.
    counts.update({key: _int(value.get(key, 0), f"{label}.{key}") for key in _SIGNAL_COUNTS})
    record = StrategyOutcomeRecord(
        workload=_workload(value["workload"], label + ".workload"),
        strategy=_enum(value["strategy"], STRATEGIES, label + ".strategy"),
        quality=_quality(value["quality"], label + ".quality"),
        security=_security(value["security"], label + ".security"),
        **counts,
    )
    if record.samples == 0:
        raise _bad(f"{label} has no samples")
    if record.accepted + record.rejected != record.samples:
        raise _bad(f"{label}: accepted + rejected must equal samples")
    if record.explicit_overrides > record.samples or record.token_samples > record.samples:
        raise _bad(f"{label} counts more tasks than samples")
    if record.signal_samples > record.samples or any(
        count > record.signal_samples
        for count in (record.bounce_tasks, record.expand_tasks, record.edit_failure_tasks)
    ):
        raise _bad(f"{label} signal counts exceed their attributed tasks")
    if record.token_samples == 0 and (record.tokens_original or record.tokens_delivered):
        raise _bad(f"{label} has tokens without token samples")
    if record.tokens_delivered > record.tokens_original:
        raise _bad(f"{label} delivered more tokens than original")
    return record


def _evaluation(value: Any, label: str) -> StrategyEvaluation:
    value = _object(value, label)
    signed = ("delta_milli", "ci_low_milli", "ci_high_milli", "margin_milli")
    _keys(value, {"strategy", "evidence_tier", "verdict", "pairs", "powered", *signed}, set(),
          label)
    evaluation = StrategyEvaluation(
        strategy=_enum(value["strategy"], STRATEGIES, label + ".strategy"),
        evidence_tier=_enum(value["evidence_tier"], EVIDENCE_TIERS, label + ".evidence_tier"),
        verdict=_enum(value["verdict"], VERDICTS, label + ".verdict"),
        pairs=_int(value["pairs"], label + ".pairs"),
        powered=_bool(value["powered"], label + ".powered"),
        **{key: _signed(value[key], f"{label}.{key}") for key in signed},
    )
    if evaluation.strategy == "other":
        raise _bad(f"{label} must name a known strategy")
    if evaluation.ci_low_milli > evaluation.ci_high_milli or evaluation.margin_milli < 0:
        raise _bad(f"{label} has an impossible interval")
    if evaluation.powered and evaluation.pairs == 0:
        raise _bad(f"{label} is powered without pairs")
    return evaluation


def _record_key(record: StrategyOutcomeRecord) -> tuple:
    workload = record.workload
    return (
        TASK_CLASSES.index(workload.task_class), LANGUAGES.index(workload.language),
        SIZES.index(workload.size), STRATEGIES.index(record.strategy), record.observed_day,
    )


def parse_policy_evidence(value: Any) -> ContextPolicyEvidence:
    """Parse and validate a `ContextPolicyEvidenceV1` document."""
    value = _object(value, "evidence")
    _keys(value, {"schema_version", "records"}, {"evaluations"}, "evidence")
    if value["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise _bad("unsupported evidence schema_version")
    records = tuple(
        _record(item, f"records[{index}]")
        for index, item in enumerate(_list(value["records"], "records", MAX_EVIDENCE_RECORDS))
    )
    if any(_record_key(a) >= _record_key(b) for a, b in zip(records, records[1:])):
        raise _bad("records must be strictly sorted by workload, strategy and day")
    evaluations = tuple(
        _evaluation(item, f"evaluations[{index}]")
        for index, item in enumerate(
            _list(value.get("evaluations", []), "evaluations", len(STRATEGIES))
        )
    )
    if any(
        STRATEGIES.index(a.strategy) >= STRATEGIES.index(b.strategy)
        for a, b in zip(evaluations, evaluations[1:])
    ):
        raise _bad("evaluations must be one per strategy, sorted")
    return ContextPolicyEvidence(records=records, evaluations=evaluations)


def _step(value: Any, label: str) -> LineageStep:
    value = _object(value, label)
    _keys(value, {"sequence", "kind", "timestamp", "fields"}, set(), label)
    fields = _object(value["fields"], label + ".fields")
    for key, item in fields.items():
        if not _FIELD.fullmatch(key):
            raise _bad(f"{label}.fields has an invalid name")
        _text(item, f"{label}.fields.{key}")
    return LineageStep(
        sequence=_int(value["sequence"], label + ".sequence"),
        kind=_enum(value["kind"], STEP_KINDS, label + ".kind"),
        timestamp=_text(value["timestamp"], label + ".timestamp"),
        fields=dict(fields),
    )


def _summary(value: Any, label: str) -> DeliverySummary:
    value = _object(value, label)
    counts = ("inspected", "delivered", "withheld", "redactions", "tokens_original",
              "tokens_delivered")
    _keys(value, {"outcome", "destination", "policy_digest", "final_context", *counts}, set(),
          label)
    summary = DeliverySummary(
        outcome=_enum(value["outcome"], DELIVERY_OUTCOMES, label + ".outcome"),
        destination=_text(value["destination"], label + ".destination"),
        policy_digest=None if value["policy_digest"] is None
        else _digest(value["policy_digest"], label + ".policy_digest"),
        final_context=None if value["final_context"] is None
        else _digest(value["final_context"], label + ".final_context"),
        **{key: _int(value[key], f"{label}.{key}") for key in counts},
    )
    if summary.tokens_delivered > summary.tokens_original:
        raise _bad(f"{label} delivered more tokens than original")
    return summary


def _delivery(value: Any, label: str) -> LineageDelivery:
    value = _object(value, label)
    _keys(value, {"digest", "verified"}, {"error", "summary"}, label)
    verified = _bool(value["verified"], label + ".verified")
    if verified != ("summary" in value) or verified == ("error" in value):
        raise _bad(f"{label}: a verified delivery has a summary, an unverified one an error")
    return LineageDelivery(
        digest=_digest(value["digest"], label + ".digest"),
        verified=verified,
        error=_enum(value["error"], DELIVERY_ERRORS, label + ".error") if "error" in value
        else None,
        summary=_summary(value["summary"], label + ".summary") if verified else None,
    )


def parse_task_lineage(value: Any) -> TaskLineage:
    """Parse and validate a `TaskLineageV1` document."""
    value = _object(value, "lineage")
    _keys(value, {"schema_version", "task_id", "steps", "deliveries", "outcome", "gaps"},
          {"scope", "ledger_error"}, "lineage")
    if value["schema_version"] != LINEAGE_SCHEMA_VERSION:
        raise _bad("unsupported lineage schema_version")
    gaps = tuple(
        _enum(item, GAPS, "gaps") for item in _list(value["gaps"], "gaps", len(GAPS))
    )
    if len(set(gaps)) != len(gaps):
        raise _bad("gaps must not repeat")
    ledger_error = value.get("ledger_error")
    if ledger_error is not None:
        _text(ledger_error, "ledger_error", MAX_TEXT_BYTES)
    if (ledger_error is not None) != ("ledger_unverified" in gaps):
        raise _bad("a ledger error and the ledger_unverified gap go together")
    steps = tuple(
        _step(item, f"steps[{index}]")
        for index, item in enumerate(_list(value["steps"], "steps", MAX_LINEAGE_ITEMS))
    )
    deliveries = tuple(
        _delivery(item, f"deliveries[{index}]")
        for index, item in enumerate(
            _list(value["deliveries"], "deliveries", MAX_LINEAGE_ITEMS)
        )
    )
    if any(not delivery.verified for delivery in deliveries) != ("delivery_unverified" in gaps):
        raise _bad("an unverified delivery and the delivery_unverified gap go together")
    return TaskLineage(
        task_id=_text(value["task_id"], "task_id"),
        scope=_text(value["scope"], "scope", MAX_TEXT_BYTES) if "scope" in value else None,
        steps=steps,
        deliveries=deliveries,
        ledger_error=ledger_error,
        outcome=_enum(value["outcome"], OUTCOMES, "outcome"),
        gaps=gaps,
    )


def store_request(
    *,
    project_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Mapping[str, Any]:
    """The `ContextStoreRequestV1` document for one read."""
    request = {
        "schema_version": 1,
        "transport_version": TRANSPORT_VERSION,
        "engine_interface_version": ENGINE_INTERFACE_VERSION,
    }
    for key, item in (("project_id", project_id), ("tenant_id", tenant_id), ("task_id", task_id)):
        if item is None:
            continue
        if not isinstance(item, str) or not item.strip() or len(item.encode("utf-8")) > MAX_REF_BYTES:
            raise ValidationError(f"{key} must be a non-empty bounded string")
        request[key] = item
    return request


def _read(engine, operation: str, project_root: str, request: Mapping[str, Any], body: str):
    raw = engine._invoke_bytes(  # noqa: SLF001 - same package, bounded runner
        operation, project_root, request, maximum=MAX_STORE_REQUEST_BYTES,
    )
    if len(raw) > MAX_STORE_RESPONSE_BYTES:
        raise _bad("response exceeds its bound")
    try:
        document = strict_json_loads(raw, label="context store")
    except ValidationError as exc:
        raise _bad("response is not strict JSON") from exc
    document = _object(document, "response")
    _keys(document, {"schema_version", "transport_version", "engine_interface_version", body},
          set(), "response")
    if (
        document["schema_version"] != 1
        or document["transport_version"] != TRANSPORT_VERSION
        or document["engine_interface_version"] != ENGINE_INTERFACE_VERSION
    ):
        raise _bad("unsupported response versions")
    return document[body]


def read_policy_evidence(
    engine,
    project_root: str,
    *,
    project_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> ContextPolicyEvidence:
    """The scope's read-strategy evidence. `engine` is a `SubprocessEngineClient`.

    Without `project_id` the Engine uses `project_root`, as `lean-ctx autopilot`
    does by default.
    """
    request = store_request(project_id=project_id, tenant_id=tenant_id)
    return parse_policy_evidence(
        _read(engine, "context-policy-evidence", project_root, request, "evidence")
    )


def read_task_lineage(
    engine,
    project_root: str,
    task_id: str,
    *,
    project_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> TaskLineage:
    """One task's lineage within the scope. `engine` is a `SubprocessEngineClient`."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValidationError("task_id must be a non-empty string")
    request = store_request(project_id=project_id, tenant_id=tenant_id, task_id=task_id)
    return parse_task_lineage(
        _read(engine, "context-lineage", project_root, request, "lineage")
    )
