"""Session containment (Q13): what the SDK asks of the Engine binary and reports.

The stand-in Engine is a script run by this interpreter. On its own it has no
session starter, so the SDK must report reduced containment. With a real
Engine binary (``LEANCTX_INSTANCE_BIN``) the stand-in is started through that
binary's own session starter — the process an SDK gets from a real Engine —
and the Engine's containment itself is exercised.
"""

import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest

from leanctx_sdk import AgentContext, InstanceExecutionError
from leanctx_sdk._deprecation import instance_env as _instance_env
from leanctx_sdk.instance import SubprocessInstanceClient

_ENGINE_BIN = _instance_env("LEANCTX_INSTANCE_BIN")

_ACK = b'{"leanctx_session_containment":1}\n'
_PAYLOAD = b'{"payload": "\xc3\xa9", "n": 1}\n'

# A one-shot stand-in: optionally the acknowledgement, then fixed bytes, then
# the given exit code.
_ONE_SHOT = r'''
import os, sys
if {ack!r}:
    os.write(1, {ack_bytes!r})
os.write(1, {payload!r})
if {code!r}:
    os.write(2, b"engine: nonzero_exit\n")
sys.exit({code!r})
'''

_STAND_IN = r'''
import json, os, subprocess, sys

MODE = {mode!r}
ROOT = {root!r}
READ = ["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"]


def send(value):
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def publish(name, value):
    path = os.path.join(ROOT, name)
    with open(path + ".tmp", "w") as handle:
        handle.write(str(value))
    os.replace(path + ".tmp", path)


publish("containment.env", os.environ.get("LEAN_CTX_SESSION_CONTAINMENT", "<absent>"))
if MODE == "ack":
    # What a session starter writes before the Engine exists.
    sys.stdout.write('{{"leanctx_session_containment":1}}\n')
    sys.stdout.flush()
hello = json.loads(sys.stdin.readline())
# Ignores SIGTERM and holds this process's output: only a group SIGKILL ends it.
descendant = subprocess.Popen(
    [sys.executable, "-c",
     "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
    stdin=subprocess.DEVNULL,
)
publish("descendant.pid", descendant.pid)
send(dict(id=hello["id"], ok=True, result=dict(
    agent_tools_interface_version="1.0.0", allow_exec=False, allow_write=False,
    capabilities=READ, engine_version="3.11.0", schema_version=1,
    transport_version=1)))
if MODE == "exit":
    os._exit(0)
for line in sys.stdin:
    request = json.loads(line)
    if request["op"] == "close":
        send(dict(id=request["id"], ok=True, result=dict()))
        break
'''


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _gone(pid, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.01)
    return False


