# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Bounded HTTP adapters for canonical Enterprise Engine operations.

The Enterprise endpoint authenticates and governs source identifiers, then
delegates planning to the public Engine protocol.  The separate context-read
adapter invokes the guarded Engine v1 tool and validates the returned canonical
receipt reference; neither adapter manufactures receipt artifacts.

The deadline covers connected socket I/O, including trickled response headers
and bodies.  Python's standard-library socket setup performs DNS resolution in
the connecting thread, and that resolver call is not cancellable by a Python
timer; callers should account for that limitation when choosing a deadline.
"""

from __future__ import annotations

import http.client
import ipaddress
import math
import socket
import threading
import time
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Mapping, NoReturn, Optional, Tuple, Union, cast
from urllib.parse import urlsplit

from .errors import (
    ConfigurationError,
    EngineProtocolError,
    EngineRejected,
    EngineTimeout,
    EngineUnavailable,
    PolicyAdmissionError,
    ValidationError,
)
from .planning import (
    ENGINE_INTERFACE_VERSION,
    MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES,
    MAX_ENGINE_SOURCE_PLAN_SOURCES,
    EnginePlanningRequest,
    _timestamp,
    parse_source_plan,
)
from .protocol import (
    MAX_PATH_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_TEXT_BYTES,
    canonical_bytes,
    sha256_digest,
    strict_json_loads,
    validate_digest,
    validate_ref,
)
from .source_execution import (
    _MAX_ENGINE_SOURCE_EXECUTION_V2_RESPONSE_BYTES,
    _MAX_ENGINE_SOURCE_EXECUTION_V2_WRAPPER_BYTES,
    _MAX_ENGINE_OUTCOME_REQUEST_BYTES,
    _MAX_ENGINE_OUTCOME_RESPONSE_BYTES,
    parse_engine_outcome_response,
    parse_source_execution_response,
    parse_source_execution_v2_response,
    validate_outcome_request,
    validate_execution_request,
)
from .provider_execution import (
    _MAX_PROVIDER_REQUEST_BYTES,
    _MAX_PROVIDER_RESPONSE_BYTES,
    parse_provider_execution_response,
    validate_provider_request,
)


_ENGINE_PATH = "/v1/engine/context-plan"
_MATERIALIZATION_PATH = "/v1/engine/context-materialize"
_EXECUTION_PATH = "/v1/engine/context-execute"
_EXECUTION_V2_PATH = "/v2/engine/context-execute"
_OUTCOME_PATH = "/v1/engine/context-outcome"
_PROVIDER_EXECUTION_PATH = "/v1/engine/provider-execute"
_CONTEXT_READ_PATH = "/v1/tools/call"
_SCHEMA_VERSION = 1
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_MATERIALIZED_CONTENT_BYTES = 1024 * 1024
_MAX_MATERIALIZATION_RESPONSE_BYTES = _MAX_RESPONSE_BYTES + _MAX_MATERIALIZED_CONTENT_BYTES
# Source execution carries a bounded source plan and a bounded Engine view;
# retain one additional protocol-response allowance for both envelopes.
_MAX_EXECUTION_RESPONSE_BYTES = (
    _MAX_RESPONSE_BYTES + _MAX_MATERIALIZED_CONTENT_BYTES + _MAX_RESPONSE_BYTES
)
_MAX_EXECUTION_V2_RESPONSE_BYTES = (
    _MAX_ENGINE_SOURCE_EXECUTION_V2_RESPONSE_BYTES
    + _MAX_ENGINE_SOURCE_EXECUTION_V2_WRAPPER_BYTES
)
_MAX_CREDENTIAL_BYTES = 4096
_MAX_URL_BYTES = 4096
_MAX_TIMEOUT_SECONDS = 120.0
_MAX_U64 = (1 << 64) - 1
_REMOTE_RESPONSE_KEYS = {
    "schema_version",
    "tenant_id",
    "governance_revision",
    "plan",
}

_Connection = Union[http.client.HTTPConnection, http.client.HTTPSConnection]


def _configuration_error(
    message: str, cause: Optional[BaseException] = None
) -> NoReturn:
    if cause is None:
        raise ConfigurationError(message)
    raise ConfigurationError(message) from cause


def _validation_error(message: str, cause: Optional[BaseException] = None) -> NoReturn:
    if cause is None:
        raise ValidationError(message)
    raise ValidationError(message) from cause


def _protocol_error(message: str, cause: Optional[BaseException] = None) -> NoReturn:
    if cause is None:
        raise EngineProtocolError(message)
    raise EngineProtocolError(message) from cause


def _remaining(deadline: float, operation: str = "Enterprise Engine") -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EngineTimeout(f"{operation} request exceeded its deadline")
    return remaining


def _canonical_uuid(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _validation_error(f"{field_name} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError, TypeError) as exc:
        _validation_error(f"{field_name} must be a canonical UUID", exc)
    if parsed.int == 0 or str(parsed) != value.lower():
        _validation_error(f"{field_name} must be a non-nil canonical UUID")
    return str(parsed)


def _configured_uuid(value: Any, field_name: str) -> str:
    try:
        return _canonical_uuid(value, field_name)
    except ValidationError as exc:
        _configuration_error(f"{field_name} must be a non-nil UUID", exc)


def _validate_credential(value: Any) -> str:
    if not isinstance(value, str):
        _configuration_error("credential must be a non-empty visible ASCII string")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        _configuration_error("credential must be a non-empty visible ASCII string", exc)
    if not 0 < len(encoded) <= _MAX_CREDENTIAL_BYTES or any(
        not 0x21 <= ord(character) <= 0x7E for character in value
    ):
        _configuration_error("credential must be a non-empty visible ASCII string")
    return value


def _validate_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _configuration_error("timeout must be between 0.1 and 120 seconds")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        _configuration_error("timeout must be between 0.1 and 120 seconds", exc)
    if not math.isfinite(numeric) or not 0.1 <= numeric <= _MAX_TIMEOUT_SECONDS:
        _configuration_error("timeout must be between 0.1 and 120 seconds")
    return numeric


def _validate_base_url(value: Any, allow_loopback_http: bool) -> Tuple[str, str, int]:
    if not isinstance(value, str) or not value:
        _configuration_error("base_url must be a bounded absolute URL")
    try:
        url_bytes = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        _configuration_error("base_url must be valid UTF-8", exc)
    if len(url_bytes) > _MAX_URL_BYTES:
        _configuration_error("base_url must be a bounded absolute URL")
    if any(ord(character) < 0x21 for character in value):
        _configuration_error("base_url must not contain whitespace or controls")
    try:
        parsed = urlsplit(value)
        username = parsed.username
        password = parsed.password
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as exc:
        _configuration_error("base_url is not a valid URL", exc)
    if username is not None or password is not None:
        _configuration_error("base_url must not contain userinfo")
    if (
        "?" in value
        or "#" in value
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        _configuration_error("base_url may contain only an optional root path")
    if host is None or not host:
        _configuration_error("base_url must contain a host")
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"}:
        _configuration_error("base_url must use HTTPS")
    if scheme == "http":
        if not allow_loopback_http:
            _configuration_error("HTTP is only allowed for explicit loopback testing")
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            _configuration_error("loopback HTTP requires a literal loopback IP", exc)
        if not address.is_loopback:
            _configuration_error("loopback HTTP requires a literal loopback IP")
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 1 <= port <= 65535:
        _configuration_error("base_url port is outside its bounds")
    return scheme, host, port


def _source_ids(value: Any) -> Tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _validation_error("source_ids must be a bounded sequence of UUID strings")
    if len(value) > MAX_ENGINE_SOURCE_PLAN_SOURCES:
        _validation_error("source_ids exceeds the Engine source bound")
    normalized = tuple(_canonical_uuid(item, "source_id") for item in value)
    if len(set(normalized)) != len(normalized):
        _validation_error("source_ids must not contain duplicates")
    return normalized


def _validate_u64(value: Any, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_U64
    ):
        _protocol_error(f"{field_name} must be an unsigned 64-bit integer")
    return value


def _validate_input_u64(value: Any, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_U64
    ):
        _validation_error(f"{field_name} must be an unsigned 64-bit integer")
    return value


def _validate_source_scope(
    plan: Mapping[str, object], source_ids: Tuple[str, ...]
) -> None:
    requested = set(source_ids)
    plan_value = cast(Mapping[str, Any], plan)
    result = cast(Mapping[str, Any], plan_value["result"])
    result_plan = cast(Mapping[str, Any], result["plan"])
    selections = cast(Sequence[Mapping[str, Any]], result_plan["selections"])
    for selection in selections:
        if (
            selection["source_ref"] not in requested
            or selection["provider"] not in requested
        ):
            _protocol_error("Enterprise Engine selection is outside requested sources")
    bindings = cast(Sequence[Mapping[str, Any]], plan_value["source_bindings"])
    for binding in bindings:
        if (
            binding["object_ref"] not in requested
            or binding["source_id"] not in requested
        ):
            _protocol_error("Enterprise Engine source binding is outside requested sources")
        if binding["permission"] != "permitted":
            _protocol_error("Enterprise Engine selected source is not permitted")


def _validate_materialized_content(value: Any) -> tuple[str, bytes]:
    if not isinstance(value, str):
        _protocol_error("Enterprise Engine materialized content is not a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        _protocol_error("Enterprise Engine materialized content is not valid UTF-8", exc)
    if len(encoded) > _MAX_MATERIALIZED_CONTENT_BYTES:
        _protocol_error("Enterprise Engine materialized content exceeds its byte bound")
    return value, encoded


def _interrupt(connection: _Connection, connected_socket: socket.socket) -> None:
    try:
        connected_socket.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        connected_socket.close()
    except OSError:
        pass
    try:
        connection.close()
    except OSError:
        pass


def _validate_context_path(value: Any) -> str:
    if not isinstance(value, str):
        _validation_error("context-read path must be a non-empty string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        _validation_error("context-read path must be valid UTF-8", exc)
    if not encoded or len(encoded) > MAX_PATH_BYTES:
        _validation_error("context-read path exceeds its byte bound")
    if any(unicodedata.category(character) == "Cc" for character in value):
        _validation_error("context-read path contains a control character")
    return value


def _validate_context_text(value: Any) -> str:
    if not isinstance(value, str):
        _protocol_error("context-read response text is not a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        _protocol_error("context-read response text is not valid UTF-8", exc)
    if len(encoded) > MAX_TEXT_BYTES:
        _protocol_error("context-read response text exceeds its byte bound")
    return value


@dataclass(frozen=True)
class EngineContextReadResult:
    """Guarded context text plus the host's canonical receipt metadata."""

    text: str
    canonical_receipt: Mapping[str, object]
    raw_response: Mapping[str, object]


