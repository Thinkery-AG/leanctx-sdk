"""AgentContext against a real child process: deadlines, cancel, terminal failures.

The stand-in Engine is a script run by this interpreter. Nothing here starts a
LeanCTX | OSS.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from leanctx_sdk import agent as agent_module
from leanctx_sdk import instance as engine_module
from leanctx_sdk import (
    AgentContext,
    InstanceCrashed,
    InstanceExecutionError,
    InstanceProtocolError,
    InstanceTimeout,
)

_STAND_IN = r'''
import json, os, subprocess, sys, time

MODE = {mode!r}
ROOT = {root!r}
READ = ["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"]
GOOD = dict(text="ok", content_blocks=[], original_tokens=4, output_tokens=1,
            saved_tokens=3, mode="full", changed=False, shell=None)


def send(value):
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def publish(name, value):
    path = os.path.join(ROOT, name)
    with open(path + ".tmp", "w") as handle:
        handle.write(str(value))
    os.replace(path + ".tmp", path)


publish("policy.path", sys.argv[sys.argv.index("--policy-file") + 1])
hello = json.loads(sys.stdin.readline())
descendant = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
publish("descendant.pid", descendant.pid)
publish("leader.pid", os.getpid())
send(dict(id=hello["id"], ok=True, result=dict(
    agent_tools_interface_version="1.0.0", allow_exec=False, allow_write=False,
    capabilities=READ, engine_version={engine_version!r}, schema_version=1,
    transport_version=1)))
if MODE == "stall":
    time.sleep(60)
elif MODE == "close_stdout":
    os.close(1)
    time.sleep(60)
for line in sys.stdin:
    request = json.loads(line)
    if request["op"] == "close":
        send(dict(id=request["id"], ok=True, result=dict()))
        break
    if MODE == "bad_result":
        send(dict(id=request["id"], ok=True, result=dict(GOOD, saved_tokens=99)))
    elif MODE == "wrong_id":
        send(dict(id="unexpected", ok=True, result=GOOD))
    elif MODE == "exit":
        os._exit(7)
    elif MODE == "exit_holder":
        # Leaves a child that inherited this process's stdout pipe, then exits.
        # The child also ignores SIGTERM: only a group SIGKILL ends it.
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
            stdin=subprocess.DEVNULL)
        publish("holder.pid", holder.pid)
        os._exit(7)
    else:
        send(dict(id=request["id"], ok=True, result=GOOD))
descendant.kill()
'''


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _group_members(group):
    """Members of a process group, read from the process table."""
    output = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "pgid=", "-o", "stat="],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.decode()
    members = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == str(group) and not fields[2].startswith("Z"):
            members.append(int(fields[0]))
    return members


@unittest.skipIf(os.name == "nt", "POSIX process groups; Windows runs in the CI matrix")
class AgentContextProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.directory.name)
        self.contexts = []

    def tearDown(self):
        for context in self.contexts:
            try:
                context.cancel()
            except Exception:
                pass
        for name in ("leader.pid", "descendant.pid"):
            path = os.path.join(self.root, name)
            if os.path.exists(path):
                try:
                    os.kill(int(Path(path).read_text()), 9)
                except (ProcessLookupError, ValueError):
                    pass
        self.directory.cleanup()

    def open(self, mode, *, timeout=2.0, engine_version="3.11.0"):
        script = os.path.join(self.root, "stand-in-engine.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write("#!" + sys.executable + "\n")
            handle.write(
                _STAND_IN.format(mode=mode, root=self.root, engine_version=engine_version)
            )
        os.chmod(script, 0o700)
        context = AgentContext(self.root, instance_binary=script, timeout=timeout)
        self.contexts.append(context)
        return context

    def published(self, name):
        deadline = time.monotonic() + 3
        path = os.path.join(self.root, name)
        while time.monotonic() < deadline:
            if os.path.exists(path):
                return Path(path).read_text()
            time.sleep(0.01)
        self.fail("stand-in Engine did not publish " + name)

    def assert_terminal(self, context):
        """Tree gone by process-table listing, policy gone, reader joined, calls typed."""
        leader = int(self.published("leader.pid"))
        descendant = int(self.published("descendant.pid"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and (
            _alive(descendant) or _group_members(leader)
        ):
            time.sleep(0.01)
        self.assertFalse(_alive(descendant), "descendant survived")
        self.assertEqual(_group_members(leader), [])
        self.assertIsNotNone(context._process.poll(), "Engine was not reaped")
        self.assertFalse(os.path.exists(self.published("policy.path")))
        context._reader.join(timeout=3)
        self.assertFalse(context._reader.is_alive(), "reader thread is still blocked")
        with self.assertRaises(InstanceCrashed):
            context.read("README.md")
        context.close()
        context.close()

    def test_round_trip_removes_exact_policy_file_after_hello(self):
        context = self.open("ok")
        policy_path = self.published("policy.path")
        self.assertTrue(os.path.basename(policy_path).startswith("leanctx-agent-policy-"))
        self.assertFalse(os.path.exists(policy_path))
        self.assertEqual(context.read("README.md").text, "ok")
        context.close()
        self.assert_terminal(context)

    def test_stalled_stdin_write_is_bounded_by_the_deadline(self):
        context = self.open("stall", timeout=0.7)
        started = time.monotonic()
        with self.assertRaises(InstanceTimeout):
            context.call("ctx_read", {"padding": "x" * 600_000})
        self.assertLess(time.monotonic() - started, 3.0)
        self.assert_terminal(context)

    def test_missing_response_times_out_typed(self):
        context = self.open("stall", timeout=0.5)
        started = time.monotonic()
        with self.assertRaises(InstanceTimeout):
            context.read("README.md")
        self.assertLess(time.monotonic() - started, 3.0)
        self.assert_terminal(context)

    def test_cancel_interrupts_stalled_write_and_pending_response(self):
        for name, arguments in (
            ("stalled write", {"padding": "x" * 600_000}),
            ("pending response", {}),
        ):
            with self.subTest(name=name):
                for stale in ("leader.pid", "descendant.pid", "policy.path"):
                    path = os.path.join(self.root, stale)
                    if os.path.exists(path):
                        os.unlink(path)
                # The deadline is far away: only cancel() may end this call.
                context = self.open("stall", timeout=30.0)
                self.published("leader.pid")
                outcome = []

                def call():
                    try:
                        context.call("ctx_read", arguments)
                        outcome.append(None)
                    except BaseException as error:  # recorded for the assertion
                        outcome.append(error)

                worker = threading.Thread(target=call)
                worker.start()
                time.sleep(0.3)
                started = time.monotonic()
                context.cancel()
                self.assertLess(time.monotonic() - started, 2.0)
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive(), "cancelled call is still blocked")
                self.assertIsInstance(outcome[0], InstanceCrashed)
                self.assert_terminal(context)

    def test_closed_stdout_with_live_engine_and_descendant_is_terminal(self):
        context = self.open("close_stdout")
        with self.assertRaises(InstanceCrashed):
            context.read("README.md")
        self.assert_terminal(context)

    def test_engine_exit_with_surviving_descendant_is_terminal(self):
        context = self.open("exit")
        with self.assertRaises(InstanceCrashed):
            context.read("README.md")
        self.assert_terminal(context)

    def test_invalid_tool_result_in_valid_envelope_is_terminal(self):
        context = self.open("bad_result")
        with self.assertRaises(InstanceProtocolError):
            context.read("README.md")
        self.assert_terminal(context)

    def test_unexpected_response_id_is_terminal(self):
        context = self.open("wrong_id")
        with self.assertRaises(InstanceProtocolError):
            context.read("README.md")
        self.assert_terminal(context)

    def test_engine_version_mismatch_is_refused_and_cleaned(self):
        with self.assertRaises(InstanceProtocolError):
            self.open("ok", engine_version="3.10.5")
        leader = int(self.published("leader.pid"))
        descendant = int(self.published("descendant.pid"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and (_alive(descendant) or _group_members(leader)):
            time.sleep(0.01)
        self.assertEqual(_group_members(leader), [])
        self.assertFalse(_alive(descendant))
        self.assertFalse(os.path.exists(self.published("policy.path")))

    def test_failed_cleanup_is_stored_and_shared_with_every_caller(self):
        context = self.open("stall", timeout=30.0)
        self.published("leader.pid")
        outcomes = []

        def shut_down(method):
            started = time.monotonic()
            try:
                method()
                outcomes.append((None, time.monotonic() - started))
            except BaseException as error:  # recorded for the assertion
                outcomes.append((error, time.monotonic() - started))

        # The group kill "fails" without signalling anything: the cleanup must
        # be reported as failed. The leader is then killed by the fallback.
        with mock.patch.object(
            AgentContext, "_kill_posix_tree", return_value=PermissionError("denied")
        ):
            owner = threading.Thread(target=shut_down, args=(context.cancel,))
            owner.start()
            time.sleep(0.2)
            # Parallel close while the owner is still cleaning up.
            shut_down(context.close)
            owner.join(timeout=10)
            self.assertFalse(owner.is_alive())
        parallel_error, parallel_seconds = outcomes[0]
        self.assertIsInstance(parallel_error, InstanceExecutionError)
        self.assertGreater(
            parallel_seconds, 1.0, "the parallel close returned before the owner finished"
        )
        self.assertLess(parallel_seconds, 8.0)
        self.assertIsInstance(outcomes[1][0], InstanceExecutionError)
        # Stored: every later cancel and close reports the same failure, never success.
        for method in (context.cancel, context.close, context.cancel):
            with self.assertRaises(InstanceExecutionError) as raised:
                method()
            self.assertIs(raised.exception, outcomes[1][0])
        with self.assertRaises(InstanceCrashed):
            context.read("README.md")

    def test_waiter_is_bounded_while_the_owner_is_still_cleaning_up(self):
        context = self.open("stall", timeout=30.0)
        self.published("leader.pid")
        original = AgentContext._kill_posix_tree

        def slow_kill(pid):
            time.sleep(1.5)
            return original(pid)

        owner_outcome = []
        with mock.patch.object(agent_module, "_CLEANUP_WAIT_SECONDS", 0.3), mock.patch.object(
            AgentContext, "_kill_posix_tree", side_effect=slow_kill
        ):
            owner = threading.Thread(
                target=lambda: owner_outcome.append(context.cancel())
            )
            owner.start()
            time.sleep(0.2)
            started = time.monotonic()
            with self.assertRaises(InstanceExecutionError) as pending:
                context.close()
            waited = time.monotonic() - started
            owner.join(timeout=10)
        self.assertIn("still pending", str(pending.exception))
        self.assertLess(waited, 1.2, "the waiter was not bounded by its own budget")
        self.assertEqual(owner_outcome, [None])
        # The owner finished successfully afterwards: now success is confirmed.
        context.cancel()
        self.assert_terminal(context)

    def test_engine_that_cannot_be_reaped_is_a_bounded_reported_failure(self):
        context = self.open("stall", timeout=30.0)
        leader = int(self.published("leader.pid"))
        # Neither the tree kill nor the leader kill signals anything: the
        # closest a test can get to a child that survives termination.
        started = time.monotonic()
        with mock.patch.object(agent_module, "_REAP_SECONDS", 0.3), mock.patch.object(
            AgentContext, "_kill_posix_tree", return_value=None
        ), mock.patch.object(AgentContext, "_resend_group_kill", lambda *_: None), mock.patch.object(
            subprocess.Popen, "kill", lambda self: None
        ):
            with self.assertRaises(InstanceExecutionError) as first:
                context.cancel()
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(_alive(leader), "the stand-in was expected to survive")
        for method in (context.cancel, context.close):
            with self.assertRaises(InstanceExecutionError) as again:
                method()
            self.assertIs(again.exception, first.exception)
        # The survivor is this test's own child: end and reap it.
        os.killpg(leader, 9)
        context._process.wait(timeout=5)
        context._close_pipes()
        self.assertEqual(_group_members(leader), [])

    # --- Group ownership at the last signal -------------------------------
    # The Engine's group is addressed by the leader's pid, which is this
    # context's own only while the leader is unreaped. Every signal the SDK
    # sends is recorded together with that fact; nothing here depends on a
    # foreign process being hit or spared.

    def record_signals(self, context):
        """Patches os.kill and os.killpg; returns the list the records go to."""
        records = []
        real_kill, real_killpg = os.kill, os.killpg
        process = context._process

        def unreaped():
            in_table = subprocess.run(["ps", "-o", "stat=", "-p", str(process.pid)],
                                      stdout=subprocess.PIPE).stdout.strip() != b""
            return process.returncode is None and in_table

        def kill(pid, sig):
            if sig != 0:
                records.append(("kill", pid, unreaped()))
            return real_kill(pid, sig)

        def killpg(pgid, sig):
            records.append(("killpg", pgid, unreaped()))
            return real_killpg(pgid, sig)

        for name, replacement in (("kill", kill), ("killpg", killpg)):
            patcher = mock.patch.object(agent_module.os, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        return records

    def test_ownership_every_signal_is_sent_while_the_leader_is_unreaped(self):
        context = self.open("stall", timeout=30.0)
        leader = int(self.published("leader.pid"))
        records = self.record_signals(context)
        context.cancel()
        self.assertTrue(any(kind == "killpg" for kind, _, _ in records), records)
        for kind, target, unreaped in records:
            self.assertEqual(target, leader, "a signal went to another number")
            self.assertTrue(unreaped, "%s was sent after the leader had been reaped" % kind)
        sent = len(records)
        self.assertIsNotNone(context._process.returncode)
        for method in (context.cancel, context.close, context.cancel):
            method()
        self.assertEqual(len(records), sent, "a signal was sent after the reap")
        self.assert_terminal(context)

    def test_ownership_a_reaped_leader_is_never_signalled(self):
        context = self.open("stall", timeout=30.0)
        leader = int(self.published("leader.pid"))
        descendant = int(self.published("descendant.pid"))
        # Devalue the ownership: the leader ends and is reaped behind the SDK's
        # back. Its number may now belong to anyone.
        os.kill(leader, 9)
        context._process.wait(timeout=5)
        records = self.record_signals(context)
        context.cancel()
        context.close()
        self.assertEqual(records, [], "a reaped leader's number was signalled")
        # The descendant was in a group the SDK may no longer address: it is
        # left alone. It is this test's own process and is ended here.
        self.assertTrue(_alive(descendant))
        os.kill(descendant, 9)

    def test_ownership_request_path_never_reaps_before_cleanup(self):
        context = self.open("exit", timeout=5.0)
        leader = int(self.published("leader.pid"))
        with self.assertRaises(InstanceCrashed):
            context.read("README.md")  # the stand-in exits on this request
        # Give the exit time to happen, then ask again: the request path must
        # notice a dead Engine without reaping it.
        fresh = self.open("exit", timeout=5.0)
        second = int(self.published("leader.pid"))
        os.kill(second, 9)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not fresh._engine_exited() and hasattr(os, "waitid"):
            time.sleep(0.01)
        self.assertIsNone(fresh._process.returncode, "the exit check reaped the leader")
        records = self.record_signals(fresh)
        with self.assertRaises(InstanceCrashed):
            fresh.read("README.md")
        for kind, target, unreaped in records:
            self.assertEqual(target, second)
            self.assertTrue(unreaped, "%s was sent after the leader had been reaped" % kind)
        self.assertTrue(records, "the exited leader's group was not ended")
        self.assertNotEqual(leader, second)
        self.assert_terminal(fresh)

    def test_ownership_leader_exits_first_with_a_pipe_holding_child(self):
        context = self.open("exit_holder", timeout=0.8)
        leader = int(self.published("leader.pid"))
        records = self.record_signals(context)
        started = time.monotonic()
        with self.assertRaises((InstanceTimeout, InstanceCrashed)):
            context.read("README.md")
        self.assertLess(time.monotonic() - started, 5.0)
        holder = int(self.published("holder.pid"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _alive(holder):
            time.sleep(0.01)
        self.assertFalse(_alive(holder), "the pipe-holding child survived")
        self.assertTrue(records)
        for kind, target, unreaped in records:
            self.assertEqual(target, leader)
            self.assertTrue(unreaped, "%s was sent after the leader had been reaped" % kind)
        self.assert_terminal(context)

    def test_ownership_one_shot_leader_exits_first_with_a_pipe_holding_child(self):
        script = os.path.join(self.root, "one-shot-holder.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(
                "#!" + sys.executable + "\n"
                "import os, subprocess, sys\n"
                "holder = subprocess.Popen([sys.executable, '-c', "
                "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], "
                "stdin=subprocess.DEVNULL)\n"
                "for name, value in (('leader.pid', os.getpid()), ('holder.pid', holder.pid)):\n"
                "    path = os.path.join(%r, name)\n"
                "    open(path + '.tmp', 'w').write(str(value))\n"
                "    os.replace(path + '.tmp', path)\n"
                "os._exit(0)\n" % self.root
            )
        os.chmod(script, 0o700)
        request = os.path.join(self.root, "request.json")
        Path(request).write_text("{}")
        client = engine_module.SubprocessInstanceClient(script, timeout=0.6)
        records = []
        real_killpg = os.killpg

        def killpg(pgid, sig):
            in_table = subprocess.run(["ps", "-o", "stat=", "-p", str(pgid)],
                                      stdout=subprocess.PIPE).stdout.strip() != b""
            records.append((pgid, in_table))
            return real_killpg(pgid, sig)

        started = time.monotonic()
        with mock.patch.object(os, "killpg", killpg):
            with self.assertRaises(InstanceTimeout):
                client._run("context-view", self.root, request)
        self.assertLess(time.monotonic() - started, 5.0)
        leader = int(self.published("leader.pid"))
        holder = int(self.published("holder.pid"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _alive(holder):
            time.sleep(0.01)
        self.assertFalse(_alive(holder), "the pipe-holding child survived")
        self.assertTrue(records)
        for pgid, unreaped in records:
            self.assertEqual(pgid, leader)
            self.assertTrue(unreaped, "the group was signalled after the leader had been reaped")

    def test_ownership_one_shot_never_signals_a_reaped_engine(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        process.wait(timeout=10)
        with mock.patch.object(os, "killpg") as killpg, mock.patch.object(os, "kill") as kill:
            engine_module._kill_and_reap(process)
        killpg.assert_not_called()
        kill.assert_not_called()

    def test_windows_tree_kill_is_bounded_and_its_result_checked(self):
        class Helper:
            """Stands in for the taskkill process; records how it was waited for."""

            def __init__(self, statuses):
                self.statuses = list(statuses)  # one per wait(): a status or "hang"
                self.waits, self.killed = [], False

            def wait(self, timeout=None):
                self.waits.append(timeout)
                status = self.statuses.pop(0)
                if status == "hang":
                    raise subprocess.TimeoutExpired("taskkill", timeout)
                return status

            def kill(self):
                self.killed = True

        budget, reap = agent_module._TASKKILL_SECONDS, agent_module._REAP_SECONDS
        for statuses, expected, waits, killed in (
            ([0], None, [budget], False),
            ([128], None, [budget], False),
            ([1], OSError, [budget], False),
            # Overran its budget: killed, reaped within a budget, reported.
            (["hang", 0], subprocess.TimeoutExpired, [budget, reap], True),
            # Could not even be ended: still bounded, still reported.
            (["hang", "hang"], subprocess.TimeoutExpired, [budget, reap], True),
        ):
            with self.subTest(statuses=statuses):
                helper = Helper(statuses)
                with mock.patch.object(agent_module.subprocess, "Popen", return_value=helper):
                    error = AgentContext._kill_windows_tree(4242)
                self.assertEqual((helper.waits, helper.killed), (waits, killed))
                self.assertNotIn(None, helper.waits, "the helper was waited for without a bound")
                if expected is None:
                    self.assertIsNone(error)
                else:
                    self.assertIsInstance(error, expected)
        with mock.patch.object(agent_module.subprocess, "Popen", side_effect=FileNotFoundError("taskkill")):
            self.assertIsInstance(AgentContext._kill_windows_tree(4242), OSError)

    def one_shot(self, timeout):
        """A one-shot Engine client on a stand-in that publishes its pid and then hangs."""
        script = os.path.join(self.root, "one-shot-stand-in.py")
        pidfile = os.path.join(self.root, "leader.pid")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(
                "#!" + sys.executable + "\n"
                "import os, time\n"
                "with open(%r, 'w') as out:\n    out.write(str(os.getpid()))\n"
                "os.replace(%r, %r)\ntime.sleep(60)\n" % (pidfile + ".tmp", pidfile + ".tmp", pidfile)
            )
        os.chmod(script, 0o700)
        request = os.path.join(self.root, "request.json")
        Path(request).write_text("{}")
        client = engine_module.SubprocessInstanceClient(script, timeout=timeout)
        return client, request

    def test_one_shot_client_timeout_is_typed_and_reaps_within_its_budget(self):
        client, request = self.one_shot(0.4)
        # The client's real process, as it starts it. The stand-in's own pid
        # file is no witness here: an Engine that runs into the deadline before
        # its first line is still an Engine the client has to end and reap.
        real_popen = subprocess.Popen
        engines = []

        def start(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            engines.append(process)
            return process

        started = time.monotonic()
        with mock.patch.object(subprocess, "Popen", start):
            with self.assertRaises(InstanceTimeout):
                client._run("context-view", self.root, request)
        self.assertLess(time.monotonic() - started, 4.0)
        self.assertEqual(len(engines), 1)
        engine = engines[0]
        # Reaped by the client already: nothing here waits for it.
        self.assertIsNotNone(engine.returncode)
        self.assertFalse(_alive(engine.pid))
        pidfile = os.path.join(self.root, "leader.pid")
        if os.path.exists(pidfile):
            self.assertEqual(int(Path(pidfile).read_text()), engine.pid)

    def test_one_shot_client_that_cannot_reap_its_engine_reports_it_bounded(self):
        client, request = self.one_shot(0.4)
        started = time.monotonic()
        # Neither kill signals anything: the stand-in really keeps running.
        # The client's own process object is kept, so that this test can reap
        # what the client, by design here, had to give up on.
        abandoned = []
        with mock.patch.object(engine_module, "_REAP_SECONDS", 0.3), mock.patch.object(
            subprocess.Popen, "kill", lambda self: abandoned.append(self)
        ), mock.patch.object(os, "killpg", lambda *_: None):
            with self.assertRaises(InstanceExecutionError) as failure:
                client._run("context-view", self.root, request)
        self.assertIn("could not be reaped within its budget", str(failure.exception))
        self.assertIsInstance(failure.exception.__context__, (InstanceTimeout, subprocess.TimeoutExpired))
        self.assertLess(time.monotonic() - started, 4.0)
        leader = int(self.published("leader.pid"))
        self.assertTrue(_alive(leader), "the stand-in was expected to survive")
        os.kill(leader, 9)  # this test's own child
        # Reaped through the object that started it: otherwise it stays an
        # unreaped child and that object is collected as "still running".
        self.assertTrue(abandoned, "the client never tried to end its Engine")
        self.assertEqual({process.pid for process in abandoned}, {leader})
        self.assertEqual(abandoned[0].wait(timeout=5), -9)

    def test_successful_shutdown_is_shared_and_happens_once(self):
        context = self.open("stall", timeout=30.0)
        self.published("leader.pid")
        kills = []
        original = AgentContext._kill_posix_tree

        def counting(pid):
            kills.append(pid)
            return original(pid)

        failures = []

        def shut_down(method):
            try:
                method()
            except BaseException as error:  # recorded for the assertion
                failures.append(error)

        with mock.patch.object(AgentContext, "_kill_posix_tree", side_effect=counting):
            callers = [
                threading.Thread(
                    target=shut_down,
                    args=(context.cancel if index % 2 == 0 else context.close,),
                )
                for index in range(8)
            ]
            for caller in callers:
                caller.start()
            for caller in callers:
                caller.join(timeout=10)
                self.assertFalse(caller.is_alive())
            for method in (context.cancel, context.close, context.cancel):
                method()
        self.assertEqual(failures, [])
        self.assertEqual(len(kills), 1, "the process tree was terminated more than once")
        self.assert_terminal(context)

    def test_explicit_reconnect_starts_a_new_process_and_never_replays(self):
        context = self.open("ok")
        first_leader = int(self.published("leader.pid"))
        context.cancel()
        with self.assertRaises(InstanceCrashed):
            context.read("README.md")
        os.unlink(os.path.join(self.root, "leader.pid"))
        replacement = context.reconnect()
        self.contexts.append(replacement)
        self.assertNotEqual(int(self.published("leader.pid")), first_leader)
        self.assertEqual(replacement.metrics.tool_calls, 0)
        self.assertEqual(replacement.read("README.md").text, "ok")
        replacement.close()


if __name__ == "__main__":
    unittest.main()
