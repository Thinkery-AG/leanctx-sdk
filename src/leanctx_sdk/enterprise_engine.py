# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Bounded HTTP adapter for the canonical Enterprise Engine planner.

The Enterprise endpoint authenticates and governs source identifiers, then
delegates planning to the public Engine protocol.  This adapter only transports
that request and validates the returned plan; it does not execute, admit, or
manufacture an execution receipt.

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
import uuid
from collections.abc import Sequence
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
    MAX_ENGINE_SOURCE_PLAN_SOURCES,
    EnginePlanningRequest,
    parse_source_plan,
)
from .protocol import canonical_bytes, strict_json_loads


_ENGINE_PATH = "/v1/engine/context-plan"
_SCHEMA_VERSION = 1
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
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


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EngineTimeout("Enterprise Engine request exceeded its deadline")
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


class EnterpriseEngineClient:
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
        if not isinstance(allow_loopback_http, bool):
            _configuration_error("allow_loopback_http must be a boolean")
        self._scheme, self._host, self._port = _validate_base_url(
            base_url, allow_loopback_http
        )
        self._credential = _validate_credential(credential)
        self._tenant_id = _configured_uuid(tenant_id, "tenant_id")
        self._timeout = _validate_timeout(timeout)
        self.base_url = base_url

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def timeout(self) -> float:
        return self._timeout

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

    def _new_connection(self, timeout: float) -> _Connection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(self._host, self._port, timeout=timeout)
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout)

    def _post(self, payload: bytes) -> bytes:
        deadline = time.monotonic() + self._timeout
        connection: Optional[_Connection] = None
        timer: Optional[threading.Timer] = None
        response: Optional[http.client.HTTPResponse] = None
        deadline_fired = threading.Event()
        try:
            active_connection = self._new_connection(_remaining(deadline))
            connection = active_connection
            try:
                active_connection.connect()
            except (socket.timeout, TimeoutError) as exc:
                raise EngineTimeout(
                    "Enterprise Engine connection exceeded its deadline"
                ) from exc
            _remaining(deadline)
            connected_socket = active_connection.sock
            if connected_socket is None:
                raise EngineUnavailable("Enterprise Engine did not establish a socket")
            connected_socket.settimeout(_remaining(deadline))
            # The explicit connection and deadline timer are the only allowed
            # connection lifecycle; HTTPConnection must not reconnect after
            # the timer closes its socket.
            active_connection.auto_open = 0

            def interrupt_at_deadline() -> None:
                deadline_fired.set()
                _interrupt(active_connection, connected_socket)

            timer = threading.Timer(_remaining(deadline), interrupt_at_deadline)
            timer.daemon = True
            timer.start()

            # Do not materialize or send the credential until the deadline is
            # checked after connection establishment.
            _remaining(deadline)
            active_connection.putrequest(
                "POST", _ENGINE_PATH, skip_accept_encoding=True
            )
            active_connection.putheader("Accept", "application/json")
            active_connection.putheader("Content-Type", "application/json")
            active_connection.putheader("Content-Length", str(len(payload)))
            active_connection.putheader("Authorization", "Bearer " + self._credential)
            active_connection.putheader("Connection", "close")
            active_connection.endheaders(payload)
            response = active_connection.getresponse()
            _remaining(deadline)
            status = response.status
            if 300 <= status < 400:
                _protocol_error("Enterprise Engine redirects are not followed")
            if status == 401:
                raise EngineRejected("Enterprise Engine authentication was rejected")
            if status == 403:
                raise PolicyAdmissionError(
                    "Enterprise Engine policy rejected the request"
                )
            if not 200 <= status < 300:
                if status >= 500:
                    raise EngineUnavailable("Enterprise Engine returned a server error")
                raise EngineRejected("Enterprise Engine rejected the planning request")
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except (TypeError, ValueError) as exc:
                    _protocol_error("Enterprise Engine response length is invalid", exc)
                if declared < 0 or declared > _MAX_RESPONSE_BYTES:
                    _protocol_error("Enterprise Engine response exceeds its byte bound")
            return self._read_response(response, deadline, deadline_fired)
        except (EngineRejected, EngineProtocolError, EngineTimeout, EngineUnavailable):
            raise
        except (socket.timeout, TimeoutError) as exc:
            if deadline_fired.is_set() or time.monotonic() >= deadline:
                raise EngineTimeout(
                    "Enterprise Engine request exceeded its deadline"
                ) from exc
            raise EngineUnavailable("Enterprise Engine connection failed") from exc
        except (http.client.HTTPException, OSError) as exc:
            if deadline_fired.is_set() or time.monotonic() >= deadline:
                raise EngineTimeout(
                    "Enterprise Engine request exceeded its deadline"
                ) from exc
            raise EngineUnavailable("Enterprise Engine connection failed") from exc
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
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            _remaining(deadline)
            try:
                chunk = response.read(min(64 * 1024, _MAX_RESPONSE_BYTES + 1 - total))
            except (socket.timeout, TimeoutError) as exc:
                if deadline_fired.is_set() or time.monotonic() >= deadline:
                    raise EngineTimeout(
                        "Enterprise Engine response exceeded its deadline"
                    ) from exc
                raise EngineUnavailable(
                    "Enterprise Engine response could not be read"
                ) from exc
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                _protocol_error("Enterprise Engine response exceeds its byte bound")
            chunks.append(chunk)

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
                _protocol_error(
                    "Enterprise Engine selection is outside requested sources"
                )
        bindings = cast(Sequence[Mapping[str, Any]], plan_value["source_bindings"])
        for binding in bindings:
            if (
                binding["object_ref"] not in requested
                or binding["source_id"] not in requested
            ):
                _protocol_error(
                    "Enterprise Engine source binding is outside requested sources"
                )
            if binding["permission"] != "permitted":
                _protocol_error("Enterprise Engine selected source is not permitted")
        return {
            "schema_version": _SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "governance_revision": governance_revision,
            "plan": plan,
        }


__all__ = ["EnterpriseEngineClient"]
