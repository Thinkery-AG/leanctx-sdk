# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""SDK 1.3: Instance names are primary; Engine names stay deprecated aliases until 2.0."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import leanctx_sdk  # noqa: E402
from leanctx_sdk import _deprecation  # noqa: E402
import leanctx_sdk.errors as errors  # noqa: E402
import leanctx_sdk.preview as preview  # noqa: E402


ROOT_ALIASES = {
    "EngineClient": "InstanceClient",
    "EngineContextClient": "InstanceContextClient",
    "EngineContextReadResult": "InstanceContextReadResult",
    "EngineCrashed": "InstanceCrashed",
    "EngineError": "InstanceError",
    "EngineExecutionError": "InstanceExecutionError",
    "EnginePlanningClient": "InstancePlanningClient",
    "EnginePlanningRequest": "InstancePlanningRequest",
    "EngineProtocolError": "InstanceProtocolError",
    "EngineRejected": "InstanceRejected",
    "EngineSource": "InstanceSource",
    "EngineStatus": "InstanceStatus",
    "EngineTimeout": "InstanceTimeout",
    "EngineUnavailable": "InstanceUnavailable",
    "EnterpriseEngineClient": "EnterpriseInstanceClient",
    "SubprocessEngineClient": "SubprocessInstanceClient",
    "UnsupportedEngineError": "UnsupportedInstanceError",
}

MODULE_ALIASES = {
    "leanctx_sdk.errors": [
        "EngineError", "EngineUnavailable", "EngineProtocolError", "EngineExecutionError",
        "EngineTimeout", "EngineCrashed", "EngineRejected", "UnsupportedEngineError",
        "EngineUnavailableError", "EngineTimeoutError", "EngineProtocolErrorError",
        "EngineRejectedError", "EngineExecutionErrorError",
    ],
    "leanctx_sdk.protocol": ["EngineStatus"],
    "leanctx_sdk.planning": ["EnginePlanningRequest", "EngineSource"],
    "leanctx_sdk.instance": ["EngineClient", "EnginePlanningClient", "SubprocessEngineClient"],
    "leanctx_sdk.enterprise_instance": [
        "EnterpriseEngineClient", "EngineContextClient", "EngineContextReadResult",
    ],
    "leanctx_sdk.checkpoint_package": ["LocalCheckpointPackageEngine"],
    "leanctx_sdk.preview": ["LocalCheckpointPackageEngine"],
}


def _deprecated(test, call):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = call()
    messages = [str(w.message) for w in caught if w.category is DeprecationWarning]
    test.assertTrue(messages, "expected a DeprecationWarning")
    test.assertTrue(any("2.0" in m for m in messages))
    return value


