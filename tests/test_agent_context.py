import asyncio
import json
import os
from pathlib import Path
import queue
import signal
import tempfile
import unittest
from unittest import mock

from leanctx_sdk import (
    AgentContext,
    AgentPermissionError,
    AgentPermissions,
    AsyncAgentContext,
    ConfigurationError,
    EngineCrashed,
    EngineProtocolError,
    ExecutionPolicy,
    ValidationError,
)


class _FakeStdout:
    def __init__(self):
        self.responses = queue.Queue()

    def readline(self, _limit):
        return self.responses.get(timeout=2)


class _FakeStdin:
    def __init__(self, process):
        self.process = process
        self.buffer = bytearray()

    def write(self, value):
        self.buffer.extend(value)
        while b"\n" in self.buffer:
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer[:] = remainder
            self.process.respond(json.loads(line))
        return len(value)

    def flush(self):
        return None


class _FakeProcess:
    def __init__(self, argv):
        self.argv = argv
        policy_path = Path(argv[argv.index("--policy-file") + 1])
        self.policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.stdout = _FakeStdout()
        self.stdin = _FakeStdin(self)
        self.returncode = None
        self.requests = []

    def respond(self, request):
        self.requests.append(request)
        if request["op"] == "hello":
            capabilities = [
                "ctx_compose",
                "ctx_glob",
                "ctx_read",
                "ctx_search",
                "ctx_symbol",
                "ctx_tree",
            ]
            if self.policy["allow_write"]:
                capabilities.extend(("ctx_edit", "ctx_fill", "ctx_patch"))
            if self.policy["allow_exec"]:
                capabilities.append("ctx_shell")
            result = {
                "agent_tools_interface_version": "1.0.0",
                "allow_exec": self.policy["allow_exec"],
                "allow_write": self.policy["allow_write"],
                "capabilities": sorted(capabilities),
                "engine_version": "3.10.1",
                "schema_version": 1,
                "transport_version": 1,
            }
        elif request["op"] == "close":
            result = {"closed": True}
        else:
            result = {
                "changed": request["tool"] == "ctx_patch",
                "content_blocks": [],
                "mode": request["arguments"].get("mode"),
                "original_tokens": 100,
                "output_tokens": 25,
                "saved_tokens": 75,
                "shell": {"exitCode": 0} if request["tool"] == "ctx_shell" else None,
                "text": request["tool"] + " result",
            }
        response = {"id": request["id"], "ok": True, "result": result}
        self.stdout.responses.put(
            json.dumps(response, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.stdout.responses.put(b"")

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.processes = []

        def factory(argv, **_kwargs):
            process = _FakeProcess(argv)
            self.processes.append(process)
            return process

        self.patches = (
            mock.patch("leanctx_sdk.agent.shutil.which", return_value="/fake/lean-ctx"),
            mock.patch("leanctx_sdk.agent.os.path.isfile", return_value=True),
            mock.patch("leanctx_sdk.agent.subprocess.Popen", side_effect=factory),
        )
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.root.cleanup()

    def test_default_is_read_only_and_tracks_savings(self):
        with AgentContext(self.root.name, task="inspect") as context:
            self.assertNotIn("ctx_patch", context.capabilities)
            result = context.read("README.md")
            self.assertEqual(result.output_tokens, 25)
            self.assertEqual(result.saved_ratio, 0.75)
            self.assertEqual(context.metrics.saved_tokens, 75)
            with self.assertRaises(AgentPermissionError):
                context.create_file("new.txt", "content")
            with self.assertRaises(AgentPermissionError):
                context.run(("git", "status"))

    @unittest.skipIf(os.name == "nt", "POSIX directory mode test")
    def test_read_only_project_root_does_not_need_policy_file_writes(self):
        os.chmod(self.root.name, 0o500)
        try:
            with AgentContext(self.root.name) as context:
                self.assertEqual(context.tree().tool, "ctx_tree")
        finally:
            os.chmod(self.root.name, 0o700)

    def test_explicit_permissions_enable_patch_and_argv_execution(self):
        policy = ExecutionPolicy(
            max_timeout=10,
            allowed_executables=("git",),
            allowed_env=("CI",),
        )
        with AgentContext(
            self.root.name,
            permissions=AgentPermissions(write=True, execute=True),
            execution_policy=policy,
        ) as context:
            self.assertTrue(context.create_file("new.txt", "content").changed)
            shell = context.run(("git", "status"), timeout=5)
            self.assertEqual(shell.shell, {"exitCode": 0})
            request = self.processes[-1].requests[-1]
            self.assertEqual(request["arguments"]["argv"], ["git", "status"])
            with self.assertRaises(AgentPermissionError):
                context.run(("python", "-V"))
            with self.assertRaises(AgentPermissionError):
                context.run(("/tmp/git", "status"))
            with self.assertRaises(AgentPermissionError):
                context.run((r"C:\\tools\\git", "status"))
            if os.name != "nt":
                outside = tempfile.TemporaryDirectory()
                self.addCleanup(outside.cleanup)
                os.symlink(outside.name, os.path.join(self.root.name, "escape-link"))
                with self.assertRaises(AgentPermissionError):
                    context.run(("git", "status"), cwd="escape-link")
            with self.assertRaises(AgentPermissionError):
                context.call("ctx_shell", {"command": "git status"})
            with self.assertRaises(ValidationError):
                context.run(("git", "status"), timeout="5")
            context.run(("git", "status"), env={"CI": "1"})
            with self.assertRaises(AgentPermissionError):
                context.run(("git", "status"), env={"PATH": "/tmp"})

    def test_execute_permission_requires_an_explicit_executable_allowlist(self):
        with self.assertRaises(ConfigurationError):
            AgentContext(
                self.root.name,
                permissions=AgentPermissions(execute=True),
            )
        with self.assertRaises(ValidationError):
            ExecutionPolicy(allowed_env=("PATH",))

    def test_async_facade_matches_sync_result(self):
        async def exercise():
            async with AsyncAgentContext(self.root.name) as context:
                result = await context.search("needle")
                self.assertEqual(result.text, "ctx_search result")
                self.assertEqual(context.metrics.tool_calls, 1)

        asyncio.run(exercise())

    def test_cancel_is_terminal_and_reconnect_is_explicit(self):
        context = AgentContext(self.root.name)
        context.cancel()
        with self.assertRaises(EngineCrashed):
            context.read("README.md")
        replacement = context.reconnect()
        try:
            self.assertEqual(replacement.tree().tool, "ctx_tree")
        finally:
            replacement.close()

    @unittest.skipIf(os.name == "nt", "POSIX process-tree behavior")
    @mock.patch("leanctx_sdk.agent.os.killpg")
    @mock.patch("leanctx_sdk.agent.os.kill")
    def test_cancel_tree_stops_engine_and_kills_descendants(self, kill, killpg):
        AgentContext._kill_posix_tree(10)
        kill.assert_any_call(10, signal.SIGSTOP)
        killpg.assert_called_once_with(10, signal.SIGKILL)

    @unittest.skipIf(os.name == "nt", "POSIX process-tree behavior")
    @mock.patch("leanctx_sdk.agent.os.killpg", side_effect=PermissionError("denied"))
    @mock.patch("leanctx_sdk.agent.os.kill")
    def test_cancel_tree_reports_group_kill_failure(self, kill, _killpg):
        error = AgentContext._kill_posix_tree(10)
        self.assertIsInstance(error, PermissionError)
        kill.assert_any_call(10, signal.SIGKILL)

    def test_capability_injection_is_rejected(self):
        with AgentContext(self.root.name) as context:
            hello = {
                "agent_tools_interface_version": "1.0.0",
                "allow_exec": False,
                "allow_write": False,
                "capabilities": sorted((*context.capabilities, "ctx_provider")),
                "engine_version": "3.10.1",
                "schema_version": 1,
                "transport_version": 1,
            }
            with self.assertRaises(EngineProtocolError):
                context._accept_hello(hello)

    def test_non_text_content_blocks_are_preserved(self):
        with AgentContext(self.root.name) as context:
            result = context._parse_tool_result(
                "ctx_read",
                {
                    "changed": False,
                    "content_blocks": [
                        {"type": "image", "data": "YWJj", "mimeType": "image/png"}
                    ],
                    "mode": "image",
                    "original_tokens": 0,
                    "output_tokens": 0,
                    "saved_tokens": 0,
                    "shell": None,
                    "text": "",
                },
            )
            self.assertEqual(result.content_blocks[0]["type"], "image")


if __name__ == "__main__":
    unittest.main()
