"""Preview: the information gateway's egress admission and decision receipts.

`admit_egress` runs one model request through the local Engine's egress
admission (`lean-ctx engine egress-admit`) before the caller sends it: secrets
are masked, restricted content is withheld, and the request is classified.
The result names what may leave (`body`), how sensitive it is
(`classification`) and why (`receipt`, a `ContextDecisionReceipt`).

Parsing is strict and mirrors the Engine's own validation: unknown fields,
unknown enum values and inconsistent receipts are rejected, never defaulted.
Preview contract `leanctx-gateway-preview` 0.1; it may change in minor
releases.
"""

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .errors import EngineProtocolError, ValidationError
from .protocol import canonical_bytes, strict_json_loads

GATEWAY_PREVIEW_CONTRACT = "leanctx-gateway-preview"
GATEWAY_PREVIEW_VERSION = "0.1.0"
EGRESS_SCHEMA_VERSION = 1
MAX_EGRESS_REQUEST_BYTES = 8 * 1024 * 1024
MAX_EGRESS_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_REF_BYTES = 512
MAX_DECISIONS = 4096
MAX_REASON_CODES = 32
MAX_SIGNALS = 32

CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
DISPOSITIONS = ("forward", "rewritten", "refused")
MODES = ("developer", "governed", "sovereign")
PRINCIPAL_KINDS = (
    "person", "team", "organization", "project", "agent", "session", "workload", "unknown",
)
LOCALITIES = ("local", "remote", "unknown")
OUTCOMES = ("delivered", "withheld", "failed")
CONTEXT_DISPOSITIONS = (
    "allow", "allow_minimized", "allow_redacted", "allow_summary_only",
    "allow_local_model_only", "allow_with_approval", "quarantine", "deny",
)
TRANSFORMATIONS = (
    "redaction", "classification", "selection", "deduplication",
    "structural_extraction", "compression", "summarization", "recovery", "reranking",
)
CATEGORIES = ("secret", "pii", "prompt_injection", "classification", "policy", "custom")
SEVERITIES = ("info", "low", "medium", "high", "critical")
COVERAGE_KINDS = ("complete", "partial", "unsupported", "failed", "not_required")
DETECTOR_STATUSES = ("completed", "failed", "timed_out", "skipped")

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")
_MAX_SAFE = 2**53 - 1


def _bad(message: str) -> EngineProtocolError:
    return EngineProtocolError("egress admission: " + message)


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


def _int(value: Any, label: str, maximum: int = _MAX_SAFE) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise _bad(f"{label} must be an integer in 0..{maximum}")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise _bad(f"{label} must be a boolean")
    return value


def _ref(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > MAX_REF_BYTES
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)
    ):
        raise _bad(f"{label} must be a bounded reference")
    return value


def _enum(value: Any, allowed: Sequence[str], label: str) -> str:
    if value not in allowed:
        raise _bad(f"{label} has unknown value {value!r}")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise _bad(f"{label} must be a sha256 digest")
    return value