def _parse_context_receipt(value: Any) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _protocol_error("context-read response canonical_receipt is not an object")
    required = (
        "schema_version",
        "receipt_id",
        "receipt_ref",
        "receipt_digest",
        "outcome",
        "delivery",
    )
    allowed = set(required) | {"context_decision_ref", "outcome_observation_ref"}
    if set(value) - allowed:
        _protocol_error("context-read canonical receipt fields are unsupported")
    for key in required:
        if key not in value:
            _protocol_error(f"context-read canonical receipt is missing {key}")
    schema_version = value["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != _SCHEMA_VERSION
    ):
        _protocol_error("context-read canonical receipt schema_version is unsupported")
    try:
        validate_ref(value["receipt_id"], "receipt_id")
        receipt_digest = validate_digest(value["receipt_digest"], "receipt_digest")
        receipt_ref = validate_ref(value["receipt_ref"], "receipt_ref")
    except (KeyError, ValidationError) as exc:
        _protocol_error("context-read canonical receipt identity is invalid", exc)
    if receipt_ref != f"id:{receipt_digest}":
        _protocol_error("context-read canonical receipt reference is not digest-bound")
    if value["outcome"] != "unknown":
        _protocol_error("context-read canonical receipt outcome is unsupported")
    if value["delivery"] != "native_engine_view":
        _protocol_error("context-read canonical receipt delivery is unsupported")
    for key in ("context_decision_ref", "outcome_observation_ref"):
        if key in value:
            try:
                validate_ref(value[key], key)
            except ValidationError as exc:
                _protocol_error(f"context-read canonical receipt {key} is invalid", exc)
    return cast(Mapping[str, object], value)


