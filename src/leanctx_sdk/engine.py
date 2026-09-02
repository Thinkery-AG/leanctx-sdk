"""Strict subprocess client for the two public Engine Interface v1 operations."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple, Union, cast

from .errors import (
    ArtifactIntegrityError,
    CompatibilityError,
    ConfigurationError,
    EngineExecutionError,
    EngineProtocolError,
    EngineRejected,
    EngineTimeout,
    EngineUnavailable,
    PolicyAdmissionError,
    SourceUnavailableError,
    UnsupportedEngineError,
    ValidationError,
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
    EngineStatus,
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


class EngineClient(Protocol):
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


def _protocol(message: str) -> EngineProtocolError:
    return EngineProtocolError(message)


def _compatibility(message: str) -> CompatibilityError:
    return CompatibilityError(message)


def _unsupported_engine(message: str) -> UnsupportedEngineError:
    return UnsupportedEngineError(message)


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
        raise _protocol("unknown Engine failure code")
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
        raise _unsupported_engine("unsupported Engine identity")
    if int(engine_version.split(".", 1)[0]) != 3:
        raise _unsupported_engine("unsupported Engine major version")
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
        raise _unsupported_engine("unsupported Engine capability")
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
    if status not in {item.value for item in EngineStatus}:
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
        raise _protocol("Engine response exceeds the bound")
    try:
        decoded = strict_json_loads(raw, label="Engine response")
    except ValidationError as exc:
        raise _protocol(str(exc)) from exc
    try:
        exact_keys(decoded, _TOP_LEVEL_KEYS, "Engine response")
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
        raise EngineProtocolError("path must be a string")
    try:
        encoded = path.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise EngineProtocolError("path is not valid UTF-8") from exc
    if not encoded or len(encoded) > MAX_PATH_BYTES or "\x00" in path:
        raise EngineProtocolError("path violates the bound")
    if os.path.isabs(path) or any(ord(char) < 0x20 for char in path):
        raise EngineProtocolError("path must be a rooted relative path")
    normalized = os.path.normpath(path).replace(os.sep, "/")
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise EngineProtocolError("path escapes project root")
    return normalized


def _failure_from_view(observation: Mapping[str, object]) -> Optional[ContextFailure]:
    failure = observation.get("failure")
    return failure if isinstance(failure, ContextFailure) else None


class SubprocessEngineClient:
    """Invoke only the named local Engine CLI with bounded process I/O."""

    def __init__(
        self,
        engine_binary: Optional[Union[str, os.PathLike]] = None,
        *,
        timeout: float = 30.0,
    ):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0.1 <= timeout <= 120.0
        ):
            raise ConfigurationError("timeout must be between 0.1 and 120 seconds")
        self.engine_binary = (
            os.fspath(engine_binary) if engine_binary is not None else "lean-ctx"
        )
        self.timeout = float(timeout)

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
            raise _protocol("Engine source_ref differs from requested binding")
        if (
            source.source_digest is not None
            and source.source_digest != recovery["source_digest"]
        ):
            raise _protocol("Engine source_digest differs from requested binding")
        result = self._build_view(source, view_data, records, recovery)
        status = result.status
        if status == EngineStatus.REJECTED.value:
            failure = result.failure
            code = failure.code if failure is not None else None
            detail = code.value if isinstance(code, FailureCode) else "rejected"
            if code == FailureCode.POLICY_REJECTED:
                raise PolicyAdmissionError(
                    f"Engine rejected request: {detail}", failure=failure, view=result
                )
            if code == FailureCode.SOURCE_UNAVAILABLE:
                raise SourceUnavailableError(
                    f"Engine rejected request: {detail}", failure=failure, view=result
                )
            raise EngineRejected(f"Engine rejected request: {detail}")
        if status == EngineStatus.FAILED.value:
            failure = result.failure
            code = failure.code if failure is not None else None
            detail = code.value if isinstance(code, FailureCode) else "failed"
            error_type = EngineExecutionError
            if code == FailureCode.SOURCE_UNAVAILABLE:
                error_type = SourceUnavailableError
            elif code == FailureCode.SOURCE_INTEGRITY_MISMATCH:
                error_type = ArtifactIntegrityError
            elif code == FailureCode.UNSUPPORTED_OPERATION:
                raise UnsupportedEngineError(f"Engine execution failed: {detail}")
            raise error_type(
                f"Engine execution failed: {detail}",
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
        candidate = self.engine_binary
        if os.path.sep not in candidate:
            candidate = shutil.which(candidate) or candidate
        else:
            candidate = os.path.abspath(candidate)
        if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            raise EngineUnavailable("configured Engine binary is unavailable")
        return candidate

    def _invoke(self, operation: str, project_root: str, request: Mapping[str, object]):
        root = self._validate_root(project_root)
        payload = canonical_bytes(request)
        if len(payload) > MAX_REQUEST_BYTES:
            raise EngineProtocolError("Engine request exceeds the bound")
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
        return _parse_response(raw)

    def _run(self, operation: str, project_root: str, request_path: str) -> bytes:
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
        try:
            process = subprocess.Popen(
                argv,
                cwd=project_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise EngineUnavailable("Engine process could not be started") from exc
        stdout = bytearray()
        stderr = bytearray()
        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        assert process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + self.timeout
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise EngineTimeout("Engine process exceeded its deadline")
                events = selector.select(remaining)
                if not events:
                    process.kill()
                    process.wait()
                    raise EngineTimeout("Engine process exceeded its deadline")
                for key, _ in events:
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
                        process.kill()
                        process.wait()
                        raise EngineProtocolError(
                            "Engine process output exceeds its bound"
                        )
            return_code = process.wait()
        except (EngineTimeout, EngineProtocolError):
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            selector.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        if return_code != 0:
            code = self._stderr_code(bytes(stderr))
            if code in {
                "unsafe_root",
                "source_outside_root",
                "source_symlink",
                "policy_rejected",
            }:
                raise PolicyAdmissionError(f"Engine rejected request: {code}")
            if code == "source_unavailable":
                raise SourceUnavailableError("Engine source is unavailable")
            if code == "unsupported_mode":
                raise UnsupportedEngineError("Engine operation is unsupported")
            raise EngineExecutionError(
                f"Engine process failed: {code or 'nonzero_exit'}"
            )
        if not stdout:
            raise EngineProtocolError("Engine returned empty stdout")
        try:
            bytes(stdout).decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise EngineProtocolError("Engine returned invalid UTF-8") from exc
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
            raise _protocol("Engine records are malformed")
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
            raise ArtifactIntegrityError("succeeded Engine evidence is not sealed")
        return result