class SessionContainmentTests(unittest.TestCase):
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
        path = os.path.join(self.root, "descendant.pid")
        if os.path.exists(path):
            try:
                os.kill(int(Path(path).read_text()), 9)
            except (ProcessLookupError, ValueError):
                pass
        self.directory.cleanup()

    def stand_in(self, mode):
        script = os.path.join(self.root, "stand-in-engine.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write("#!" + sys.executable + "\n")
            handle.write(_STAND_IN.format(mode=mode, root=self.root))
        os.chmod(script, 0o700)
        return script

    def through_session_starter(self, stand_in):
        wrapper = os.path.join(self.root, "contained-engine")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\nexec "
                + shlex.quote(os.path.realpath(_ENGINE_BIN))
                + " __session-starter --ack --watch-parent -- "
                + shlex.quote(stand_in)
                + ' "$@"\n'
            )
        os.chmod(wrapper, 0o700)
        return wrapper

    def open(self, binary):
        context = AgentContext(self.root, instance_binary=binary, timeout=5.0)
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

    @unittest.skipIf(os.name == "nt", "session containment is requested on POSIX only")
    def test_an_engine_without_the_starter_is_reported_as_unavailable(self):
        context = self.open(self.stand_in("serve"))
        # The request reached the binary; it has no starter and says nothing.
        self.assertEqual(self.published("containment.env"), "1")
        self.assertEqual(context.session_containment, "unavailable")
        context.close()

    @unittest.skipUnless(
        _ENGINE_BIN and os.name != "nt", "needs a real LeanCTX instance binary (LEANCTX_INSTANCE_BIN)"
    )
    def test_the_starter_acknowledges_and_hides_its_request_from_the_engine(self):
        context = self.open(self.through_session_starter(self.stand_in("serve")))
        self.assertEqual(context.session_containment, "engine-starter")
        self.assertEqual(self.published("containment.env"), "<absent>")
        context.close()

    @unittest.skipUnless(
        _ENGINE_BIN and os.name != "nt", "needs a real LeanCTX instance binary (LEANCTX_INSTANCE_BIN)"
    )
    def test_the_starter_ends_what_an_engine_that_exits_first_leaves_behind(self):
        context = self.open(self.through_session_starter(self.stand_in("exit")))
        descendant = int(self.published("descendant.pid"))
        # Nothing is asked of the SDK: the starter ends the Engine's group
        # before it exits itself.
        self.assertTrue(_gone(descendant), "the descendant outlived its Engine")
        self.assertEqual(context.session_containment, "engine-starter")

    @unittest.skipUnless(
        _ENGINE_BIN and os.name != "nt", "needs a real LeanCTX instance binary (LEANCTX_INSTANCE_BIN)"
    )
    def test_ending_the_starter_ends_the_engines_tree(self):
        context = self.open(self.through_session_starter(self.stand_in("serve")))
        descendant = int(self.published("descendant.pid"))
        self.assertTrue(_alive(descendant))
        # The SDK ends the process it started — the starter. The guard the
        # starter attached to the Engine's group ends the Engine and the
        # SIGTERM-ignoring descendant.
        context.cancel()
        self.assertTrue(_gone(descendant), "the descendant outlived the session")


    @unittest.skipIf(os.name == "nt", "session containment is requested on POSIX only")
    def test_an_acknowledging_stand_in_is_reported_and_its_line_is_not_a_response(self):
        # Hermetic: the stand-in writes the starter's line itself. The hello
        # that follows is still accepted, so the line was not taken for a
        # response.
        context = self.open(self.stand_in("ack"))
        self.assertEqual(context.session_containment, "engine-starter")
        context.close()


@unittest.skipIf(os.name == "nt", "session containment is requested on POSIX only")
class OneShotSessionContainmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def client(self, *, ack, code=0):
        script = os.path.join(self.root, "one-shot-engine.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write("#!" + sys.executable + "\n")
            handle.write(
                _ONE_SHOT.format(ack=ack, ack_bytes=_ACK, payload=_PAYLOAD, code=code)
            )
        os.chmod(script, 0o700)
        return SubprocessInstanceClient(script, timeout=10.0)

    def run_once(self, client):
        return client._run("context-view", self.root, os.path.join(self.root, "request.json"))

    def test_without_an_acknowledgement_the_bytes_are_unchanged(self):
        client = self.client(ack=False)
        self.assertEqual(client.last_session_containment, "unavailable")
        self.assertEqual(self.run_once(client), _PAYLOAD)
        self.assertEqual(client.last_session_containment, "unavailable")

    def test_an_acknowledgement_is_stripped_and_the_payload_is_identical(self):
        client = self.client(ack=True)
        self.assertEqual(self.run_once(client), _PAYLOAD)
        self.assertEqual(client.last_session_containment, "engine-starter")

    def test_a_failed_run_still_records_the_acknowledgement(self):
        client = self.client(ack=True, code=3)
        with self.assertRaises(InstanceExecutionError):
            self.run_once(client)
        self.assertEqual(client.last_session_containment, "engine-starter")


if __name__ == "__main__":
    unittest.main()
