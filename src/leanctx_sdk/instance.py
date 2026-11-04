# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Public instance transport; v4 adds bounded, digest-validated planning operations."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple, Union, cast

from .errors import (
    ArtifactIntegrityError,
    CompatibilityError,
    ConfigurationError,
    InstanceExecutionError,
    InstanceProtocolError,
    InstanceRejected,
    InstanceTimeout,
    InstanceUnavailable,
    PolicyAdmissionError,
    SourceUnavailableError,
    UnsupportedInstanceError,
    ValidationError,
)
from .planning import (
    InstancePlanningRequest,
    InstanceSource,
    parse_context_plan,
    parse_source_plan,
)
from .protocol import (
    ENGINE_INTERFACE_VERSION,
    MAX_MEASUREMENTS,
    MAX_PATH_BYTES,
    MAX_REFS,
    MAX_REF_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_STDERR_BYTES,
    MAX_TEXT_BYTES,
    SCHEMA_VERSION,
    TRANSPORT_VERSION,
    ContextFailure,
    ContextMeasurement,
    ContextPlan,
    ContextReceiptLink,
    ContextSource,
    ContextView,
    InstanceStatus,
    FailureCode,
    RecoveredSource,
    canonical_bytes,
    exact_keys,
    sha256_digest,
    strict_json_loads,
    validate_digest,
    validate_output_ref,
    validate_ref,
)


class InstanceClient(Protocol):
    """The only injected transport seam owned by the Product SDK."""

    def context_view(self, plan: ContextPlan) -> ContextView: ...

    def recover(
        self,
        project_root: str,
        path: str,
        recovery_ref: str,
        source_ref: str,
        source_digest: str,
    ) -> RecoveredSource: ...


class InstancePlanningClient(Protocol):
    """Additive planning seam; existing injected InstanceClient implementations remain valid."""

    def context_plan(
        self, project_root: str, request: InstancePlanningRequest
    ) -> Mapping[str, object]: ...

    def context_plan_sources(
        self,
        project_root: str,
        request: InstancePlanningRequest,
        sources: Sequence[InstanceSource],
    ) -> Mapping[str, object]: ...


_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "transport_version",
    "engine_interface_version",
    "view",
    "invocation",
    "observation",
    "recovery",
}
_VIEW_KEYS = {"text", "output_ref", "output_digest"}
_RECOVERY_KEYS = {"recovery_ref", "source_ref", "source_digest"}
_INVOCATION_KEYS = {
    "schema_version",
    "invocation_id",
    "engine",
    "operation",
    "input_ref",
    "input_digest",
    "source_refs",
    "policy_admission",
}
_ENGINE_KEYS = {"engine_id", "engine_version"}
_OPERATION_KEYS = {"capability_id", "capability_version"}
_POLICY_KEYS = {"policy_ref", "decision"}
_OBSERVATION_KEYS = {
    "schema_version",
    "invocation_id",
    "status",
    "output_ref",
    "output_digest",
    "source_lineage",
    "measurements",
    "failure",
    "receipt_link",
}
_OBSERVATION_REQUIRED_KEYS = {
    "schema_version",
    "invocation_id",
    "status",
    "source_lineage",
    "measurements",
}
_MEASUREMENT_KEYS = {"name", "unit", "classification", "value"}
_FAILURE_KEYS = {"code", "retryable_by_host", "recovery_ref"}
_RECEIPT_LINK_KEYS = {
    "schema_version",
    "receipt_id",
    "receipt_ref",
    "receipt_digest",
    "invocation_id",
}
_FAILURE_CODES = {item.value for item in FailureCode}


def _protocol(message: str) -> InstanceProtocolError:
    return InstanceProtocolError(message)


def _compatibility(message: str) -> CompatibilityError:
    return CompatibilityError(message)


def _unsupported_instance(message: str) -> UnsupportedInstanceError:
    return UnsupportedInstanceError(message)


def _exact_keys(value: Mapping[str, Any], expected: set, label: str) -> None:
    try:
        exact_keys(value, expected, label)
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc


def _int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _protocol(f"{field_name} must be an integer")
    return value


