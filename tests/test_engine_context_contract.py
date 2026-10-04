# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Shared wire fixture compatibility; synthetic metadata is not a receipt proof."""
import copy
import json
from pathlib import Path
import unittest

from test_engine_context_read import _client, _reply_factory
from test_enterprise_engine import _server

from leanctx_sdk.errors import EngineProtocolError


class EngineContextContractTests(unittest.TestCase):
    def test_shared_guarded_read_fixture(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / "contracts/guarded-context-read-v1.json").read_text())
        fixture = json.loads((root / contract["fixture"]).read_text())
        request = fixture["request"]
        response = fixture["response"]
        with _server(_reply_factory(200, json.dumps(response).encode())) as server:
            result = _client(server).context_read(request["arguments"]["path"])
        self.assertEqual(json.loads(server.bodies[0]), request)
        self.assertEqual(result.raw_response, response)
        self.assertEqual(result.text, response["result"]["content"][0]["text"])
        for override in fixture["invalid_receipt_overrides"]:
            with self.subTest(override=override):
                invalid = copy.deepcopy(response)
                invalid["result"]["_meta"]["canonical_receipt"].update(override)
                with _server(_reply_factory(200, json.dumps(invalid).encode())) as server:
                    with self.assertRaises(EngineProtocolError):
                        _client(server).context_read(request["arguments"]["path"])


if __name__ == "__main__":
    unittest.main()
