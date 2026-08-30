"""Stable local coding-agent tools backed by one persistent LeanCTX Engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
from types import MappingProxyType
from typing import Callable, Mapping, Optional, Sequence, Tuple, Union

from .errors import (
    AgentPermissionError,
    ConfigurationError,
    EngineCrashed,
    EngineError,
    EngineExecutionError,
    EngineProtocolError,
    EngineTimeout,
    EngineUnavailable,
    UnsupportedCapabilityError,
    ValidationError,
)
from .protocol import strict_json_loads

AGENT_TOOLS_INTERFACE_VERSION = "1.0.0"
AGENT_TOOLS_SCHEMA_VERSION = 1
AGENT_TOOLS_TRANSPORT_VERSION = 1
SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = "3.11.0"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_READ_TOOLS = frozenset(
    ("ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree")
)
_WRITE_TOOLS = frozenset(("ctx_edit", "ctx_fill", "ctx_patch"))
_EXEC_TOOLS = frozenset(("ctx_shell",))
_FORBIDDEN_ENV = frozenset(
    (
        "COMSPEC",
        "DYLD_INSERT_LIBRARIES",
        "HOME",
        "LD_PRELOAD",
        "PATH",
        "PATHEXT",
        "PYTHONPATH",
        "RUSTC_WRAPPER",
        "SHELL",
    )
)


class ReadMode(str, Enum):
    AUTO = "auto"
    FULL = "full"
    RAW = "raw"
    SIGNATURES = "signatures"
    MAP = "map"
    DIFF = "diff"
    REFERENCE = "reference"
    TASK = "task"
    ANCHORED = "anchored"


@dataclass(frozen=True)
class AgentPermissions:
    write: bool = False
    execute: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.write, bool) or not isinstance(self.execute, bool):
            raise ValidationError("AgentPermissions values must be boolean")


@dataclass(frozen=True)
class ExecutionPolicy:
    max_timeout: float = 30.0
    allowed_executables: Tuple[str, ...] = ()
    allowed_env: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_timeout, bool)
            or not isinstance(self.max_timeout, (int, float))
            or not 0.1 <= float(self.max_timeout) <= 120.0
        ):
            raise ValidationError("max_timeout must be between 0.1 and 120 seconds")
        normalized = []
        for executable in self.allowed_executables:
            if (
                not isinstance(executable, str)
                or not executable
                or os.path.basename(executable) != executable
                or any(not (character.isalnum() or character in "._+-") for character in executable)
            ):
                raise ValidationError("allowed_executables must contain executable basenames")
            normalized.append(executable)
        object.__setattr__(self, "allowed_executables", tuple(sorted(set(normalized))))
        allowed_env = []
        for name in self.allowed_env:
            if (
                not isinstance(name, str)
                or not name
                or not (name[0].isalpha() or name[0] == "_")
                or any(not (character.isalnum() or character == "_") for character in name)
                or name.upper() in _FORBIDDEN_ENV
            ):
                raise ValidationError("allowed_env must contain environment variable names")
            allowed_env.append(name)
        object.__setattr__(self, "allowed_env", tuple(sorted(set(allowed_env))))


@dataclass(frozen=True)
class ToolResult:
    tool: str
    text: str
    content_blocks: Tuple[Mapping[str, object], ...]
    original_tokens: int
    output_tokens: int
    saved_tokens: int
    mode: Optional[str]
    changed: bool
    shell: Optional[Mapping[str, object]] = None

    @property
    def saved_ratio(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return min(self.saved_tokens, self.original_tokens) / self.original_tokens


@dataclass(frozen=True)
class AgentMetrics:
    tool_calls: int = 0
    original_tokens: int = 0
    output_tokens: int = 0
    saved_tokens: int = 0

    @property
    def saved_ratio(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return min(self.saved_tokens, self.original_tokens) / self.original_tokens


class AgentContext:
    """A deterministic, project-jailed tool session for one host-owned agent."""

    def __init__(
        self,
        project_root: Union[os.PathLike[str], str],
        *,
        task: str = "",
        permissions: AgentPermissions = AgentPermissions(),
        execution_policy: ExecutionPolicy = ExecutionPolicy(),
        engine_binary: Union[os.PathLike[str], str] = "lean-ctx",
        timeout: float = 30.0,
    ) -> None:
        root = Path(project_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ConfigurationError("project_root must be a directory")
        if not isinstance(task, str) or len(task.encode("utf-8")) > 16 * 1024:
            raise ValidationError("task must be a bounded string")
        if not isinstance(permissions, AgentPermissions):
            raise ValidationError("permissions must be AgentPermissions")
        if not isinstance(execution_policy, ExecutionPolicy):
            raise ValidationError("execution_policy must be ExecutionPolicy")
        if permissions.execute and not execution_policy.allowed_executables:
            raise ConfigurationError(
                "execute permission requires at least one allowed executable"
            )
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 120.0:
            raise ConfigurationError("timeout must be between 0.1 and 120 seconds")

        binary = os.fspath(engine_binary)
        resolved_binary = shutil.which(binary) if os.path.sep not in binary else os.path.abspath(binary)
        if not resolved_binary or not os.path.isfile(resolved_binary):
            raise EngineUnavailable("configured Engine binary is unavailable")

        self.project_root = str(root)
        self.task = task
        self.permissions = permissions
        self.execution_policy = execution_policy
        self.timeout = float(timeout)
        self._engine_binary = resolved_binary
        self._lock = threading.RLock()
        self._next_id = 0
        self._closed = False
        self._metrics = AgentMetrics()
        self._capabilities: Tuple[str, ...] = ()
        self._stderr = tempfile.TemporaryFile(mode="w+b")
        self._policy_path = self._write_policy()
        env = {
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
        }
        if permissions.execute:
            for name in ("PATH", "TMPDIR", "TEMP", "TMP"):
                if name in os.environ:
                    env[name] = os.environ[name]
        try:
            self._process = subprocess.Popen(
                [
                    resolved_binary,
                    "engine",
                    "tool-session",
                    "--project-root",
                    self.project_root,
                    "--policy-file",
                    self._policy_path,
                ],
                cwd=self.project_root,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                shell=False,
            )
            self._responses: "queue.Queue[bytes]" = queue.Queue()
            self._reader = threading.Thread(
                target=self._reader_loop,
                name="leanctx-agent-tools-reader",
                daemon=True,
            )
            self._reader.start()
            result = self._exchange(
                {
                    "op": "hello",
                    "schema_version": AGENT_TOOLS_SCHEMA_VERSION,
                    "transport_version": AGENT_TOOLS_TRANSPORT_VERSION,
                    "agent_tools_interface_version": AGENT_TOOLS_INTERFACE_VERSION,
                    "sdk_version": "1.1.0",
                }
            )
            self._accept_hello(result)
        except BaseException:
            self._terminate()
            raise
        finally:
            self._remove_policy()

    def _write_policy(self) -> str:
        descriptor, path = tempfile.mkstemp(
            prefix="leanctx-agent-policy-", suffix=".json"
        )
        try:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(descriptor, 0o600)
            payload = json.dumps(
                {
                    "allow_exec": self.permissions.execute,
                    "allow_write": self.permissions.write,
                    "allowed_env": list(self.execution_policy.allowed_env),
                    "allowed_executables": list(self.execution_policy.allowed_executables),
                    "max_timeout_ms": int(self.execution_policy.max_timeout * 1000),
                    "schema_version": AGENT_TOOLS_SCHEMA_VERSION,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("failed to write Agent Tools policy")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return path

    def _remove_policy(self) -> None:
        path = getattr(self, "_policy_path", None)
        if path is not None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            self._policy_path = None

    def _request_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def _reader_loop(self) -> None:
        assert self._process.stdout is not None
        while True:
            try:
                raw = self._process.stdout.readline(MAX_RESPONSE_BYTES + 1)
            except OSError:
                raw = b""
            self._responses.put(raw)
            if not raw or len(raw) > MAX_RESPONSE_BYTES:
                return

    def _exchange(
        self,
        request: Mapping[str, object],
        *,
        response_timeout: Optional[float] = None,
    ) -> Mapping[str, object]:
        with self._lock:
            if self._closed:
                raise EngineCrashed("AgentContext is closed")
            if self._process.poll() is not None:
                raise EngineCrashed(self._crash_message())
            request_id = self._request_id()
            envelope = dict(request)
            envelope["id"] = request_id
            encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise EngineProtocolError("Agent Tools request exceeds its bound")
            assert self._process.stdin is not None
            try:
                self._process.stdin.write(encoded + b"\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise EngineCrashed(self._crash_message()) from exc
            try:
                raw = self._responses.get(
                    timeout=self.timeout if response_timeout is None else response_timeout
                )
            except queue.Empty as exc:
                self._terminate()
                raise EngineTimeout("Agent Tools response exceeded its deadline") from exc
            if not raw:
                if self._closed:
                    raise EngineCrashed("AgentContext is closed")
                raise EngineCrashed(self._crash_message())
            if len(raw) > MAX_RESPONSE_BYTES or not raw.endswith(b"\n"):
                raise EngineProtocolError("Agent Tools response exceeds its bound")
            try:
                response = strict_json_loads(raw, label="Agent Tools response")
            except (UnicodeDecodeError, ValidationError) as exc:
                raise EngineProtocolError("Agent Tools response is invalid JSON") from exc
            if not isinstance(response, dict) or response.get("id") != request_id or not isinstance(response.get("ok"), bool):
                raise EngineProtocolError("Agent Tools response envelope is invalid")
            expected_envelope = (
                {"id", "ok", "result"}
                if response["ok"]
                else {"id", "ok", "error"}
            )
            if set(response) != expected_envelope:
                raise EngineProtocolError("Agent Tools response envelope fields are invalid")
            if not response["ok"]:
                self._raise_engine_error(response.get("error"))
            result = response.get("result")
            if not isinstance(result, dict):
                raise EngineProtocolError("Agent Tools response omitted result")
            return result

    def _raise_engine_error(self, value: object) -> None:
        if (
            not isinstance(value, dict)
            or set(value) != {"code", "message"}
            or not isinstance(value.get("code"), str)
            or not isinstance(value.get("message"), str)
        ):
            raise EngineProtocolError("Agent Tools error envelope is invalid")
        code = value["code"]
        message = value["message"]
        if code == "permission_denied":
            raise AgentPermissionError(message)
        if code == "unsupported_capability":
            raise UnsupportedCapabilityError(message)
        if code in ("invalid_request", "invalid_state", "unsupported_interface"):
            raise EngineProtocolError(message)
        raise EngineExecutionError(message)

    def _accept_hello(self, result: Mapping[str, object]) -> None:
        expected_fields = {
            "agent_tools_interface_version",
            "allow_exec",
            "allow_write",
            "capabilities",
            "engine_version",
            "schema_version",
            "transport_version",
        }
        if set(result) != expected_fields or (
            result.get("schema_version") != AGENT_TOOLS_SCHEMA_VERSION
            or result.get("transport_version") != AGENT_TOOLS_TRANSPORT_VERSION
            or result.get("agent_tools_interface_version") != AGENT_TOOLS_INTERFACE_VERSION
            or result.get("engine_version") != SUPPORTED_AGENT_TOOLS_ENGINE_VERSION
            or result.get("allow_write") is not self.permissions.write
            or result.get("allow_exec") is not self.permissions.execute
        ):
            raise EngineProtocolError("Agent Tools hello is incompatible")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise EngineProtocolError("Agent Tools capabilities are invalid")
        if capabilities != sorted(set(capabilities)):
            raise EngineProtocolError("Agent Tools capabilities are not canonical")
        expected = set(_READ_TOOLS)
        if self.permissions.write:
            expected.update(_WRITE_TOOLS)
        if self.permissions.execute:
            expected.update(_EXEC_TOOLS)
        if set(capabilities) != expected:
            raise EngineProtocolError("Agent Tools capabilities do not match policy")
        self._capabilities = tuple(capabilities)

    @property
    def capabilities(self) -> Tuple[str, ...]:
        return self._capabilities

    @property
    def metrics(self) -> AgentMetrics:
        return self._metrics

    def call(self, tool: str, arguments: Optional[Mapping[str, object]] = None) -> ToolResult:
        if not isinstance(tool, str) or not tool:
            raise ValidationError("tool must be a non-empty string")
        if tool in _EXEC_TOOLS:
            raise AgentPermissionError("execution tools must use run()")
        if tool in _WRITE_TOOLS and not self.permissions.write:
            raise AgentPermissionError("write permission is disabled")
        return self._call_tool(tool, arguments or {})

    def _call_tool(
        self,
        tool: str,
        arguments: Mapping[str, object],
        *,
        response_timeout: Optional[float] = None,
    ) -> ToolResult:
        if tool not in self._capabilities:
            raise UnsupportedCapabilityError("Engine did not negotiate capability: " + tool)
        if not isinstance(arguments, Mapping) or any(not isinstance(key, str) for key in arguments):
            raise ValidationError("arguments must be a string-keyed mapping")
        result = self._exchange(
            {"op": "call", "tool": tool, "arguments": dict(arguments)},
            response_timeout=response_timeout,
        )
        parsed = self._parse_tool_result(tool, result)
        self._metrics = AgentMetrics(
            tool_calls=self._metrics.tool_calls + 1,
            original_tokens=self._metrics.original_tokens + parsed.original_tokens,
            output_tokens=self._metrics.output_tokens + parsed.output_tokens,
            saved_tokens=self._metrics.saved_tokens + parsed.saved_tokens,
        )
        return parsed

    def _parse_tool_result(self, tool: str, value: Mapping[str, object]) -> ToolResult:
        expected = {
            "text",
            "content_blocks",
            "original_tokens",
            "output_tokens",
            "saved_tokens",
            "mode",
            "changed",
            "shell",
        }
        if set(value) != expected:
            raise EngineProtocolError("Agent Tools result fields are invalid")
        text = value["text"]
        mode = value["mode"]
        shell_value = value["shell"]
        content_blocks_value = value["content_blocks"]
        if not isinstance(text, str) or (mode is not None and not isinstance(mode, str)):
            raise EngineProtocolError("Agent Tools text or mode is invalid")
        integers = (value["original_tokens"], value["output_tokens"], value["saved_tokens"])
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in integers):
            raise EngineProtocolError("Agent Tools token metrics are invalid")
        if value["output_tokens"] + value["saved_tokens"] != value["original_tokens"]:
            raise EngineProtocolError("Agent Tools token metrics are inconsistent")
        if not isinstance(value["changed"], bool) or (shell_value is not None and not isinstance(shell_value, dict)):
            raise EngineProtocolError("Agent Tools status metadata is invalid")
        if not isinstance(content_blocks_value, list) or any(
            not isinstance(block, dict) for block in content_blocks_value
        ):
            raise EngineProtocolError("Agent Tools content blocks are invalid")
        shell = MappingProxyType(dict(shell_value)) if isinstance(shell_value, dict) else None
        content_blocks = tuple(
            MappingProxyType(dict(block)) for block in content_blocks_value
        )
        return ToolResult(
            tool=tool,
            text=text,
            content_blocks=content_blocks,
            original_tokens=value["original_tokens"],
            output_tokens=value["output_tokens"],
            saved_tokens=value["saved_tokens"],
            mode=mode,
            changed=value["changed"],
            shell=shell,
        )

    def read(self, path: str, mode: Union[ReadMode, str] = ReadMode.AUTO, *, fresh: bool = False) -> ToolResult:
        selected = mode.value if isinstance(mode, ReadMode) else mode
        return self._call_tool("ctx_read", {"path": path, "mode": selected, "fresh": fresh})

    def search(self, pattern: str, *, path: str = ".", max_results: int = 50, include: Optional[str] = None) -> ToolResult:
        arguments: dict[str, object] = {"path": path, "pattern": pattern, "max_results": max_results}
        if include is not None:
            arguments["include"] = include
        return self._call_tool("ctx_search", arguments)

    def glob(self, pattern: str, *, path: str = ".", max_results: int = 200) -> ToolResult:
        return self._call_tool("ctx_glob", {"path": path, "pattern": pattern, "max_results": max_results})

    def tree(self, path: str = ".", *, depth: int = 3, show_hidden: bool = False) -> ToolResult:
        return self._call_tool("ctx_tree", {"path": path, "depth": depth, "show_hidden": show_hidden})

    def compose(self, task: Optional[str] = None, *, path: str = ".") -> ToolResult:
        selected_task = self.task if task is None else task
        return self._call_tool("ctx_compose", {"path": path, "task": selected_task})

    def symbol(self, name: str) -> ToolResult:
        return self._call_tool("ctx_symbol", {"name": name})

    def patch(self, *, path: str, op: str, **arguments: object) -> ToolResult:
        if not self.permissions.write:
            raise AgentPermissionError("write permission is disabled")
        payload = dict(arguments)
        payload.update({"path": path, "op": op})
        return self._call_tool("ctx_patch", payload)

    def create_file(self, path: str, text: str) -> ToolResult:
        return self.patch(path=path, op="create", new_text=text)

    def replace_unique(self, path: str, old_text: str, new_text: str) -> ToolResult:
        return self.patch(path=path, op="replace_unique", old_text=old_text, new_text=new_text)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str = ".",
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> ToolResult:
        if not self.permissions.execute:
            raise AgentPermissionError("execute permission is disabled")
        if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValidationError("argv must be a non-empty sequence of strings")
        executable = os.path.basename(argv[0])
        allowed = self.execution_policy.allowed_executables
        if allowed and executable not in allowed:
            raise AgentPermissionError("executable is not allowed: " + executable)
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise ValidationError("timeout must be numeric")
        selected_timeout = (
            self.execution_policy.max_timeout if timeout is None else float(timeout)
        )
        if not 0.1 <= selected_timeout <= self.execution_policy.max_timeout:
            raise ValidationError("timeout exceeds ExecutionPolicy")
        environment = dict(env or {})
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment.items()):
            raise ValidationError("env must be a string mapping")
        unexpected_env = set(environment).difference(self.execution_policy.allowed_env)
        if unexpected_env:
            raise AgentPermissionError(
                "environment variable is not allowed: " + sorted(unexpected_env)[0]
            )
        return self._call_tool(
            "ctx_shell",
            {
                "argv": list(argv),
                "cwd": cwd,
                "env": environment,
                "timeout_ms": int(selected_timeout * 1000),
            },
            response_timeout=max(self.timeout, selected_timeout + 2.0),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._exchange({"op": "close"})
            except EngineError:
                pass
            finally:
                self._closed = True
                self._terminate()

    def cancel(self) -> None:
        """Terminate an in-flight session without retrying its current operation."""
        if self._closed:
            return
        self._closed = True
        self._terminate()

    def reconnect(self) -> "AgentContext":
        """Close this process and return a fresh session with the same policy."""
        self.close()
        return AgentContext(
            self.project_root,
            task=self.task,
            permissions=self.permissions,
            execution_policy=self.execution_policy,
            engine_binary=self._engine_binary,
            timeout=self.timeout,
        )

    def _terminate(self) -> None:
        process = getattr(self, "_process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stderr = getattr(self, "_stderr", None)
        if stderr is not None and not stderr.closed:
            stderr.close()
        self._remove_policy()

    def _crash_message(self) -> str:
        if self._stderr.closed:
            return "Agent Tools Engine exited"
        self._stderr.flush()
        self._stderr.seek(0)
        detail = self._stderr.read(4096).decode("utf-8", "replace").strip()
        return "Agent Tools Engine exited" + ((": " + detail) if detail else "")

    def __enter__(self) -> "AgentContext":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self._terminate()
        except Exception:
            pass


class AsyncAgentContext:
    """Async facade with behavior identical to a serialized AgentContext."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._args = args
        self._kwargs = kwargs
        self._context: Optional[AgentContext] = None

    async def open(self) -> "AsyncAgentContext":
        if self._context is None:
            self._context = await asyncio.to_thread(AgentContext, *self._args, **self._kwargs)
        return self

    @property
    def context(self) -> AgentContext:
        if self._context is None:
            raise EngineUnavailable("AsyncAgentContext is not open")
        return self._context

    @property
    def capabilities(self) -> Tuple[str, ...]:
        return self.context.capabilities

    @property
    def metrics(self) -> AgentMetrics:
        return self.context.metrics

    async def _invoke(
        self,
        function: Callable[..., ToolResult],
        *args: object,
        **kwargs: object,
    ) -> ToolResult:
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except asyncio.CancelledError:
            await asyncio.to_thread(self.context.cancel)
            raise

    async def call(self, tool: str, arguments: Optional[Mapping[str, object]] = None) -> ToolResult:
        return await self._invoke(self.context.call, tool, arguments)

    async def read(self, path: str, mode: Union[ReadMode, str] = ReadMode.AUTO, *, fresh: bool = False) -> ToolResult:
        return await self._invoke(self.context.read, path, mode, fresh=fresh)

    async def search(self, pattern: str, **kwargs: object) -> ToolResult:
        return await self._invoke(self.context.search, pattern, **kwargs)

    async def glob(self, pattern: str, **kwargs: object) -> ToolResult:
        return await self._invoke(self.context.glob, pattern, **kwargs)

    async def tree(self, path: str = ".", **kwargs: object) -> ToolResult:
        return await self._invoke(self.context.tree, path, **kwargs)

    async def compose(self, task: Optional[str] = None, **kwargs: object) -> ToolResult:
        return await self._invoke(self.context.compose, task, **kwargs)

    async def symbol(self, name: str) -> ToolResult:
        return await self._invoke(self.context.symbol, name)

    async def patch(self, **kwargs: object) -> ToolResult:
        return await self._invoke(self.context.patch, **kwargs)

    async def create_file(self, path: str, text: str) -> ToolResult:
        return await self._invoke(self.context.create_file, path, text)

    async def replace_unique(self, path: str, old_text: str, new_text: str) -> ToolResult:
        return await self._invoke(self.context.replace_unique, path, old_text, new_text)

    async def run(self, argv: Sequence[str], **kwargs: object) -> ToolResult:
        return await self._invoke(self.context.run, argv, **kwargs)

    async def cancel(self) -> None:
        if self._context is not None:
            await asyncio.to_thread(self._context.cancel)

    async def reconnect(self) -> "AsyncAgentContext":
        if self._context is not None:
            await asyncio.to_thread(self._context.close)
        self._context = await asyncio.to_thread(AgentContext, *self._args, **self._kwargs)
        return self

    async def close(self) -> None:
        if self._context is not None:
            await asyncio.to_thread(self._context.close)

    async def __aenter__(self) -> "AsyncAgentContext":
        return await self.open()

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()


__all__ = [
    "AGENT_TOOLS_INTERFACE_VERSION",
    "AGENT_TOOLS_SCHEMA_VERSION",
    "AGENT_TOOLS_TRANSPORT_VERSION",
    "AgentContext",
    "AgentMetrics",
    "AgentPermissions",
    "AsyncAgentContext",
    "ExecutionPolicy",
    "ReadMode",
    "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
    "ToolResult",
]