def _string(value: Any, field_name: str, maximum: int = MAX_REF_BYTES) -> str:
    if not isinstance(value, str):
        raise _protocol(f"{field_name} must be a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise _protocol(f"{field_name} is not valid UTF-8") from exc
    if len(encoded) > maximum:
        raise _protocol(f"{field_name} exceeds its bound")
    if "\x00" in value:
        raise _protocol(f"{field_name} contains NUL")
    return value


def _optional_digest(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    try:
        return validate_digest(value, field_name)
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc


def _optional_output_ref(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    try:
        return validate_output_ref(value, field_name)
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc


def _required_ref(value: Any, field_name: str) -> str:
    try:
        return validate_ref(value, field_name)
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc


def _validate_pair(
    output_ref: Optional[str], output_digest: Optional[str], label: str
) -> None:
    # Recovery v1 intentionally permits a null output_ref while retaining the
    # source-bound output_digest. A materialized context view requires both.
    if output_ref is not None and output_digest is None:
        raise _protocol(f"{label} output reference requires a digest")
    if output_ref is not None and output_digest is not None:
        expected = "output:" + output_digest.removeprefix("sha256:")
        if output_ref != expected:
            raise _protocol(f"{label} output reference does not match digest")


def _parse_measurement(value: Any) -> ContextMeasurement:
    if not isinstance(value, Mapping):
        raise _protocol("measurement must be an object")
    try:
        _exact_keys(value, _MEASUREMENT_KEYS, "measurement")
        name = value["name"]
        unit = value["unit"]
        classification = value["classification"]
        measurement_value = value["value"]
        return ContextMeasurement(name, unit, classification, measurement_value)
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc


def _parse_failure(value: Any) -> Optional[ContextFailure]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _protocol("failure must be an object or null")
    _exact_keys(value, _FAILURE_KEYS, "failure")
    code = value["code"]
    if code not in _FAILURE_CODES:
        raise _protocol("unknown instance failure code")
    recovery_ref = value["recovery_ref"]
    if recovery_ref is not None:
        recovery_ref = _required_ref(recovery_ref, "failure.recovery_ref")
    try:
        return ContextFailure(code, value["retryable_by_host"], recovery_ref)
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc


def _parse_receipt_link(value: Any, invocation_id: str) -> Optional[ContextReceiptLink]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _protocol("receipt_link must be an object or null")
    _exact_keys(value, _RECEIPT_LINK_KEYS, "receipt_link")
    receipt_digest = _optional_digest(
        value["receipt_digest"], "receipt_link.receipt_digest"
    )
    receipt_ref = _required_ref(value["receipt_ref"], "receipt_link.receipt_ref")
    if receipt_digest is None:
        raise _protocol("receipt_link.receipt_digest is required")
    if receipt_ref != "receipt:" + receipt_digest:
        raise _protocol("receipt_link.receipt_ref does not match digest")
    if value["invocation_id"] != invocation_id:
        raise _protocol("receipt_link invocation binding mismatch")
    try:
        return ContextReceiptLink(
            _int(value["schema_version"], "receipt_link.schema_version"),
            _required_ref(value["receipt_id"], "receipt_link.receipt_id"),
            receipt_ref,
            receipt_digest,
            _string(value["invocation_id"], "receipt_link.invocation_id"),
        )
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc


def _parse_invocation(value: Any) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _protocol("invocation must be an object or null")
    _exact_keys(value, _INVOCATION_KEYS, "invocation")
    engine = value["engine"]
    operation = value["operation"]
    policy = value["policy_admission"]
    if (
        not isinstance(engine, Mapping)
        or not isinstance(operation, Mapping)
        or not isinstance(policy, Mapping)
    ):
        raise _protocol("invocation nested records must be objects")
    _exact_keys(engine, _ENGINE_KEYS, "invocation.engine")
    _exact_keys(operation, _OPERATION_KEYS, "invocation.operation")
    _exact_keys(policy, _POLICY_KEYS, "invocation.policy_admission")
    engine_id = _string(engine["engine_id"], "invocation.engine.engine_id")
    engine_version = _string(
        engine["engine_version"], "invocation.engine.engine_version"
    )
    if engine_id != "lean-ctx-local" or not _SEMVER_RE.fullmatch(engine_version):
        raise _unsupported_instance("unsupported instance identity")
    if int(engine_version.split(".", 1)[0]) != 3:
        raise _unsupported_instance("unsupported instance major version")
    capability_id = _string(
        operation["capability_id"], "invocation.operation.capability_id"
    )
    capability_version = _string(
        operation["capability_version"], "invocation.operation.capability_version"
    )
    if (
        capability_id != "capability://leanctx/context-optimization"
        or capability_version != "1.0.0"
    ):
        raise _unsupported_instance("unsupported instance capability")
    decision = policy["decision"]
    if decision not in {"admitted", "rejected"}:
        raise _protocol("unknown policy decision")
    policy_ref = _required_ref(
        policy["policy_ref"], "invocation.policy_admission.policy_ref"
    )
    invocation_id = _string(value["invocation_id"], "invocation.invocation_id")
    if _int(value["schema_version"], "invocation.schema_version") != SCHEMA_VERSION:
        raise _protocol("unsupported invocation schema version")
    input_ref = _required_ref(value["input_ref"], "invocation.input_ref")
    input_digest = _optional_digest(value["input_digest"], "invocation.input_digest")
    if input_digest is None:
        raise _protocol("invocation.input_digest is required")
    source_refs_value = value["source_refs"]
    if (
        not isinstance(source_refs_value, list)
        or not 0 < len(source_refs_value) <= MAX_REFS
    ):
        raise _protocol("invocation.source_refs exceeds its bound")
    source_refs = tuple(
        _required_ref(item, "invocation.source_refs") for item in source_refs_value
    )
    if len(set(source_refs)) != len(source_refs):
        raise _protocol("invocation.source_refs contains duplicates")
    if input_ref not in source_refs:
        raise _protocol("invocation input_ref is not in source_refs")
    return {
        "schema_version": SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "engine": {"engine_id": engine_id, "engine_version": engine_version},
        "operation": {
            "capability_id": capability_id,
            "capability_version": capability_version,
        },
        "input_ref": input_ref,
        "input_digest": input_digest,
        "source_refs": source_refs,
        "policy_admission": {"policy_ref": policy_ref, "decision": decision},
    }


def _parse_observation(value: Any, invocation_id: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _protocol("observation must be an object or null")
    if set(value) - _OBSERVATION_KEYS or not _OBSERVATION_REQUIRED_KEYS.issubset(value):
        raise _protocol("observation fields do not match the v1 contract")
    observed_invocation_id = _string(
        value["invocation_id"], "observation.invocation_id"
    )
    if observed_invocation_id != invocation_id:
        raise _protocol("observation invocation binding mismatch")
    if _int(value["schema_version"], "observation.schema_version") != SCHEMA_VERSION:
        raise _protocol("unsupported observation schema version")
    status = value["status"]
    if status not in {item.value for item in InstanceStatus}:
        raise _protocol("unknown observation status")
    output_ref = _optional_output_ref(value.get("output_ref"), "observation.output_ref")
    output_digest = _optional_digest(
        value.get("output_digest"), "observation.output_digest"
    )
    _validate_pair(output_ref, output_digest, "observation")
    lineage_value = value["source_lineage"]
    if not isinstance(lineage_value, list) or not 0 < len(lineage_value) <= MAX_REFS:
        raise _protocol("observation.source_lineage exceeds its bound")
    lineage = tuple(
        _required_ref(item, "observation.source_lineage") for item in lineage_value
    )
    if len(set(lineage)) != len(lineage):
        raise _protocol("observation.source_lineage contains duplicates")
    measurements_value = value["measurements"]
    if (
        not isinstance(measurements_value, list)
        or len(measurements_value) > MAX_MEASUREMENTS
    ):
        raise _protocol("observation.measurements exceeds its bound")
    measurements = tuple(_parse_measurement(item) for item in measurements_value)
    failure = _parse_failure(value.get("failure"))
    receipt_link = _parse_receipt_link(value.get("receipt_link"), invocation_id)
    if status in {"succeeded", "degraded"} and failure is not None:
        raise _protocol("successful/degraded observation cannot contain failure")
    if status in {"failed", "rejected"} and failure is None:
        raise _protocol("failed/rejected observation requires failure")
    if status == "succeeded" and receipt_link is None:
        raise _protocol("succeeded observation requires receipt_link")
    return {
        "schema_version": SCHEMA_VERSION,
        "invocation_id": observed_invocation_id,
        "status": status,
        "output_ref": output_ref,
        "output_digest": output_digest,
        "source_lineage": lineage,
        "measurements": measurements,
        "failure": failure,
        "receipt_link": receipt_link,
    }


def _parse_view(value: Any) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _protocol("view must be an object")
    _exact_keys(value, _VIEW_KEYS, "view")
    text = _string(value["text"], "view.text", MAX_TEXT_BYTES)
    output_ref = _optional_output_ref(value["output_ref"], "view.output_ref")
    output_digest = _optional_digest(value["output_digest"], "view.output_digest")
    _validate_pair(output_ref, output_digest, "view")
    if (
        output_digest is not None
        and sha256_digest(text.encode("utf-8")) != output_digest
    ):
        raise _protocol("view output digest mismatch")
    return {"text": text, "output_ref": output_ref, "output_digest": output_digest}


def _parse_recovery(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise _protocol("recovery must be an object")
    _exact_keys(value, _RECOVERY_KEYS, "recovery")
    source_digest = _optional_digest(value["source_digest"], "recovery.source_digest")
    if source_digest is None:
        raise _protocol("recovery.source_digest is required")
    return {
        "recovery_ref": _required_ref(value["recovery_ref"], "recovery.recovery_ref"),
        "source_ref": _required_ref(value["source_ref"], "recovery.source_ref"),
        "source_digest": source_digest,
    }


def _parse_response(
    raw: bytes,
) -> Tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _protocol("Instance response exceeds the bound")
    try:
        decoded = strict_json_loads(raw, label="Instance response")
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc
    try:
        exact_keys(decoded, _TOP_LEVEL_KEYS, "Instance response")
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc
    if _int(decoded["schema_version"], "response.schema_version") != SCHEMA_VERSION:
        raise _compatibility("unsupported schema version")
    transport = _int(decoded["transport_version"], "response.transport_version")
    if transport != TRANSPORT_VERSION:
        raise _compatibility("unsupported transport version")
    if decoded["engine_interface_version"] != ENGINE_INTERFACE_VERSION:
        raise _compatibility("unsupported Engine Interface version")
    view = _parse_view(decoded["view"])
    recovery = _parse_recovery(decoded["recovery"])
    invocation_value = decoded["invocation"]
    observation_value = decoded["observation"]
    if invocation_value is None or observation_value is None:
        if invocation_value is not None or observation_value is not None:
            raise _protocol("invocation and observation must both be null or present")
        return view, {}, recovery
    invocation = _parse_invocation(invocation_value)
    observation = _parse_observation(
        observation_value,
        cast(str, invocation["invocation_id"]),
    )
    if tuple(cast(Sequence[object], observation["source_lineage"])) != tuple(
        cast(Sequence[object], invocation["source_refs"])
    ):
        raise _protocol("observation source lineage does not match invocation")
    if (
        observation["output_ref"] != view["output_ref"]
        or observation["output_digest"] != view["output_digest"]
    ):
        raise _protocol("view and observation output binding mismatch")
    return view, {"invocation": invocation, "observation": observation}, recovery


def _safe_relative_path(path: str) -> str:
    if not isinstance(path, str):
        raise InstanceProtocolError("path must be a string")
    try:
        encoded = path.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise InstanceProtocolError("path is not valid UTF-8") from exc
    if not encoded or len(encoded) > MAX_PATH_BYTES or "\x00" in path:
        raise InstanceProtocolError("path violates the bound")
    if os.path.isabs(path) or any(ord(char) < 0x20 for char in path):
        raise InstanceProtocolError("path must be a rooted relative path")
    normalized = os.path.normpath(path).replace(os.sep, "/")
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise InstanceProtocolError("path escapes project root")
    return normalized


def _failure_from_view(observation: Mapping[str, object]) -> Optional[ContextFailure]:
    failure = observation.get("failure")
    return failure if isinstance(failure, ContextFailure) else None


_REAP_SECONDS = 5.0
# Session containment (POSIX): the request to the instance binary and the line
# its session starter answers with, before the instance itself is started.
_SESSION_CONTAINMENT_ENV = "LEAN_CTX_SESSION_CONTAINMENT"
_SESSION_CONTAINMENT_ACK = b'{"leanctx_session_containment":1}\n'


def _kill_and_reap(process: "subprocess.Popen[bytes]") -> None:
    """Ends a one-shot instance process and reaps it within a budget.

    Never waits without a bound. An instance that is still running when the
    budget ends is a cleanup failure and is raised as one: it replaces the
    error that led here, with that error kept as its context.

    Ownership rule: on POSIX the instance leads its own process group, and that
    group number is this client's own only while the leader is unreaped —
    running or exited. `returncode` is set only by a reap through this Popen
    object, so while it is None the group may be signalled, which also ends
    the children an exited instance left behind. `poll()` is not used to decide,
    because it reaps. After the reap nothing is signalled.
    """
    if process.returncode is not None:
        return
    if os.name != "nt":
        for _ in range(4):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                # Gone, or (Darwin) only the exited, unreaped leader is left.
                break
            # Again, briefly: a member caught in the middle of a fork can
            # leave a child the first signal did not reach.
            time.sleep(0.002)
    # Popen.kill reaps an exited leader itself and signals only a live one.
    process.kill()
    try:
        process.wait(timeout=_REAP_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise InstanceExecutionError(
            "Instance process could not be reaped within its budget"
        ) from exc


class SubprocessInstanceClient:
    """Invoke only the named local instance CLI with bounded process I/O."""

    def __init__(
        self,
        instance_binary: Optional[Union[str, os.PathLike]] = None,
        *,
        timeout: float = 30.0,
        engine_binary: Optional[Union[str, os.PathLike]] = None,
    ):
        binary = _deprecation.renamed_kwarg(
            "engine_binary", engine_binary, "instance_binary", instance_binary, None
        )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0.1 <= timeout <= 120.0
        ):
            raise ConfigurationError("timeout must be between 0.1 and 120 seconds")
        self.instance_binary = (
            os.fspath(binary) if binary is not None else "lean-ctx"
        )
        self.timeout = float(timeout)
        #: How the process tree of the most recent completed instance run was
        #: contained: ``"engine-starter"`` (the started process was the
        #: instance's session starter, which ends the instance's process group and
        #: exits only afterwards) or ``"unavailable"`` (the instance binary has
        #: no such mode, or the platform is Windows: reduced containment). A
        #: run ended early by a deadline or an output bound does not change
        #: it; with concurrent calls it is the state of whichever ended last.
        self.last_session_containment = "unavailable"

    @property
    def engine_binary(self) -> str:
        """Deprecated since 1.3.0, removed in 2.0: use ``instance_binary``."""
        _deprecation.warn_renamed("engine_binary", "instance_binary")
        return self.instance_binary

    @engine_binary.setter
    def engine_binary(self, value: str) -> None:
        _deprecation.warn_renamed("engine_binary", "instance_binary")
        self.instance_binary = value

    def context_plan(
        self, project_root: str, request: InstancePlanningRequest
    ) -> Mapping[str, object]:
        """Return the canonical local plan, not an execution receipt or admission."""
        if not isinstance(request, InstancePlanningRequest):
            raise ValidationError("context_plan requires InstancePlanningRequest")
        raw = self._invoke_bytes(
            "context-plan", project_root, request.to_dict(), stdin=True
        )
        return parse_context_plan(raw, request)

    def context_plan_sources(
        self,
        project_root: str,
        request: InstancePlanningRequest,
        sources: Sequence[InstanceSource],
    ) -> Mapping[str, object]:
        """Plan explicit bounded sources; permission labels never authenticate a caller."""
        if not isinstance(request, InstancePlanningRequest):
            raise ValidationError("context_plan_sources requires InstancePlanningRequest")
        if not isinstance(sources, (list, tuple)) or len(sources) > 64:
            raise ValidationError("sources must be a bounded list or tuple")
        if any(not isinstance(source, InstanceSource) for source in sources):
            raise ValidationError("sources must contain InstanceSource values")
        descriptors = [source.to_dict() for source in sources]
        references = [source.descriptor["object_ref"] for source in sources]
        if len(set(references)) != len(references):
            raise ValidationError("source object references must be unique")
        raw = self._invoke_bytes(
            "context-plan-sources",
            project_root,
            {"planning": request.to_dict(), "sources": descriptors},
            maximum=1024 * 1024,
            stdin=True,
        )
        return parse_source_plan(raw, request, sources)

    def context_view(self, plan: ContextPlan) -> ContextView:
        if not isinstance(plan, ContextPlan):
            raise ValidationError("context_view requires ContextPlan")
        source = plan.source
        request = {
            "schema_version": SCHEMA_VERSION,
            "transport_version": TRANSPORT_VERSION,
            "engine_interface_version": ENGINE_INTERFACE_VERSION,
            "path": source.relative_path,
            "mode": plan.mode,
        }
        project_root = source.project_root
        if project_root is None:
            raise SourceUnavailableError("source project_root is unavailable")
        view_data, records, recovery = self._invoke(
            "context-view", project_root, request
        )
        if not records:
            raise _protocol("context-view response omitted invocation/observation")
        invocation = records["invocation"]
        if recovery["source_ref"] not in invocation["source_refs"]:
            raise _protocol("recovery source_ref is not admitted by invocation")
        if (
            source.source_ref is not None
            and source.source_ref != recovery["source_ref"]
        ):
            raise _protocol("Instance source_ref differs from requested binding")
        if (
            source.source_digest is not None
            and source.source_digest != recovery["source_digest"]
        ):
            raise _protocol("Instance source_digest differs from requested binding")
        result = self._build_view(source, view_data, records, recovery)
        status = result.status
        if status == InstanceStatus.REJECTED.value:
            failure = result.failure
            code = failure.code if failure is not None else None
            detail = code.value if isinstance(code, FailureCode) else "rejected"
            if code == FailureCode.POLICY_REJECTED:
                raise PolicyAdmissionError(
                    f"Instance rejected request: {detail}", failure=failure, view=result
                )
            if code == FailureCode.SOURCE_UNAVAILABLE:
                raise SourceUnavailableError(
                    f"Instance rejected request: {detail}", failure=failure, view=result
                )
            raise InstanceRejected(f"Instance rejected request: {detail}")
        if status == InstanceStatus.FAILED.value:
            failure = result.failure
            code = failure.code if failure is not None else None
            detail = code.value if isinstance(code, FailureCode) else "failed"
            error_type = InstanceExecutionError
            if code == FailureCode.SOURCE_UNAVAILABLE:
                error_type = SourceUnavailableError
            elif code == FailureCode.SOURCE_INTEGRITY_MISMATCH:
                error_type = ArtifactIntegrityError
            elif code == FailureCode.UNSUPPORTED_OPERATION:
                raise UnsupportedInstanceError(f"Instance execution failed: {detail}")
            raise error_type(
                f"Instance execution failed: {detail}",
                failure=failure,
                view=result,
            )
        return result

    def recover(
        self,
        project_root: str,
        path: str,
        recovery_ref: str,
        source_ref: str,
        source_digest: str,
    ) -> RecoveredSource:
        root = self._validate_root(project_root)
        relative_path = _safe_relative_path(path)
        recovery_ref = _required_ref(recovery_ref, "recovery_ref")
        source_ref = _required_ref(source_ref, "source_ref")
        try:
            source_digest = validate_digest(source_digest, "source_digest")
        except ValidationError as exc:
            raise _protocol(str(exc)) from exc
        request = {
            "schema_version": SCHEMA_VERSION,
            "transport_version": TRANSPORT_VERSION,
            "engine_interface_version": ENGINE_INTERFACE_VERSION,
            "path": relative_path,
            "recovery_ref": recovery_ref,
            "source_ref": source_ref,
            "source_digest": source_digest,
        }
        view_data, records, recovery = self._invoke("recover", root, request)
        if records:
            raise _protocol("recover response must have null invocation/observation")
        if recovery != {
            "recovery_ref": recovery_ref,
            "source_ref": source_ref,
            "source_digest": source_digest,
        }:
            raise ArtifactIntegrityError("recover response binding mismatch")
        if view_data["output_digest"] != source_digest:
            raise ArtifactIntegrityError(
                "recover output digest does not match source digest"
            )
        expected_output_ref = "output:" + source_digest.removeprefix("sha256:")
        if view_data["output_ref"] not in (None, expected_output_ref):
            raise ArtifactIntegrityError(
                "recover output reference does not match source digest"
            )
        try:
            return RecoveredSource(
                view_data["text"], source_ref, source_digest, recovery_ref
            )
        except ValidationError as exc:
            raise _protocol(str(exc)) from exc

    def _validate_root(self, project_root: str) -> str:
        if not isinstance(project_root, str) or not project_root:
            raise SourceUnavailableError("project_root is invalid")
        if "\x00" in project_root:
            raise SourceUnavailableError("project_root contains NUL")
        root = os.path.abspath(os.path.normpath(project_root))
        if len(root.encode("utf-8")) > MAX_PATH_BYTES or not os.path.isdir(root):
            raise SourceUnavailableError("project_root is unavailable")
        return root

    def _resolve_binary(self) -> str:
        candidate = self.instance_binary
        if os.path.sep not in candidate:
            candidate = shutil.which(candidate) or candidate
        else:
            candidate = os.path.abspath(candidate)
        if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            raise InstanceUnavailable("configured instance binary is unavailable")
        return candidate

    def _invoke(self, operation: str, project_root: str, request: Mapping[str, object]):
        return _parse_response(self._invoke_bytes(operation, project_root, request))

    def _invoke_bytes(
        self,
        operation: str,
        project_root: str,
        request: Mapping[str, object],
        *,
        maximum: int = MAX_REQUEST_BYTES,
        stdin: bool = False,
    ) -> bytes:
        root = self._validate_root(project_root)
        payload = canonical_bytes(request)
        if len(payload) > maximum:
            raise InstanceProtocolError("Instance request exceeds the bound")
        if stdin:
            return self._run(operation, root, "-", input_bytes=payload)
        request_path = None
        try:
            fd, request_path = tempfile.mkstemp(
                prefix=".leanctx-sdk-", suffix=".json", dir=root
            )
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            raw = self._run(operation, root, request_path)
        finally:
            if request_path is not None:
                try:
                    os.unlink(request_path)
                except FileNotFoundError:
                    pass
        return raw

    def _run(
        self,
        operation: str,
        project_root: str,
        request_path: str,
        *,
        input_bytes: Optional[bytes] = None,
    ) -> bytes:
        binary = self._resolve_binary()
        argv = [
            binary,
            "engine",
            operation,
            "--project-root",
            project_root,
            "--json-file",
            request_path,
        ]
        env = {
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
        }
        # Ask the instance to contain its session: on POSIX it runs as its own
        # session starter, on Windows it joins a kill-on-close job. An instance
        # without the mode ignores the variable and acknowledges nothing.
        env[_SESSION_CONTAINMENT_ENV] = "1"
        try:
            process = subprocess.Popen(
                argv,
                cwd=project_root,
                env=env,
                stdin=subprocess.PIPE
                if input_bytes is not None
                else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                # Its own session and process group, like the agent's instance
                # and the other language clients: the tree can then be ended
                # as a group, including what an exited instance left behind.
                start_new_session=os.name != "nt",
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise InstanceUnavailable("Instance process could not be started") from exc
        stdout = bytearray()
        stderr = bytearray()
        selector: Optional[selectors.BaseSelector] = None
        input_offset = 0
        deadline = time.monotonic() + self.timeout
        try:
            selector = selectors.DefaultSelector()
            if process.stdout is None or process.stderr is None:
                raise InstanceProtocolError("Instance output pipe is unavailable")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _kill_and_reap(process)
                    raise InstanceTimeout("Instance process exceeded its deadline")
                events = selector.select(remaining)
                if not events:
                    _kill_and_reap(process)
                    raise InstanceTimeout("Instance process exceeded its deadline")
                for key, _ in events:
                    if key.data == "stdin":
                        if process.stdin is None or input_bytes is None:
                            raise InstanceProtocolError(
                                "Instance input pipe is unavailable"
                            )
                        try:
                            # Never let pipe backpressure bypass the shared deadline.
                            written = os.write(
                                process.stdin.fileno(),
                                input_bytes[input_offset : input_offset + 512],
                            )
                            input_offset += written
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            input_offset = len(input_bytes)
                        if input_offset == len(input_bytes):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                        continue
                    fileobj = key.fileobj
                    descriptor = (
                        fileobj if isinstance(fileobj, int) else fileobj.fileno()
                    )
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = stdout if key.data == "stdout" else stderr
                    maximum = (
                        MAX_RESPONSE_BYTES if key.data == "stdout" else MAX_STDERR_BYTES
                    )
                    target.extend(chunk)
                    if len(target) > maximum:
                        _kill_and_reap(process)
                        raise InstanceProtocolError(
                            "Instance process output exceeds its bound"
                        )
            try:
                return_code = process.wait(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            except subprocess.TimeoutExpired as exc:
                raise InstanceTimeout("Instance process exceeded its deadline") from exc
        except (InstanceTimeout, InstanceProtocolError):
            _kill_and_reap(process)
            raise
        except OSError as exc:
            raise InstanceExecutionError("Instance process I/O failed") from exc
        finally:
            # Last resort for every other exit path. Bounded like the others,
            # but silent: an exception is already on its way.
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=_REAP_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            if selector is not None:
                selector.close()
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        # The session starter's acknowledgement precedes everything the instance
        # wrote. It is recorded and is not output.
        if stdout.startswith(_SESSION_CONTAINMENT_ACK):
            del stdout[: len(_SESSION_CONTAINMENT_ACK)]
            self.last_session_containment = "engine-starter"
        else:
            self.last_session_containment = "unavailable"
        if return_code != 0:
            code = self._stderr_code(bytes(stderr))
            if code in {
                "unsafe_root",
                "source_outside_root",
                "source_symlink",
                "policy_rejected",
            }:
                raise PolicyAdmissionError(f"Instance rejected request: {code}")
            if code == "source_unavailable":
                raise SourceUnavailableError("Instance source is unavailable")
            if code == "unsupported_mode":
                raise UnsupportedInstanceError("Instance operation is unsupported")
            raise InstanceExecutionError(
                f"Instance process failed: {code or 'nonzero_exit'}"
            )
        if not stdout:
            raise InstanceProtocolError("Instance returned empty stdout")
        try:
            bytes(stdout).decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise InstanceProtocolError("Instance returned invalid UTF-8") from exc
        return bytes(stdout)

    @staticmethod
    def _stderr_code(stderr: bytes) -> Optional[str]:
        try:
            text = stderr.decode("utf-8", "replace")
        except Exception:
            return None
        match = re.search(r"(?:^|\n)engine:\s*([a-z0-9_]+)", text)
        return match.group(1) if match else None

    def _build_view(
        self,
        source: ContextSource,
        view_data: Mapping[str, object],
        records: Mapping[str, object],
        recovery: Mapping[str, str],
    ) -> ContextView:
        invocation = records["invocation"]
        observation = records["observation"]
        if not isinstance(invocation, Mapping) or not isinstance(observation, Mapping):
            raise _protocol("Instance records are malformed")
        if recovery["source_ref"] not in invocation["source_refs"]:
            raise _protocol("recovery source_ref is not in invocation lineage")
        if tuple(observation["source_lineage"]) != tuple(invocation["source_refs"]):
            raise _protocol("observation lineage mismatch")
        try:
            result = ContextView(
                source=source,
                text=cast(Optional[str], view_data["text"]),
                output_ref=cast(Optional[str], view_data["output_ref"]),
                output_digest=cast(Optional[str], view_data["output_digest"]),
                source_ref=recovery["source_ref"],
                source_digest=recovery["source_digest"],
                recovery_ref=recovery["recovery_ref"],
                status=observation["status"],
                measurements=observation["measurements"],
                failure=observation["failure"],
                receipt_link=observation["receipt_link"],
                invocation=invocation,
                observation=observation,
            )
        except ValidationError as exc:
            raise _protocol(str(exc)) from exc
        if result.status == "succeeded" and not result.verify():
            raise ArtifactIntegrityError("succeeded instance evidence is not sealed")
        return result


from . import _deprecation  # noqa: E402

# Engine names are deprecated aliases of the same objects until SDK 2.0.
__getattr__ = _deprecation.module_getattr(
    __name__,
    globals(),
    {
        "EngineClient",
        "EnginePlanningClient",
        "SubprocessEngineClient",
    },
)
