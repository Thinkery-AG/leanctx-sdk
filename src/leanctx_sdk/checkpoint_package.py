"""Internal `.ctxpkg v2` checkpoint lifecycle bridge.

The open Engine owns package parsing, hashing and Ed25519 verification. This
module owns Product admission and Workspace lifecycle semantics only.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from .errors import (
    WorkspaceConflictError,
    WorkspaceIOError,
    WorkspaceIncompatibleError,
    WorkspacePolicyError,
    WorkspaceValidationError,
)
from .protocol import strict_json_loads
from .workspace import ContextCheckpointV2, ContextWorkspace, WorkspacePolicy


_PACKAGE_ENVELOPE_SCHEMA = "leanctx.ctxpkg-checkpoint/v1"
_INSPECT_SCHEMA = "leanctx.ctxpkg-checkpoint-inspect/v1"
_MIGRATION_SCHEMA = "leanctx.snapshot-v1-migration/v1"
_MAX_PACKAGE_BYTES = 16 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_ENGINE_OUTPUT_BYTES = 16 * 1024 * 1024
_MAX_ENGINE_ERROR_BYTES = 16 * 1024


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
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _exact(value: Any, keys: Sequence[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise WorkspaceIncompatibleError(f"{label} fields do not match the contract")
    return value


def _non_portable_fields(value: Any, pointer: str = "$.checkpoint") -> Tuple[str, ...]:
    found = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = pointer + "." + str(key)
            if key in {"path", "project_root"} and isinstance(item, str):
                if os.path.isabs(item) or (len(item) > 2 and item[1] == ":" and item[2] in "/\\"):
                    found.append(child)
            if key in {"canonical_id", "immutable_ref"} and isinstance(item, str):
                if (
                    item.startswith("file:///")
                    or os.path.isabs(item)
                    or (len(item) > 2 and item[1] == ":" and item[2] in "/\\")
                ):
                    found.append(child)
            found.extend(_non_portable_fields(item, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_non_portable_fields(item, f"{pointer}[{index}]"))
    return tuple(sorted(set(found)))


@dataclass(frozen=True)
class SnapshotV1MigrationProvenance:
    legacy_snapshot_id: str
    legacy_snapshot_digest: str
    checkpoint_id: str
    state_digest: str
    limitations: Tuple[str, ...] = ()
    origin: str = "SnapshotV1"
    migration_contract: str = _MIGRATION_SCHEMA

    def __post_init__(self) -> None:
        if self.origin != "SnapshotV1" or self.migration_contract != _MIGRATION_SCHEMA:
            raise WorkspaceIncompatibleError("SnapshotV1 migration contract is unsupported")
        if not self.legacy_snapshot_id or len(self.legacy_snapshot_id.encode("utf-8")) > 512:
            raise WorkspaceValidationError("legacy_snapshot_id is invalid")
        for name in ("legacy_snapshot_digest", "state_digest"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != 71
                or not value.startswith("sha256:")
                or any(char not in "0123456789abcdef" for char in value[7:])
            ):
                raise WorkspaceValidationError(f"{name} is invalid")
        if len(self.limitations) > 64 or any(
            not isinstance(item, str) or len(item.encode("utf-8")) > 2048
            for item in self.limitations
        ):
            raise WorkspaceValidationError("migration limitations exceed their bounds")

    def to_dict(self) -> Mapping[str, object]:
        return {
            "origin": self.origin,
            "legacy_snapshot_id": self.legacy_snapshot_id,
            "legacy_snapshot_digest": self.legacy_snapshot_digest,
            "migration_contract": self.migration_contract,
            "checkpoint_id": self.checkpoint_id,
            "state_digest": self.state_digest,
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SnapshotV1MigrationProvenance":
        value = _exact(
            value,
            (
                "origin",
                "legacy_snapshot_id",
                "legacy_snapshot_digest",
                "migration_contract",
                "checkpoint_id",
                "state_digest",
                "limitations",
            ),
            "migration provenance",
        )
        if not isinstance(value["limitations"], list):
            raise WorkspaceIncompatibleError("migration limitations must be an array")
        return cls(
            value["legacy_snapshot_id"],
            value["legacy_snapshot_digest"],
            value["checkpoint_id"],
            value["state_digest"],
            tuple(value["limitations"]),
            value["origin"],
            value["migration_contract"],
        )


@dataclass(frozen=True)
class CheckpointPackageInspection:
    path: str
    package_name: str
    package_version: str
    package_digest: str
    content_hash: str
    signature_state: str
    signer_public_key: Optional[str]
    checkpoint: ContextCheckpointV2
    migration_provenance: Optional[SnapshotV1MigrationProvenance]
    non_portable_fields: Tuple[str, ...]

    @property
    def signed(self) -> bool:
        return self.signature_state == "signed_valid"


@dataclass(frozen=True)
class SnapshotV1Inspection:
    snapshot_id: str
    artifact_digest: str
    signer_public_key: str
    signature_state: str = "signed_valid"


@dataclass(frozen=True)
class SnapshotV1MigrationResult:
    classification: str
    checkpoint: ContextCheckpointV2
    provenance: SnapshotV1MigrationProvenance


class LocalCheckpointPackageEngine:
    """Bounded subprocess bridge to the open Engine checkpoint package path."""

    def __init__(
        self,
        executable: Optional[str] = None,
        *,
        timeout: float = 30.0,
        environment: Optional[Mapping[str, str]] = None,
    ) -> None:
        selected = executable or shutil.which("lean-ctx")
        if selected is None:
            raise WorkspaceIncompatibleError("lean-ctx Engine executable is unavailable")
        try:
            selected_info = os.lstat(selected)
        except OSError as exc:
            raise WorkspaceIncompatibleError("lean-ctx Engine executable is unavailable") from exc
        if stat.S_ISLNK(selected_info.st_mode):
            raise WorkspaceIncompatibleError("lean-ctx Engine executable is invalid")
        resolved = os.path.realpath(selected)
        try:
            info = os.stat(resolved)
        except OSError as exc:
            raise WorkspaceIncompatibleError("lean-ctx Engine executable is unavailable") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_mode & stat.S_IXUSR == 0:
            raise WorkspaceIncompatibleError("lean-ctx Engine executable is invalid")
        if timeout <= 0 or timeout > 120:
            raise WorkspaceValidationError("Engine timeout is invalid")
        self._executable = resolved
        self._timeout = timeout
        self._environment = dict(environment or {})

    def _run(self, arguments: Sequence[str]) -> Mapping[str, Any]:
        try:
            result = subprocess.run(
                [self._executable, "pack", *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self._timeout,
                env=dict(os.environ, NO_COLOR="1", **self._environment),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceIOError() from exc
        if len(result.stdout) > _MAX_ENGINE_OUTPUT_BYTES or len(result.stderr) > _MAX_ENGINE_ERROR_BYTES:
            raise WorkspacePolicyError()
        if result.returncode != 0:
            raise WorkspaceIncompatibleError("Engine rejected checkpoint package operation")
        try:
            decoded = result.stdout.decode("utf-8", "strict")
            value = strict_json_loads(decoded)
        except Exception as exc:
            raise WorkspaceIncompatibleError("Engine checkpoint response is invalid") from exc
        if not isinstance(value, Mapping):
            raise WorkspaceIncompatibleError("Engine checkpoint response is invalid")
        return value

    def seal(
        self,
        checkpoint: ContextCheckpointV2,
        destination: Any,
        *,
        package_name: str,
        package_version: str,
        migration_provenance: Optional[SnapshotV1MigrationProvenance] = None,
        sign: bool = True,
    ) -> CheckpointPackageInspection:
        raw_destination = Path(os.fspath(destination)).expanduser()
        if raw_destination.is_symlink():
            raise WorkspaceValidationError("destination must be a non-symlink .ctxpkg path")
        destination_path = raw_destination.resolve()
        if destination_path.suffix != ".ctxpkg":
            raise WorkspaceValidationError("destination must be a non-symlink .ctxpkg path")
        if migration_provenance is not None and (
            migration_provenance.checkpoint_id != checkpoint.checkpoint_id
            or migration_provenance.state_digest != checkpoint.state_digest
        ):
            raise WorkspaceConflictError()
        checkpoint_value = _plain(checkpoint.to_dict())
        payload = {
            "schema_version": _PACKAGE_ENVELOPE_SCHEMA,
            "checkpoint": checkpoint_value,
            "migration_provenance": (
                _plain(migration_provenance.to_dict()) if migration_provenance else None
            ),
            "non_portable_fields": list(_non_portable_fields(checkpoint_value)),
        }
        if payload["migration_provenance"] is None:
            del payload["migration_provenance"]
        destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, payload_path = tempfile.mkstemp(
            prefix=".leanctx-checkpoint-",
            suffix=".json",
            dir=str(destination_path.parent),
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            arguments = [
                "checkpoint-seal",
                f"--checkpoint={payload_path}",
                f"--output={destination_path}",
                f"--name={package_name}",
                f"--version={package_version}",
            ]
            if not sign:
                arguments.append("--unsigned")
            self._run(arguments)
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass
        return self.inspect(destination_path)

    def inspect(self, package_path: Any) -> CheckpointPackageInspection:
        raw_path = Path(os.fspath(package_path)).expanduser()
        if raw_path.is_symlink():
            raise WorkspacePolicyError()
        path = raw_path.resolve()
        try:
            info = path.stat()
        except OSError as exc:
            raise WorkspaceIOError() from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_PACKAGE_BYTES:
            raise WorkspacePolicyError()
        response = _exact(
            self._run(("checkpoint-inspect", str(path))),
            ("schema_version", "package", "checkpoint"),
            "checkpoint inspect response",
        )
        if response["schema_version"] != _INSPECT_SCHEMA:
            raise WorkspaceIncompatibleError("Engine checkpoint inspect schema is unsupported")
        package = _exact(
            response["package"],
            (
                "schema_version",
                "kind",
                "layers",
                "name",
                "version",
                "package_digest",
                "content_hash",
                "signature_state",
                "signer_public_key",
            ),
            "checkpoint package identity",
        )
        if package["schema_version"] != 2 or package["kind"] != "context":
            raise WorkspaceIncompatibleError("checkpoint package is not additive ctxpkg v2")
        if not isinstance(package["layers"], list) or package["layers"].count("checkpoint") != 1:
            raise WorkspaceIncompatibleError("checkpoint package layer is missing or duplicated")
        for name in ("package_digest", "content_hash"):
            digest = package[name]
            if (
                not isinstance(digest, str)
                or len(digest) != 71
                or not digest.startswith("sha256:")
                or any(char not in "0123456789abcdef" for char in digest[7:])
            ):
                raise WorkspaceIncompatibleError("checkpoint package digest is invalid")
        if not isinstance(package["name"], str) or not isinstance(package["version"], str):
            raise WorkspaceIncompatibleError("checkpoint package identity is invalid")
        if package["signature_state"] not in {"signed_valid", "unsigned"}:
            raise WorkspaceIncompatibleError("checkpoint package signature state is invalid")
        signer = package["signer_public_key"]
        if (
            package["signature_state"] == "signed_valid"
            and (
                not isinstance(signer, str)
                or len(signer) != 64
                or any(char not in "0123456789abcdef" for char in signer)
            )
        ) or (package["signature_state"] == "unsigned" and signer is not None):
            raise WorkspaceIncompatibleError("checkpoint package signer identity is invalid")
        portable_raw = response["checkpoint"]
        if not isinstance(portable_raw, Mapping):
            raise WorkspaceIncompatibleError("portable checkpoint envelope is invalid")
        portable = _exact(
            portable_raw,
            ("schema_version", "checkpoint", "migration_provenance", "non_portable_fields")
            if "migration_provenance" in portable_raw
            else ("schema_version", "checkpoint", "non_portable_fields"),
            "portable checkpoint envelope",
        )
        if portable["schema_version"] != _PACKAGE_ENVELOPE_SCHEMA:
            raise WorkspaceIncompatibleError("portable checkpoint schema is unsupported")
        checkpoint = ContextCheckpointV2.from_dict(portable["checkpoint"])
        declared = portable["non_portable_fields"]
        expected = _non_portable_fields(checkpoint.to_dict())
        if (
            not isinstance(declared, list)
            or any(not isinstance(item, str) for item in declared)
            or tuple(declared) != expected
        ):
            raise WorkspaceIncompatibleError("non-portable checkpoint fields are misclassified")
        migration = None
        if portable.get("migration_provenance") is not None:
            migration = SnapshotV1MigrationProvenance.from_dict(portable["migration_provenance"])
            if migration.checkpoint_id != checkpoint.checkpoint_id or migration.state_digest != checkpoint.state_digest:
                raise WorkspaceConflictError()
        return CheckpointPackageInspection(
            str(path),
            package["name"],
            package["version"],
            package["package_digest"],
            package["content_hash"],
            package["signature_state"],
            package["signer_public_key"],
            checkpoint,
            migration,
            expected,
        )

    def inspect_snapshot_v1(self, snapshot_path: Any) -> SnapshotV1Inspection:
        raw_path = Path(os.fspath(snapshot_path)).expanduser()
        try:
            info = raw_path.lstat()
        except OSError as exc:
            raise WorkspaceIOError() from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise WorkspacePolicyError()
        if info.st_size > _MAX_SNAPSHOT_BYTES:
            raise WorkspacePolicyError()
        path = raw_path.resolve()
        response = _exact(
            self._run(("snapshot-v1-inspect", str(path))),
            (
                "schema_version",
                "snapshot_id",
                "artifact_digest",
                "signature_state",
                "signer_public_key",
            ),
            "SnapshotV1 inspect response",
        )
        if (
            response["schema_version"] != "leanctx.snapshot-v1-inspect/v1"
            or response["signature_state"] != "signed_valid"
            or not isinstance(response["snapshot_id"], str)
            or len(response["snapshot_id"]) != 64
            or any(char not in "0123456789abcdef" for char in response["snapshot_id"])
            or not isinstance(response["artifact_digest"], str)
            or len(response["artifact_digest"]) != 71
            or not response["artifact_digest"].startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in response["artifact_digest"][7:])
            or not isinstance(response["signer_public_key"], str)
            or len(response["signer_public_key"]) != 64
            or any(char not in "0123456789abcdef" for char in response["signer_public_key"])
        ):
            raise WorkspaceIncompatibleError("SnapshotV1 verification result is invalid")
        return SnapshotV1Inspection(
            response["snapshot_id"],
            response["artifact_digest"],
            response["signer_public_key"],
        )


def seal_checkpoint_package(
    workspace: ContextWorkspace,
    checkpoint: ContextCheckpointV2,
    destination: Any,
    *,
    package_name: str,
    package_version: str,
    engine: Optional[LocalCheckpointPackageEngine] = None,
    migration_provenance: Optional[SnapshotV1MigrationProvenance] = None,
    sign: bool = True,
) -> CheckpointPackageInspection:
    if checkpoint.workspace_id != workspace.workspace_id:
        raise WorkspaceConflictError()
    if workspace.get_checkpoint(checkpoint.checkpoint_id).to_dict() != checkpoint.to_dict():
        raise WorkspaceConflictError()
    selected = engine or LocalCheckpointPackageEngine()
    inspection = selected.seal(
        checkpoint,
        destination,
        package_name=package_name,
        package_version=package_version,
        migration_provenance=migration_provenance,
        sign=sign,
    )
    workspace._record_sealed_package(inspection)
    return inspection


def seed_workspace_from_package(
    state_root: Any,
    package_path: Any,
    name: str,
    *,
    engine: Optional[LocalCheckpointPackageEngine] = None,
    trusted_signer: bool = False,
    allow_unsigned: bool = False,
) -> ContextWorkspace:
    selected = engine or LocalCheckpointPackageEngine()
    inspection = selected.inspect(package_path)
    if inspection.signed and not trusted_signer:
        raise WorkspacePolicyError()
    if not inspection.signed and not allow_unsigned:
        raise WorkspacePolicyError()
    policy = WorkspacePolicy.from_dict(
        _plain(inspection.checkpoint.logical_state["policy"])
    )
    workspace = ContextWorkspace.create(
        state_root,
        name,
        policy=policy,
        workspace_id=inspection.checkpoint.workspace_id,
    )
    workspace._seed_from_package(
        inspection,
        trusted_signer=trusted_signer,
        allow_unsigned=allow_unsigned,
    )
    return workspace


def migrate_snapshot_v1(
    workspace: ContextWorkspace,
    snapshot_path: Any,
    *,
    engine: Optional[LocalCheckpointPackageEngine] = None,
    limitations: Sequence[str],
) -> SnapshotV1MigrationResult:
    if not limitations:
        raise WorkspaceValidationError(
            "SnapshotV1 migration requires explicit limitations"
        )
    selected = engine or LocalCheckpointPackageEngine()
    inspection = selected.inspect_snapshot_v1(snapshot_path)
    checkpoint = workspace.checkpoint()
    provenance = SnapshotV1MigrationProvenance(
        inspection.snapshot_id,
        inspection.artifact_digest,
        checkpoint.checkpoint_id,
        checkpoint.state_digest,
        tuple(limitations),
    )
    return SnapshotV1MigrationResult(
        "MIGRATABLE_WITH_EXPLICIT_LIMITATIONS",
        checkpoint,
        provenance,
    )
