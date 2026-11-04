# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Presenter capabilities of the v2 materialization contract.

A presenter capability is a bearer secret the instance returns once, to the host
that presented sources.  Whoever later shows it is treated as THE HOLDER OF
THAT CAPABILITY for those presentations.  It is not an agent name, not an
attested identity and not an authorization: current source rights are checked
again at every use, with or without it.

The capability is host-internal.  Keep it in host memory or a host secret
store.  Never put it in model context, a tool argument, a tool result, a log
or a receipt, and never send it to a provider.  Records name a presenter only
by ``presenter_ref``.
"""

from __future__ import annotations

import re
import weakref
from typing import Any, Mapping, NoReturn, Optional

from .errors import InstanceProtocolError, ValidationError

ENGINE_PRESENTER_CAPABILITY_MIN_ENGINE_VERSION = "3.11.0"
MAX_ENGINE_PRESENTER_TTL_SECONDS = 24 * 60 * 60
# Request header that carries a capability to the proxy or the Enterprise
# suite.  Read from request headers only; never part of a request body.
PRESENTER_CAPABILITY_HEADER = "x-leanctx-presenter"

_CAPABILITY = re.compile(r"lcp1_[0-9a-f]{64}\Z")
_REFERENCE = re.compile(r"presenter:[0-9a-f]{32}\Z")
_MAX_U64 = (1 << 64) - 1
_REDACTED = "PresenterCapability(<redacted>)"
_SECRETS: "weakref.WeakKeyDictionary[PresenterCapability, str]" = (
    weakref.WeakKeyDictionary()
)


class PresenterCapability:
    """Opaque holder of one capability.

    Every rendering is redacted, and the object refuses to be pickled or
    copied.  ``reveal()`` is the one deliberate way to the secret, for a host
    that stores it in its own secret store.
    """

    __slots__ = ("__weakref__",)

    def __init__(self, secret: str) -> None:
        if not isinstance(secret, str) or _CAPABILITY.match(secret) is None:
            raise ValidationError("presenter capability is malformed")
        _SECRETS[self] = secret

    def reveal(self) -> str:
        """Return the secret.  Never log, print or forward the result."""
        return _SECRETS[self]

    def __repr__(self) -> str:
        return _REDACTED

    def __str__(self) -> str:
        return _REDACTED

    def __format__(self, _spec: str) -> str:
        return _REDACTED

    def __reduce__(self) -> NoReturn:
        raise TypeError("a presenter capability is not serializable")

    def __copy__(self) -> NoReturn:
        raise TypeError("a presenter capability is not copyable")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        raise TypeError("a presenter capability is not copyable")


def _protocol_error(message: str) -> NoReturn:
    raise InstanceProtocolError(message)


def presenter_request(presenter: Any) -> Mapping[str, object]:
    """The wire member for ``presenter``: a lifetime in seconds issues a new
    capability, a held ``PresenterCapability`` renews under it.  A capability
    is never part of this member."""
    if isinstance(presenter, PresenterCapability):
        return {"mode": "renew"}
    if (
        isinstance(presenter, bool)
        or not isinstance(presenter, int)
        or not 1 <= presenter <= MAX_ENGINE_PRESENTER_TTL_SECONDS
    ):
        raise ValidationError(
            "presenter must be a PresenterCapability or a lifetime of 1 to "
            f"{MAX_ENGINE_PRESENTER_TTL_SECONDS} seconds"
        )
    return {"mode": "issue", "ttl_seconds": presenter}


def parse_presenter_grant(
    value: Any, content: str, *, issued: bool
) -> Mapping[str, object]:
    """Validate the grant of a v2 response.  ``issued`` says whether this call
    asked for a new capability: then exactly one must be present, otherwise
    none may be."""
    if not isinstance(value, Mapping) or not set(value) <= {
        "presenter_ref",
        "expires_at_unix",
        "capability",
    }:
        _protocol_error("presenter grant fields do not match the v2 contract")
    reference = value.get("presenter_ref")
    if not isinstance(reference, str) or _REFERENCE.match(reference) is None:
        _protocol_error("presenter_ref is malformed")
    expires_at = value.get("expires_at_unix")
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or not 0 < expires_at <= _MAX_U64
    ):
        _protocol_error("presenter expires_at_unix is malformed")
    secret = value.get("capability")
    if ("capability" in value) != issued:
        _protocol_error("presenter capability presence does not match the request")
    capability: Optional[PresenterCapability] = None
    if issued:
        if not isinstance(secret, str) or _CAPABILITY.match(secret) is None:
            _protocol_error("presenter capability is malformed")
        if secret in content:
            _protocol_error("presenter capability appears in materialized content")
        capability = PresenterCapability(secret)
    return {
        "presenter_ref": reference,
        "expires_at_unix": expires_at,
        "capability": capability,
    }


# The use of text that was delivered under a presented lineage
# (``leanctx.engine-presented-use/v1``).
PRESENTED_USE_MODES = ("check", "retain", "expand")
MAX_PRESENTED_CONTENT_BYTES = 1024 * 1024
_RECOVERY_ID = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"(?:lineage|recovery)\.[a-z_]{1,48}\Z")
_USE_KEYS = {
    "schema_version",
    "presenter_ref",
    "usable",
    "reason",
    "source_ids",
    "recovery_id",
    "content",
}


def presented_use_request(mode: Any, value: Any) -> Mapping[str, object]:
    """The wire member ``use``: ``check`` and ``retain`` take the delivered
    text, ``expand`` takes a recovery id.  A capability is never part of it."""
    if mode not in PRESENTED_USE_MODES:
        raise ValidationError("presented use mode must be check, retain or expand")
    if not isinstance(value, str):
        raise ValidationError("presented use value must be a string")
    if mode == "expand":
        if _RECOVERY_ID.match(value) is None:
            raise ValidationError("recovery_id is malformed")
        return {"mode": mode, "recovery_id": value}
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeEncodeError as exc:
        raise ValidationError("presented content is not valid UTF-8") from exc
    if not 0 < size <= MAX_PRESENTED_CONTENT_BYTES:
        raise ValidationError("presented content is empty or exceeds its byte bound")
    return {"mode": mode, "content": value}


def parse_presented_use(
    value: Any, request: Mapping[str, object], capability: PresenterCapability
) -> Mapping[str, object]:
    """Validate the instance's answer to ``request`` (a ``presented_use_request``
    member).  ``usable: False`` carries a content-free reason and nothing
    else; text comes back for ``expand`` only and never holds the capability."""
    if (
        not isinstance(value, Mapping)
        or not {"schema_version", "presenter_ref", "usable", "source_ids"}
        <= set(value)
        <= _USE_KEYS
        or not isinstance(value["schema_version"], int)
        or isinstance(value["schema_version"], bool)
        or value["schema_version"] != 1
        or not isinstance(value["usable"], bool)
    ):
        _protocol_error("presented use fields do not match the v1 contract")
    reference = value["presenter_ref"]
    if not isinstance(reference, str) or _REFERENCE.match(reference) is None:
        _protocol_error("presenter_ref is malformed")
    source_ids = value["source_ids"]
    if not isinstance(source_ids, list) or not all(
        isinstance(item, str) and item for item in source_ids
    ):
        _protocol_error("presented use source_ids are malformed")
    # A lineage names every source once: a repeated id could stand in for a
    # source the answer does not name.
    if len(set(source_ids)) != len(source_ids):
        _protocol_error("presented use source_ids repeat a source")
    reason = value.get("reason")
    recovery_id = value.get("recovery_id")
    content = value.get("content")
    if not value["usable"]:
        if (
            not isinstance(reason, str)
            or _REASON.match(reason) is None
            or source_ids
            or "recovery_id" in value
            or "content" in value
        ):
            _protocol_error("presented use refusal does not match the v1 contract")
        return {
            "presenter_ref": reference,
            "usable": False,
            "reason": reason,
            "source_ids": (),
            "recovery_id": None,
            "content": None,
        }
    mode = request["mode"]
    if "reason" in value or not source_ids:
        _protocol_error("presented use answer does not match the v1 contract")
    if ("recovery_id" in value) != (mode != "check") or ("content" in value) != (
        mode == "expand"
    ):
        _protocol_error("presented use answer does not match what was asked")
    if recovery_id is not None and (
        not isinstance(recovery_id, str)
        or _RECOVERY_ID.match(recovery_id) is None
        or (mode == "expand" and recovery_id != request["recovery_id"])
    ):
        _protocol_error("presented use recovery_id is malformed")
    if content is not None and (
        not isinstance(content, str) or capability.reveal() in content
    ):
        _protocol_error("presented use content is malformed")
    return {
        "presenter_ref": reference,
        "usable": True,
        "reason": None,
        "source_ids": tuple(source_ids),
        "recovery_id": recovery_id,
        "content": content,
    }


def parse_presenter_revocation(value: Any) -> Mapping[str, object]:
    """Validate the instance's answer to a revocation."""
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "presenter_ref", "revoked"}
        or not isinstance(value["schema_version"], int)
        or isinstance(value["schema_version"], bool)
        or value["schema_version"] != 1
        or not isinstance(value["revoked"], bool)
    ):
        _protocol_error("presenter revocation fields do not match the v1 contract")
    reference = value["presenter_ref"]
    if not isinstance(reference, str) or _REFERENCE.match(reference) is None:
        _protocol_error("presenter_ref is malformed")
    return {"presenter_ref": reference, "revoked": value["revoked"]}