def _parse_context_read_response(raw: bytes) -> EngineContextReadResult:
    try:
        value = strict_json_loads(raw, label="Engine context-read response")
    except ValidationError as exc:
        _protocol_error("Engine context-read response is not valid JSON", exc)
    if not isinstance(value, Mapping) or set(value) != {"result"}:
        _protocol_error("Engine context-read response wrapper is invalid")
    result = value["result"]
    if not isinstance(result, Mapping):
        _protocol_error("Engine context-read result is not an object")
    is_error = result.get("isError")
    if is_error is not None and not isinstance(is_error, bool):
        _protocol_error("Engine context-read isError must be boolean")
    if is_error is True:
        _protocol_error("Engine context-read returned an error result")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1:
        _protocol_error("Engine context-read content must contain one item")
    content_item = content[0]
    if not isinstance(content_item, Mapping) or content_item.get("type") != "text":
        _protocol_error("Engine context-read content must be text")
    text = _validate_context_text(content_item.get("text"))
    metadata = result.get("_meta")
    if not isinstance(metadata, Mapping):
        _protocol_error("Engine context-read metadata is missing")
    receipt = _parse_context_receipt(metadata.get("canonical_receipt"))
    return EngineContextReadResult(
        text=text,
        canonical_receipt=receipt,
        raw_response=cast(Mapping[str, object], value),
    )