class InstanceNamesTests(unittest.TestCase):
    def test_root_exports_only_instance_names(self):
        for old, new in ROOT_ALIASES.items():
            self.assertIn(new, leanctx_sdk.__all__)
            self.assertNotIn(old, leanctx_sdk.__all__)
        self.assertEqual(leanctx_sdk.__version__, "1.3.0")

    def test_every_root_engine_name_is_the_same_object_and_warns(self):
        for old, new in ROOT_ALIASES.items():
            value = _deprecated(self, lambda: getattr(leanctx_sdk, old))
            self.assertIs(value, getattr(leanctx_sdk, new), old)

    def test_from_import_of_old_names_still_works(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from leanctx_sdk import EngineUnavailable, SubprocessEngineClient  # noqa: F401
        self.assertIs(EngineUnavailable, leanctx_sdk.InstanceUnavailable)
        self.assertIs(SubprocessEngineClient, leanctx_sdk.SubprocessInstanceClient)

    def test_every_module_engine_name_resolves_to_its_instance_name(self):
        for module_name, names in MODULE_ALIASES.items():
            module = importlib.import_module(module_name)
            for old in names:
                new = _deprecation.DEPRECATED_NAMES[old]
                value = _deprecated(self, lambda: getattr(module, old))
                self.assertIs(value, getattr(module, new), f"{module_name}.{old}")

    def test_unknown_names_still_raise_attribute_error(self):
        for module in (leanctx_sdk, errors, preview):
            with self.assertRaises(AttributeError):
                getattr(module, "EngineDoesNotExist")

    def test_old_exception_names_catch_what_the_sdk_raises(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pairs = [
                (errors.EngineUnavailable, errors.InstanceUnavailable),
                (errors.EngineTimeout, errors.InstanceTimeout),
                (errors.EngineProtocolError, errors.CompatibilityError),
                (errors.UnsupportedEngineError, errors.UnsupportedInstanceError),
                (errors.EngineRejected, errors.PolicyAdmissionError),
                (errors.EngineExecutionError, errors.SourceUnavailableError),
                (errors.EngineCrashed, errors.InstanceCrashed),
                (errors.EngineError, errors.AgentPermissionError),
            ]
        for old, raised in pairs:
            with self.assertRaises(old):
                raise raised("x")
        self.assertTrue(issubclass(errors.InstanceUnavailable, errors.InstanceError))

    def test_stable_error_codes_are_unchanged(self):
        self.assertEqual(errors.InstanceUnavailable.code, "engine_unavailable")
        self.assertEqual(errors.UnsupportedInstanceError.code, "unsupported_engine")

    def test_deprecated_modules_reexport_the_instance_modules(self):
        for old, new, names in (
            ("leanctx_sdk.engine", "leanctx_sdk.instance", ["SubprocessEngineClient"]),
            ("leanctx_sdk.enterprise_engine", "leanctx_sdk.enterprise_instance",
             ["EnterpriseEngineClient"]),
        ):
            sys.modules.pop(old, None)
            module = _deprecated(self, lambda: importlib.import_module(old))
            target = importlib.import_module(new)
            for name in names:
                value = _deprecated(self, lambda: getattr(module, name))
                self.assertIs(value, getattr(target, _deprecation.DEPRECATED_NAMES[name]))


class RenamedKeywordTests(unittest.TestCase):
    def test_subprocess_client_accepts_engine_binary_with_warning(self):
        client = _deprecated(
            self, lambda: leanctx_sdk.SubprocessInstanceClient(engine_binary="/x/lean-ctx")
        )
        self.assertEqual(client.instance_binary, "/x/lean-ctx")
        self.assertEqual(_deprecated(self, lambda: client.engine_binary), "/x/lean-ctx")
        new = leanctx_sdk.SubprocessInstanceClient(instance_binary="/y")
        self.assertEqual(new.instance_binary, "/y")
        positional = leanctx_sdk.SubprocessInstanceClient("/z")
        self.assertEqual(positional.instance_binary, "/z")

    def test_both_spellings_at_once_are_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with self.assertRaises(TypeError):
                leanctx_sdk.SubprocessInstanceClient(instance_binary="/a", engine_binary="/b")

    def test_context_session_accepts_engine_keyword_with_warning(self):
        client = leanctx_sdk.SubprocessInstanceClient("/x")
        session = _deprecated(self, lambda: leanctx_sdk.ContextSession("inspect", engine=client))
        self.assertIs(session._engine, client)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            fresh = leanctx_sdk.ContextSession("inspect", instance=client)
        self.assertIs(fresh._engine, client)

    def test_agent_context_accepts_engine_binary_with_warning(self):
        with tempfile.TemporaryDirectory() as root:
            missing = os.path.join(root, "absent-lean-ctx")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with self.assertRaises(leanctx_sdk.InstanceUnavailable) as failure:
                    leanctx_sdk.AgentContext(root, engine_binary=missing)
            self.assertIn("instance", str(failure.exception))
            self.assertTrue(any(w.category is DeprecationWarning for w in caught))
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                with self.assertRaises(leanctx_sdk.InstanceUnavailable):
                    leanctx_sdk.AgentContext(root, instance_binary=missing)

    def test_checkpoint_package_functions_accept_engine_keyword(self):
        from leanctx_sdk import checkpoint_package

        sentinel = object()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = _deprecation.renamed_kwarg("engine", sentinel, "instance", None, None)
        self.assertIs(value, sentinel)
        self.assertTrue(caught)
        import inspect

        for fn in (
            checkpoint_package.seal_checkpoint_package,
            checkpoint_package.seed_workspace_from_package,
            checkpoint_package.migrate_snapshot_v1,
        ):
            params = inspect.signature(fn).parameters
            self.assertIn("instance", params)
            self.assertIn("engine", params)


class EnvironmentVariableTests(unittest.TestCase):
    def setUp(self):
        _deprecation._ENV_WARNED.clear()

    def test_new_variable_wins_without_warning(self):
        env = {"LEANCTX_INSTANCE_BINARY": "/new", "LEANCTX_ENGINE_BINARY": "/old"}
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            self.assertEqual(_deprecation.instance_env("LEANCTX_INSTANCE_BINARY", env), "/new")

    def test_old_variable_is_a_fallback_warned_once(self):
        for new, old in _deprecation.DEPRECATED_ENV.items():
            env = {old: "/old"}
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.assertEqual(_deprecation.instance_env(new, env), "/old")
                self.assertEqual(_deprecation.instance_env(new, env), "/old")
            deprecations = [w for w in caught if w.category is DeprecationWarning]
            self.assertEqual(len(deprecations), 1, old)
            self.assertIn(new, str(deprecations[0].message))

    def test_unset_returns_none(self):
        self.assertIsNone(_deprecation.instance_env("LEANCTX_INSTANCE_BIN", {}))

    def test_os_environ_is_the_default(self):
        with mock.patch.dict(os.environ, {"LEANCTX_INSTANCE_BIN": "/env"}):
            self.assertEqual(_deprecation.instance_env("LEANCTX_INSTANCE_BIN"), "/env")


if __name__ == "__main__":
    unittest.main()