def _reasons(value: Any, label: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_REASON_CODES:
        raise _bad(f"{label} must be a bounded list")
    for code in value:
        if not isinstance(code, str) or not _REASON.fullmatch(code):
            raise _bad(f"{label} holds an invalid reason code")
    return tuple(value)


@dataclass(frozen=True)
class ContextPrincipal:
    """Who requested the context. `unknown` is explicit and never authorizes."""

    kind: str
    id: Optional[str] = None

    @property
    def is_known(self) -> bool:
        return self.kind != "unknown"


@dataclass(frozen=True)
class ContextDestination:
    """Where the context goes. Organisation management is only ever attested."""

    provider: str
    locality: str
    model: Optional[str] = None
    organization_managed: bool = False
    account_ref: Optional[str] = None
    region: Optional[str] = None


@dataclass(frozen=True)
class DetectorCoverage:
    """What a detector actually inspected; never more than recorded."""

    kind: str
    bytes_total: int
    bytes_inspected: int
    chunks_total: int
    chunks_inspected: int
    reason: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return self.kind == "complete"


@dataclass(frozen=True)
class SecuritySignal:
    """One detector's result: counts only, never the matched value."""

    detector_id: str
    detector_version: str
    category: str
    severity: str
    evidence_count: int
    coverage: DetectorCoverage
    status: str
    latency_us: int
    calibrated: bool = False
    confidence_milli: Optional[int] = None

    @property
    def satisfies_requirement(self) -> bool:
        return self.status == "completed" and self.coverage.is_complete


@dataclass(frozen=True)
class ContextDecision:
    """The gateway's decision about one object (by digest, never content)."""

    object: str
    disposition: str
    reason_codes: Tuple[str, ...] = ()
    signals: Tuple[SecuritySignal, ...] = ()
    required_transformations: Tuple[str, ...] = ()

    @property
    def delivers_content(self) -> bool:
        return CONTEXT_DISPOSITIONS.index(self.disposition) <= CONTEXT_DISPOSITIONS.index(
            "allow_local_model_only"
        )


@dataclass(frozen=True)
class ContextDecisionReceipt:
    """One governed delivery: who, where, under which policy, what was
    inspected, withheld or transformed, and the delivered context's digest."""

    receipt_id: str
    mode: str
    principal: ContextPrincipal
    destination: ContextDestination
    sources: Mapping[str, int]
    security: Mapping[str, int]
    tokens: Mapping[str, int]
    outcome: str
    duration_us: int
    decisions: Tuple[ContextDecision, ...] = ()
    policy: Optional[Mapping[str, str]] = None
    task: Optional[str] = None
    final_context: Optional[str] = None
    quality: Optional[Mapping[str, Any]] = None

    @property
    def signals(self) -> Tuple[SecuritySignal, ...]:
        return tuple(signal for decision in self.decisions for signal in decision.signals)


@dataclass(frozen=True)
class EgressAdmission:
    """What may leave for the model, how sensitive it is, and why."""

    disposition: str
    body: Optional[Mapping[str, Any]]
    refusal: Optional[str]
    classification: Optional[str]
    receipt: Optional[ContextDecisionReceipt]

    @property
    def may_send(self) -> bool:
        """True when `body` may be sent; a refused request must not be."""
        return self.disposition != "refused"


def _principal(value: Any) -> ContextPrincipal:
    value = _object(value, "principal")
    _keys(value, {"kind"}, {"id"}, "principal")
    kind = _enum(value["kind"], PRINCIPAL_KINDS, "principal.kind")
    if kind == "unknown":
        if "id" in value:
            raise _bad("an unknown principal must not carry an identity")
        return ContextPrincipal(kind)
    if "id" not in value:
        raise _bad("a known principal requires an id")
    return ContextPrincipal(kind, _ref(value["id"], "principal.id"))


def _destination(value: Any) -> ContextDestination:
    value = _object(value, "destination")
    _keys(value, {"provider", "locality"},
          {"model", "organization_managed", "account_ref", "region"}, "destination")
    optional = {
        key: _ref(value[key], "destination." + key)
        for key in ("model", "account_ref", "region") if key in value
    }
    return ContextDestination(
        provider=_ref(value["provider"], "destination.provider"),
        locality=_enum(value["locality"], LOCALITIES, "destination.locality"),
        organization_managed=_bool(value.get("organization_managed", False),
                                   "destination.organization_managed"),
        **optional,
    )


def _coverage(value: Any) -> DetectorCoverage:
    value = _object(value, "coverage")
    _keys(value, {"kind", "bytes_total", "bytes_inspected", "chunks_total", "chunks_inspected"},
          {"reason"}, "coverage")
    coverage = DetectorCoverage(
        kind=_enum(value["kind"], COVERAGE_KINDS, "coverage.kind"),
        bytes_total=_int(value["bytes_total"], "coverage.bytes_total"),
        bytes_inspected=_int(value["bytes_inspected"], "coverage.bytes_inspected"),
        chunks_total=_int(value["chunks_total"], "coverage.chunks_total", 2**32 - 1),
        chunks_inspected=_int(value["chunks_inspected"], "coverage.chunks_inspected", 2**32 - 1),
        reason=_reasons([value["reason"]], "coverage.reason")[0] if "reason" in value else None,
    )
    if (coverage.bytes_inspected > coverage.bytes_total
            or coverage.chunks_inspected > coverage.chunks_total):
        raise _bad("coverage must not inspect more than the object holds")
    all_bytes = coverage.bytes_inspected == coverage.bytes_total
    if coverage.kind == "complete" and not all_bytes:
        raise _bad("complete coverage must inspect every byte")
    if coverage.kind == "partial" and all_bytes:
        raise _bad("partial coverage must leave bytes uninspected")
    return coverage


def _signal(value: Any) -> SecuritySignal:
    value = _object(value, "signal")
    _keys(value, {"detector", "category", "severity", "evidence_count", "coverage", "status",
                  "latency_us"}, {"calibrated", "confidence_milli"}, "signal")
    detector = _object(value["detector"], "signal.detector")
    _keys(detector, {"id", "version"}, set(), "signal.detector")
    signal = SecuritySignal(
        detector_id=_ref(detector["id"], "signal.detector.id"),
        detector_version=_ref(detector["version"], "signal.detector.version"),
        category=_enum(value["category"], CATEGORIES, "signal.category"),
        severity=_enum(value["severity"], SEVERITIES, "signal.severity"),
        evidence_count=_int(value["evidence_count"], "signal.evidence_count", 2**32 - 1),
        coverage=_coverage(value["coverage"]),
        status=_enum(value["status"], DETECTOR_STATUSES, "signal.status"),
        latency_us=_int(value["latency_us"], "signal.latency_us"),
        calibrated=_bool(value.get("calibrated", False), "signal.calibrated"),
        confidence_milli=(_int(value["confidence_milli"], "signal.confidence_milli", 1000)
                          if "confidence_milli" in value else None),
    )
    if signal.status in ("failed", "timed_out") and signal.coverage.is_complete:
        raise _bad("a failed or timed-out detector cannot claim complete coverage")
    return signal


def _decision(value: Any) -> ContextDecision:
    value = _object(value, "decision")
    _keys(value, {"object", "disposition"},
          {"reason_codes", "signals", "required_transformations"}, "decision")
    signals = value.get("signals", [])
    transformations = value.get("required_transformations", [])
    if not isinstance(signals, list) or len(signals) > MAX_SIGNALS:
        raise _bad("decision.signals must be a bounded list")
    if not isinstance(transformations, list):
        raise _bad("decision.required_transformations must be a list")
    decision = ContextDecision(
        object=_digest(value["object"], "decision.object"),
        disposition=_enum(value["disposition"], CONTEXT_DISPOSITIONS, "decision.disposition"),
        reason_codes=_reasons(value.get("reason_codes", []), "decision.reason_codes"),
        signals=tuple(_signal(signal) for signal in signals),
        required_transformations=tuple(
            _enum(kind, TRANSFORMATIONS, "decision.required_transformations")
            for kind in transformations
        ),
    )
    if decision.disposition != "allow" and not decision.reason_codes:
        raise _bad("every non-allow decision requires at least one reason code")
    return decision


def _counts(value: Any, keys: Sequence[str], label: str, maximum: int) -> Mapping[str, int]:
    value = _object(value, label)
    _keys(value, set(keys), set(), label)
    return {key: _int(value[key], f"{label}.{key}", maximum) for key in keys}


def parse_decision_receipt(value: Any) -> ContextDecisionReceipt:
    """Parse and validate a `ContextDecisionReceiptV1` document."""
    value = _object(value, "receipt")
    _keys(value, {"schema_version", "receipt_id", "mode", "principal", "destination", "sources",
                  "security", "tokens", "outcome", "duration_us"},
          {"task", "policy", "decisions", "final_context", "quality"}, "receipt")
    if value["schema_version"] != 1:
        raise _bad("unsupported receipt schema_version")
    decisions = value.get("decisions", [])
    if not isinstance(decisions, list) or len(decisions) > MAX_DECISIONS:
        raise _bad("receipt.decisions must be a bounded list")
    parsed = tuple(_decision(decision) for decision in decisions)
    sources = _counts(value["sources"], ("inspected", "permitted", "selected", "blocked"),
                      "receipt.sources", 2**32 - 1)
    if (sources["selected"] > sources["permitted"]
            or sources["permitted"] + sources["blocked"] > sources["inspected"]):
        raise _bad("source counts must satisfy selected <= permitted and "
                   "permitted + blocked <= inspected")
    security = _counts(value["security"], ("redactions", "blocked_objects",
                                           "quarantined_objects", "injection_signals",
                                           "incomplete_coverage"),
                       "receipt.security", 2**32 - 1)
    if (security["blocked_objects"] != sum(d.disposition == "deny" for d in parsed)
            or security["quarantined_objects"]
            != sum(d.disposition == "quarantine" for d in parsed)):
        raise _bad("security counts must equal the recorded deny/quarantine decisions")
    outcome = _enum(value["outcome"], OUTCOMES, "receipt.outcome")
    final_context = (_digest(value["final_context"], "receipt.final_context")
                     if "final_context" in value else None)
    if (outcome == "delivered") != (final_context is not None):
        raise _bad("exactly a delivered receipt names the delivered context digest")
    policy = None
    if "policy" in value:
        policy_value = _object(value["policy"], "receipt.policy")
        _keys(policy_value, {"id", "digest"}, {"version"}, "receipt.policy")
        policy = {"id": _ref(policy_value["id"], "receipt.policy.id"),
                  "digest": _digest(policy_value["digest"], "receipt.policy.digest")}
        if "version" in policy_value:
            policy["version"] = _ref(policy_value["version"], "receipt.policy.version")
    quality = value.get("quality")
    if quality is not None:
        quality = _object(quality, "receipt.quality")
    return ContextDecisionReceipt(
        receipt_id=_ref(value["receipt_id"], "receipt.receipt_id"),
        mode=_enum(value["mode"], MODES, "receipt.mode"),
        principal=_principal(value["principal"]),
        destination=_destination(value["destination"]),
        sources=sources,
        security=security,
        tokens=_counts(value["tokens"], ("original", "delivered"), "receipt.tokens", _MAX_SAFE),
        outcome=outcome,
        duration_us=_int(value["duration_us"], "receipt.duration_us"),
        decisions=parsed,
        policy=policy,
        task=_ref(value["task"], "receipt.task") if "task" in value else None,
        final_context=final_context,
        quality=quality,
    )


def parse_egress_admission(value: Any) -> EgressAdmission:
    """Parse and validate an `EngineEgressAdmissionResponseV1` document."""
    value = _object(value, "response")
    _keys(value, {"schema_version", "disposition"},
          {"body", "refusal", "classification", "receipt"}, "response")
    if value["schema_version"] != EGRESS_SCHEMA_VERSION:
        raise _bad("unsupported egress schema_version")
    disposition = _enum(value["disposition"], DISPOSITIONS, "disposition")
    body = value.get("body")
    refusal = value.get("refusal")
    if disposition == "refused":
        if body is not None or not isinstance(refusal, str) or not refusal.strip():
            raise _bad("a refused request carries a refusal and no body")
    else:
        if refusal is not None or not isinstance(body, dict):
            raise _bad("an admitted request carries a body object and no refusal")
    classification = value.get("classification")
    if classification is not None:
        _enum(classification, CLASSIFICATIONS, "classification")
    receipt = value.get("receipt")
    return EgressAdmission(
        disposition=disposition,
        body=body,
        refusal=refusal,
        classification=classification,
        receipt=parse_decision_receipt(receipt) if receipt is not None else None,
    )


def egress_request(provider: str, upstream_base: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `EngineEgressAdmissionRequestV1` document for one model request."""
    if not isinstance(provider, str) or not provider.strip():
        raise ValidationError("provider must be a non-empty string")
    if not isinstance(upstream_base, str) or not upstream_base.startswith(("https://", "http://")):
        raise ValidationError("upstream_base must be an http(s) URL")
    if not isinstance(body, dict):
        raise ValidationError("body must be a JSON object")
    request = {"schema_version": EGRESS_SCHEMA_VERSION, "provider": provider,
               "upstream_base": upstream_base, "body": body}
    if len(canonical_bytes(request)) > MAX_EGRESS_REQUEST_BYTES:
        raise ValidationError("egress request exceeds its bound")
    return request


def admit_egress(
    engine,
    project_root: str,
    *,
    provider: str,
    upstream_base: str,
    body: Mapping[str, Any],
) -> EgressAdmission:
    """Admit one model request through the local Engine before sending it.

    `engine` is a `SubprocessEngineClient`. Send `admission.body`, never the
    original body, and only when `admission.may_send`.
    """
    request = egress_request(provider, upstream_base, body)
    raw = engine._invoke_bytes(  # noqa: SLF001 - same package, bounded runner
        "egress-admit", project_root, request,
        maximum=MAX_EGRESS_REQUEST_BYTES, stdin=True,
    )
    if len(raw) > MAX_EGRESS_RESPONSE_BYTES:
        raise _bad("response exceeds its bound")
    try:
        document = strict_json_loads(raw, label="egress admission")
    except ValidationError as exc:
        raise _bad("response is not strict JSON") from exc
    return parse_egress_admission(document)