class _AuthenticatedEngineTransport:
    """Shared bounded authenticated HTTP path for Enterprise operations."""

    def __init__(
        self,
        base_url: str,
        credential: str,
        *,
        timeout: float = 30.0,
        allow_loopback_http: bool = False,
    ) -> None:
        if not isinstance(allow_loopback_http, bool):
            _configuration_error("allow_loopback_http must be a boolean")
        self._scheme, self._host, self._port = _validate_base_url(
            base_url, allow_loopback_http
        )
        self._credential = _validate_credential(credential)
        self._timeout = _validate_timeout(timeout)
        self.base_url = base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    def _new_connection(self, timeout: float) -> _Connection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(self._host, self._port, timeout=timeout)
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout)

    def _post(self, payload: bytes) -> bytes:
        return self._post_json(
            _ENGINE_PATH,
            payload,
            _MAX_RESPONSE_BYTES,
            "Enterprise Engine",
            "planning request",
        )

    def _post_json(
        self,
        path: str,
        payload: bytes,
        max_response_bytes: int,
        operation: str,
        rejection: str,
    ) -> bytes:
        deadline = time.monotonic() + self._timeout
        connection: Optional[_Connection] = None
        timer: Optional[threading.Timer] = None
        response: Optional[http.client.HTTPResponse] = None
        deadline_fired = threading.Event()
        try:
            active_connection = self._new_connection(_remaining(deadline, operation))
            connection = active_connection
            try:
                active_connection.connect()
            except (socket.timeout, TimeoutError) as exc:
                raise EngineTimeout(
                    f"{operation} connection exceeded its deadline"
                ) from exc
            _remaining(deadline, operation)
            connected_socket = active_connection.sock
            if connected_socket is None:
                raise EngineUnavailable(f"{operation} did not establish a socket")
            connected_socket.settimeout(_remaining(deadline, operation))
            # The explicit connection and deadline timer are the only allowed
            # connection lifecycle; HTTPConnection must not reconnect after
            # the timer closes its socket.
            active_connection.auto_open = 0

            def interrupt_at_deadline() -> None:
                deadline_fired.set()
                _interrupt(active_connection, connected_socket)

            timer = threading.Timer(
                _remaining(deadline, operation), interrupt_at_deadline
            )
            timer.daemon = True
            timer.start()

            # Do not materialize or send the credential until the deadline is
            # checked after connection establishment.
            _remaining(deadline, operation)
            active_connection.putrequest("POST", path, skip_accept_encoding=True)
            active_connection.putheader("Accept", "application/json")
            active_connection.putheader("Content-Type", "application/json")
            active_connection.putheader("Content-Length", str(len(payload)))
            active_connection.putheader("Authorization", "Bearer " + self._credential)
            active_connection.putheader("Connection", "close")
            active_connection.endheaders(payload)
            response = active_connection.getresponse()
            _remaining(deadline, operation)
            status = response.status
            if 300 <= status < 400:
                _protocol_error(f"{operation} redirects are not followed")
            if status == 401:
                raise EngineRejected(f"{operation} authentication was rejected")
            if status == 403:
                raise PolicyAdmissionError(f"{operation} policy rejected the request")
            if not 200 <= status < 300:
                if status >= 500:
                    raise EngineUnavailable(f"{operation} returned a server error")
                raise EngineRejected(f"{operation} rejected the {rejection}")
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except (TypeError, ValueError) as exc:
                    _protocol_error(f"{operation} response length is invalid", exc)
                if declared < 0 or declared > max_response_bytes:
                    _protocol_error(f"{operation} response exceeds its byte bound")
            return self._read_response(
                response, deadline, deadline_fired, max_response_bytes, operation
            )
        except (EngineRejected, EngineProtocolError, EngineTimeout, EngineUnavailable):
            raise
        except (socket.timeout, TimeoutError) as exc:
            if deadline_fired.is_set() or time.monotonic() >= deadline:
                raise EngineTimeout(
                    f"{operation} request exceeded its deadline"
                ) from exc
            raise EngineUnavailable(f"{operation} connection failed") from exc
        except (http.client.HTTPException, OSError) as exc:
            if deadline_fired.is_set() or time.monotonic() >= deadline:
                raise EngineTimeout(
                    f"{operation} request exceeded its deadline"
                ) from exc
            raise EngineUnavailable(f"{operation} connection failed") from exc
        finally:
            if timer is not None:
                timer.cancel()
                timer.join()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    @staticmethod
    def _read_response(
        response: http.client.HTTPResponse,
        deadline: float,
        deadline_fired: threading.Event,
        max_response_bytes: int,
        operation: str,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            _remaining(deadline, operation)
            try:
                chunk = response.read(min(64 * 1024, max_response_bytes + 1 - total))
            except (socket.timeout, TimeoutError) as exc:
                if deadline_fired.is_set() or time.monotonic() >= deadline:
                    raise EngineTimeout(
                        f"{operation} response exceeded its deadline"
                    ) from exc
                raise EngineUnavailable(
                    f"{operation} response could not be read"
                ) from exc
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > max_response_bytes:
                _protocol_error(f"{operation} response exceeds its byte bound")
            chunks.append(chunk)


class EnterpriseEngineClient(_AuthenticatedEngineTransport):
    """Call the authenticated Enterprise adapter for a canonical source plan.

    This remote protocol intentionally differs from the local
    ``EnginePlanningClient`` signature: Enterprise source IDs are governed by
    the authenticated tenant, so no local project root or source body is sent.
    """

    def __init__(
        self,
        base_url: str,
        credential: str,
        tenant_id: str,
        *,
        timeout: float = 30.0,
        allow_loopback_http: bool = False,
    ) -> None:
        super().__init__(
            base_url,
            credential,
            timeout=timeout,
            allow_loopback_http=allow_loopback_http,
        )
        self._tenant_id = _configured_uuid(tenant_id, "tenant_id")

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def context_plan(
        self, request: EnginePlanningRequest, source_ids: Sequence[str]
    ) -> Mapping[str, object]:
        """Return a tenant-bound plan, without claiming admission or execution."""
        if not isinstance(request, EnginePlanningRequest):
            _validation_error("context_plan requires EnginePlanningRequest")
        normalized_ids = _source_ids(source_ids)
        body = {
            "planning": dict(request.to_dict()),
            "source_ids": list(normalized_ids),
        }
        payload = canonical_bytes(body)
        if len(payload) > _MAX_REQUEST_BYTES:
            _validation_error("Enterprise Engine request exceeds its byte bound")
        raw = self._post(payload)
        return self._parse_response(raw, request, normalized_ids)

    def context_materialize(
        self,
        request: EnginePlanningRequest,
        source_ids: Sequence[str],
        expected_governance_revision: int,
        expected_binding_digest: str,
        *,
        planning_evaluation_time: Optional[str] = None,
    ) -> Mapping[str, object]:
        """Materialize a previously bound source plan without claiming execution."""
        if not isinstance(request, EnginePlanningRequest):
            _validation_error("context_materialize requires EnginePlanningRequest")
        normalized_ids = _source_ids(source_ids)
        governance_revision = _validate_input_u64(
            expected_governance_revision, "expected_governance_revision"
        )
        try:
            binding_digest = validate_digest(
                expected_binding_digest, "expected_binding_digest"
            )
        except ValidationError as exc:
            _validation_error("expected_binding_digest is invalid", exc)
        body: dict[str, object] = {
            "planning": dict(request.to_dict()),
            "source_ids": list(normalized_ids),
            "expected_governance_revision": governance_revision,
            "expected_binding_digest": binding_digest,
        }
        if planning_evaluation_time is not None:
            body["planning_evaluation_time"] = _timestamp(
                planning_evaluation_time, "planning_evaluation_time"
            )
        payload = canonical_bytes(body)
        if len(payload) > _MAX_REQUEST_BYTES:
            _validation_error(
                "Enterprise Engine materialization request exceeds its byte bound"
            )
        raw = self._post_json(
            _MATERIALIZATION_PATH,
            payload,
            _MAX_MATERIALIZATION_RESPONSE_BYTES,
            "Enterprise Engine materialization",
            "materialization request",
        )
        return self._parse_materialization_response(
            raw,
            request,
            normalized_ids,
            governance_revision,
            binding_digest,
        )

    def context_execute(
        self,
        task: Mapping[str, object],
        plan: Mapping[str, object],
        request: EnginePlanningRequest,
        source_ids: Sequence[str],
        expected_governance_revision: int,
        expected_binding_digest: str,
        *,
        planning_evaluation_time: Optional[str] = None,
    ) -> Mapping[str, object]:
        """Execute one already-declared local-native source plan.

        The Enterprise host remains the authority for tenant/source admission,
        source bodies, and receipt persistence.  The SDK returns only the
        digest-bound execution projection; its canonical receipt is explicitly
        an ``unknown`` outcome and is not treated as a signature or acceptance
        proof.
        """
        normalized_ids = _source_ids(source_ids)
        normalized_task, normalized_plan = validate_execution_request(
            task, plan, request, self._tenant_id
        )
        governance_revision = _validate_input_u64(
            expected_governance_revision, "expected_governance_revision"
        )
        try:
            binding_digest = validate_digest(
                expected_binding_digest, "expected_binding_digest"
            )
        except ValidationError as exc:
            _validation_error("expected_binding_digest is invalid", exc)
        materialization: dict[str, object] = {
            "planning": dict(request.to_dict()),
            "source_ids": list(normalized_ids),
            "expected_governance_revision": governance_revision,
            "expected_binding_digest": binding_digest,
        }
        if planning_evaluation_time is not None:
            materialization["planning_evaluation_time"] = _timestamp(
                planning_evaluation_time, "planning_evaluation_time"
            )
        body = {
            "task": normalized_task,
            "plan": normalized_plan,
            "materialization": materialization,
        }
        payload = canonical_bytes(body)
        if len(payload) > MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES:
            _validation_error("Enterprise Engine source execution request exceeds its byte bound")
        raw = self._post_json(
            _EXECUTION_PATH,
            payload,
            _MAX_EXECUTION_RESPONSE_BYTES,
            "Enterprise Engine source execution",
            "source execution request",
        )
        return parse_source_execution_response(
            raw,
            request,
            normalized_ids,
            normalized_task,
            normalized_plan,
            self._tenant_id,
            governance_revision,
            binding_digest,
            planning_evaluation_time,
        )

    def provider_execute(
        self,
        task: Mapping[str, object],
        plan: Mapping[str, object],
        request: EnginePlanningRequest,
        source_ids: Sequence[str],
        expected_governance_revision: int,
        expected_binding_digest: str,
        *,
        max_output_tokens: int,
        planning_evaluation_time: Optional[str] = None,
    ) -> Mapping[str, object]:
        """Execute one governed concrete-provider request.

        The host remains authoritative for admission, materialization bytes,
        attempt identity, provider dispatch, billing, and receipts.  Because
        this SDK does not own those values, it validates their wire shape and
        declared task/plan/provider joins without treating request/context
        digests as independently verified proof.
        """
        normalized_ids = _source_ids(source_ids)
        normalized_task, normalized_plan, output_tokens = validate_provider_request(
            task,
            plan,
            request,
            self._tenant_id,
            max_output_tokens,
        )
        governance_revision = _validate_input_u64(
            expected_governance_revision, "expected_governance_revision"
        )
        try:
            binding_digest = validate_digest(
                expected_binding_digest, "expected_binding_digest"
            )
        except ValidationError as exc:
            _validation_error("expected_binding_digest is invalid", exc)
        materialization: dict[str, object] = {
            "planning": dict(request.to_dict()),
            "source_ids": list(normalized_ids),
            "expected_governance_revision": governance_revision,
            "expected_binding_digest": binding_digest,
        }
        if planning_evaluation_time is not None:
            materialization["planning_evaluation_time"] = _timestamp(
                planning_evaluation_time, "planning_evaluation_time"
            )
        body = {
            "schema_version": _SCHEMA_VERSION,
            "task": normalized_task,
            "plan": normalized_plan,
            "materialization": materialization,
            "max_output_tokens": output_tokens,
        }
        payload = canonical_bytes(body)
        if len(payload) > _MAX_PROVIDER_REQUEST_BYTES:
            _validation_error(
                "Enterprise Engine provider execution request exceeds its byte bound"
            )
        raw = self._post_json(
            _PROVIDER_EXECUTION_PATH,
            payload,
            _MAX_PROVIDER_RESPONSE_BYTES,
            "Enterprise Engine provider execution",
            "provider execution request",
        )
        return parse_provider_execution_response(
            raw,
            normalized_task,
            normalized_plan,
            self._tenant_id,
        )

    def context_execute_v2(
        self,
        task: Mapping[str, object],
        plan: Mapping[str, object],
        request: EnginePlanningRequest,
        source_ids: Sequence[str],
        expected_governance_revision: int,
        expected_binding_digest: str,
        *,
        planning_evaluation_time: Optional[str] = None,
    ) -> Mapping[str, object]:
        """Return the v2 execution projection and exact receipt document text.

        The nested v1 execution remains the only SDK validation authority.  The
        returned ``receipt_document_json`` is preserved as UTF-8 text so callers
        can obtain the exact signed bytes; this method does not verify a signer,
        admit a key, or claim an accepted outcome.
        """
        normalized_ids = _source_ids(source_ids)
        normalized_task, normalized_plan = validate_execution_request(
            task, plan, request, self._tenant_id
        )
        governance_revision = _validate_input_u64(
            expected_governance_revision, "expected_governance_revision"
        )
        try:
            binding_digest = validate_digest(
                expected_binding_digest, "expected_binding_digest"
            )
        except ValidationError as exc:
            _validation_error("expected_binding_digest is invalid", exc)
        materialization: dict[str, object] = {
            "planning": dict(request.to_dict()),
            "source_ids": list(normalized_ids),
            "expected_governance_revision": governance_revision,
            "expected_binding_digest": binding_digest,
        }
        if planning_evaluation_time is not None:
            materialization["planning_evaluation_time"] = _timestamp(
                planning_evaluation_time, "planning_evaluation_time"
            )
        body = {
            "task": normalized_task,
            "plan": normalized_plan,
            "materialization": materialization,
        }
        payload = canonical_bytes(body)
        if len(payload) > MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES:
            _validation_error(
                "Enterprise Engine source execution request exceeds its byte bound"
            )
        raw = self._post_json(
            _EXECUTION_V2_PATH,
            payload,
            _MAX_EXECUTION_V2_RESPONSE_BYTES,
            "Enterprise Engine source execution v2",
            "source execution v2 request",
        )
        return parse_source_execution_v2_response(
            raw,
            request,
            normalized_ids,
            normalized_task,
            normalized_plan,
            self._tenant_id,
            governance_revision,
            binding_digest,
            planning_evaluation_time,
        )

    def context_outcome(
        self,
        task_id: str,
        receipt_digest: str,
        context_decision_digest: str,
        signals: Sequence[Mapping[str, object]],
    ) -> Mapping[str, object]:
        """Carry one authenticated operator outcome to the Enterprise host.

        The host binds tenant and agent identity from the authenticated
        credential, and remains the authority for receipt signatures, outcome
        evaluation and ledger mutation.  The SDK preserves the exact returned
        receipt-document string but does not verify signer trust or claim
        accepted learning, billing or accounting.
        """
        normalized = validate_outcome_request(
            task_id,
            receipt_digest,
            context_decision_digest,
            signals,
        )
        payload = canonical_bytes(normalized)
        if len(payload) > _MAX_ENGINE_OUTCOME_REQUEST_BYTES:
            _validation_error("Enterprise Engine outcome request exceeds its byte bound")
        raw = self._post_json(
            _OUTCOME_PATH,
            payload,
            _MAX_ENGINE_OUTCOME_RESPONSE_BYTES,
            "Enterprise Engine outcome",
            "outcome request",
        )
        return parse_engine_outcome_response(
            raw,
            task_id=task_id,
            receipt_digest=receipt_digest,
            context_decision_digest=context_decision_digest,
            tenant_id=self._tenant_id,
        )

    def _parse_materialization_response(
        self,
        raw: bytes,
        request: EnginePlanningRequest,
        source_ids: Tuple[str, ...],
        expected_governance_revision: int,
        expected_binding_digest: str,
    ) -> Mapping[str, object]:
        try:
            value = strict_json_loads(
                raw, label="Enterprise Engine materialization response"
            )
        except ValidationError as exc:
            _protocol_error(
                "Enterprise Engine materialization response is not valid JSON", exc
            )
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "tenant_id",
            "governance_revision",
            "materialization",
        }:
            _protocol_error(
                "Enterprise Engine materialization response fields do not match the v1 contract"
            )
        if (
            isinstance(value["schema_version"], bool)
            or not isinstance(value["schema_version"], int)
            or value["schema_version"] != _SCHEMA_VERSION
        ):
            _protocol_error(
                "Enterprise Engine materialization response schema_version is unsupported"
            )
        try:
            tenant_id = _canonical_uuid(value["tenant_id"], "tenant_id")
        except ValidationError as exc:
            _protocol_error(
                "Enterprise Engine materialization response tenant_id is invalid", exc
            )
        if tenant_id != self._tenant_id:
            _protocol_error(
                "Enterprise Engine materialization response tenant binding does not match"
            )
        governance_revision = _validate_u64(
            value["governance_revision"], "governance_revision"
        )
        if governance_revision != expected_governance_revision:
            _protocol_error(
                "Enterprise Engine materialization governance revision does not match"
            )
        materialization = value["materialization"]
        if not isinstance(materialization, Mapping) or set(materialization) != {
            "schema_version",
            "transport_version",
            "engine_interface_version",
            "plan",
            "materialized_digest",
            "materialized_token_count",
            "content",
        }:
            _protocol_error(
                "Enterprise Engine materialization fields do not match the v1 contract"
            )
        for field_name in ("schema_version", "transport_version"):
            field_value = materialization[field_name]
            if (
                isinstance(field_value, bool)
                or not isinstance(field_value, int)
                or field_value != _SCHEMA_VERSION
            ):
                _protocol_error(
                    f"Enterprise Engine materialization {field_name} is unsupported"
                )
        if materialization["engine_interface_version"] != ENGINE_INTERFACE_VERSION:
            _protocol_error(
                "Enterprise Engine materialization engine_interface_version is unsupported"
            )
        try:
            plan_raw = canonical_bytes(materialization["plan"])
        except ValidationError as exc:
            _protocol_error(
                "Enterprise Engine materialization plan is not canonical JSON", exc
            )
        try:
            plan = parse_source_plan(plan_raw, request)
        except ValidationError as exc:
            _protocol_error(
                "Enterprise Engine materialization source plan failed validation", exc
            )
        _validate_source_scope(plan, source_ids)
        if plan["binding_digest"] != expected_binding_digest:
            _protocol_error(
                "Enterprise Engine materialization binding digest does not match"
            )
        try:
            materialized_digest = validate_digest(
                materialization["materialized_digest"], "materialized_digest"
            )
        except ValidationError as exc:
            _protocol_error("Enterprise Engine materialized digest is invalid", exc)
        materialized_token_count = _validate_u64(
            materialization["materialized_token_count"],
            "materialized_token_count",
        )
        result = cast(Mapping[str, Any], plan["result"])
        result_plan = cast(Mapping[str, Any], result["plan"])
        if materialized_token_count > result_plan["budget_tokens"]:
            _protocol_error(
                "Enterprise Engine materialized token metric exceeds the plan budget"
            )
        content, encoded_content = _validate_materialized_content(
            materialization["content"]
        )
        if sha256_digest(encoded_content) != materialized_digest:
            _protocol_error(
                "Enterprise Engine materialized content digest does not match"
            )
        return {
            "schema_version": _SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "governance_revision": governance_revision,
            "materialization": {
                "schema_version": _SCHEMA_VERSION,
                "transport_version": _SCHEMA_VERSION,
                "engine_interface_version": ENGINE_INTERFACE_VERSION,
                "plan": plan,
                "materialized_digest": materialized_digest,
                "materialized_token_count": materialized_token_count,
                "content": content,
            },
        }

    def _parse_response(
        self,
        raw: bytes,
        request: EnginePlanningRequest,
        source_ids: Tuple[str, ...],
    ) -> Mapping[str, object]:
        try:
            value = strict_json_loads(raw, label="Enterprise Engine response")
        except ValidationError as exc:
            _protocol_error("Enterprise Engine response is not valid JSON", exc)
        if set(value) != _REMOTE_RESPONSE_KEYS:
            _protocol_error(
                "Enterprise Engine response fields do not match the v1 contract"
            )
        if (
            isinstance(value["schema_version"], bool)
            or not isinstance(value["schema_version"], int)
            or value["schema_version"] != _SCHEMA_VERSION
        ):
            _protocol_error("Enterprise Engine response schema_version is unsupported")
        try:
            tenant_id = _canonical_uuid(value["tenant_id"], "tenant_id")
        except ValidationError as exc:
            _protocol_error("Enterprise Engine response tenant_id is invalid", exc)
        if tenant_id != self._tenant_id:
            _protocol_error("Enterprise Engine response tenant binding does not match")
        governance_revision = _validate_u64(
            value["governance_revision"], "governance_revision"
        )
        try:
            plan_raw = canonical_bytes(value["plan"])
        except ValidationError as exc:
            _protocol_error(
                "Enterprise Engine response plan is not canonical JSON", exc
            )
        try:
            plan = parse_source_plan(plan_raw, request)
        except ValidationError as exc:
            _protocol_error("Enterprise Engine source plan failed validation", exc)
        _validate_source_scope(plan, source_ids)
        return {
            "schema_version": _SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "governance_revision": governance_revision,
            "plan": plan,
        }


class EngineContextClient(_AuthenticatedEngineTransport):
    """Call the authenticated host's guarded Engine v1 context-read tool."""

    def context_read(self, path: str) -> EngineContextReadResult:
        normalized_path = _validate_context_path(path)
        body = {
            "name": "ctx_read",
            "arguments": {
                "path": normalized_path,
                "mode": "aggressive",
                "engine_interface": "v1",
            },
        }
        payload = canonical_bytes(body)
        if len(payload) > _MAX_REQUEST_BYTES:
            _validation_error(
                "Guarded Engine context-read request exceeds its byte bound"
            )
        raw = self._post_json(
            _CONTEXT_READ_PATH,
            payload,
            MAX_RESPONSE_BYTES,
            "Guarded Engine context read",
            "context-read request",
        )
        return _parse_context_read_response(raw)


__all__ = [
    "EngineContextClient",
    "EngineContextReadResult",
    "EnterpriseEngineClient",
]
